"""Offline PyTorch adapters; called only by the existing reserved inference worker."""
import gc
import os
import random
import resource
import time
from importlib.metadata import version

from assets import all_errors
from capabilities import capabilities
from config import MODEL_DIR, MODEL_SPECS, PROFILE, ROOT


class TorchEngine:
    def __init__(self, loader=None):
        self.model=None
        self.variant=None
        self.loader=loader

    def unload(self):
        self.model=None
        self.variant=None
        gc.collect()
        # Do not import an unrelated runtime to unload a never-loaded model.
        import sys
        if 'torch' in sys.modules:
            torch=sys.modules['torch']
            if torch.backends.mps.is_available():torch.mps.empty_cache()

    def load(self):
        if self.model is not None:return
        errors=all_errors()
        if errors:raise RuntimeError('; '.join(errors)+'. Run make download MODEL='+PROFILE['backend'])
        if self.loader is not None:
            self.model=self.loader(MODEL_DIR)
        else:
            import torch
            if not torch.backends.mps.is_available():raise RuntimeError('This backend requires the tested Apple Silicon MPS runtime')
            if PROFILE['backend']=='chatterbox':
                from chatterbox.tts import ChatterboxTTS
                self.model=ChatterboxTTS.from_local(MODEL_DIR,device='mps')
            else:
                from indextts.infer_v2_5 import IndexTTS2
                # Upstream resets this at import to a cwd-relative path. Keep it external.
                os.environ['HF_HUB_CACHE']=str(ROOT/'cache/huggingface/hub')
                # Refuse upstream auxiliary auto-downloads even if its layout changes.
                from unittest.mock import patch
                with patch('indextts.utils.model_download.ensure_models_available',side_effect=RuntimeError('Missing pinned auxiliary model. Run make download MODEL=indextts')):
                    self.model=IndexTTS2(cfg_path=str(MODEL_DIR/'config.yaml'),model_dir=str(MODEL_DIR),
                        device='mps',use_bf16=False,use_cuda_kernel=False,use_deepspeed=False,
                        use_accel=False,use_torch_compile=False,use_qwen_emo=False)
        self.variant=PROFILE['backend']

    def backend_call(self,request,reference,emotion_reference=None):
        if PROFILE['backend']=='chatterbox':
            wave=self.model.generate(text=request.text,audio_prompt_path=str(reference),
                exaggeration=request.exaggeration,cfg_weight=request.cfg_weight,temperature=request.temperature)
            return self.model.sr,wave.detach().cpu().numpy().reshape(-1)
        language={'English':'en','Chinese':'zh','Japanese':'ja','Spanish':'es','Arabic':'ar'}[request.language]
        result=self.model.infer(spk_audio_prompt=str(reference),text=request.text,output_path=None,lang=language,
            emo_audio_prompt=str(emotion_reference) if emotion_reference else None,emo_vector=request.emotion_vector,
            emo_alpha=request.emotion_alpha if request.emotion_alpha is not None else 1.,
            use_emo_text=False,use_random=False,interval_silence=request.interval_silence,
            duration_factor=request.duration_factor,temperature=request.temperature,max_mel_tokens=request.max_tokens,
            max_text_tokens_per_segment=120,stream_return=False,verbose=False,text_normalization=False)
        import numpy as np
        sr,wave=result
        data=np.asarray(wave)
        if np.issubdtype(data.dtype,np.integer):data=data.astype(np.float32)/32768.
        return sr,data.reshape(-1)

    def generate(self,request,reference,destination,on_stage=None,transcript=''):
        import numpy as np
        import soundfile as sf
        import torch
        from storage import asset_path
        started=time.monotonic();cold=self.model is None
        if on_stage:on_stage('Loading model' if cold else 'Generating speech')
        self.load();loaded=time.monotonic()
        chatter=PROFILE['backend']=='chatterbox'
        encoded=self.model.tokenizer.text_to_tokens(request.text) if chatter else self.model.tokenizer.encode(request.text,allowed_special='all')
        count=int(encoded.shape[-1]) if chatter else len(encoded)
        if count>capabilities().limits['input_tokens']:raise ValueError('Input exceeds backend token limit; shorten text')
        random.seed(request.seed);np.random.seed(request.seed);torch.manual_seed(request.seed)
        emotion_reference=asset_path('references',request.emotion_reference_id) if request.emotion_reference_id else None
        if on_stage:on_stage('Generating speech')
        from punctuation_pacing import synthesize
        with torch.inference_mode():
            sr,data,pacing=synthesize(request,PROFILE['backend'],lambda part:self.backend_call(part,reference,emotion_reference),on_stage)
        data=np.asarray(data,dtype=np.float32)
        if not data.size or not np.isfinite(data).all() or float(np.max(np.abs(data)))<1e-5:
            raise RuntimeError('Model produced empty, nonfinite or silent audio')
        temporary=destination.with_suffix('.tmp.wav')
        try:
            sf.write(temporary,data,sr,subtype='PCM_16');temporary.replace(destination)
        finally:temporary.unlink(missing_ok=True)
        finished=time.monotonic();duration=len(data)/sr;spec=MODEL_SPECS[PROFILE['backend']]
        driver_memory=round(torch.mps.driver_allocated_memory()/1e9,3)
        active_memory=round(torch.mps.current_allocated_memory()/1e9,3)
        torch.mps.empty_cache()
        return dict(sample_rate=sr,channels=1,format='WAV/PCM_16',duration=round(duration,3),
            load_seconds=round(loaded-started,3),cold_load=cold,generation_seconds=round(finished-loaded,3),
            total_seconds=round(finished-started,3),real_time_factor=round((finished-loaded)/duration,3),

            peak_process_rss_gb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9,3),
            memory_scope='MPS current allocator figures and process-lifetime peak RSS; not peak system/GPU RAM',input_tokens=count,
            possibly_truncated=None,truncation_detection='not available from upstream return value; audition complete text',
            waveform=[round(float(np.max(np.abs(x))),4) for x in np.array_split(data,min(200,len(data)))],
            model=spec['repo'],model_revision=spec['revision'],variant=self.variant,backend=PROFILE['backend'],
            runtime={key:version(key) for key in ['torch','torchaudio','transformers']},quantization=PROFILE['quantization'],
            applied_instructions='',extensions={},**{PROFILE['backend']:{
                'pacing':pacing,
                'mps_driver_allocated_gb_before_cache_release':driver_memory,'mps_active_allocated_gb':active_memory,
                'emotion_reference_path':str(emotion_reference) if emotion_reference else None,
                'emotion_reference_sha256':__import__('hashlib').sha256(emotion_reference.read_bytes()).hexdigest() if emotion_reference else None,
                'text_normalization':'upstream Chatterbox punctuation normalization' if chatter else 'disabled; phoneme annotations are explicit',
                'watermarked':chatter}})
