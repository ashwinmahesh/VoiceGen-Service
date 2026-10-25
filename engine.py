"""Backend calls only; all MLX work stays on the single inference thread."""
import gc
import time
from importlib.metadata import version
from config import MODEL_DIR, MODEL_SPECS, PROFILE
from assets import verify_assets
from capabilities import capabilities
from directions import instructions, variant_for
from emotion_steering import emotion_context
from performance_controls import rendered_text,applied_performance,qwen_sampling


def create_engine():
    if PROFILE['backend'] in ('moss','higgs'):
        from native_engine import NativeEngine
        return NativeEngine()
    if PROFILE['backend'] in ('chatterbox', 'indextts'):
        from torch_engine import TorchEngine
        return TorchEngine()
    return Engine()


class Engine:
    def __init__(self, loader=None):
        self.model = None
        self.variant = None
        self.loader = loader

    def unload(self):
        self.model = None
        self.variant = None
        gc.collect()
        import mlx.core as mx
        mx.clear_cache()

    def load(self, variant):
        if self.variant == variant and self.model is not None:
            return
        self.unload()
        errors = verify_assets(variant)
        if errors:
            raise RuntimeError('; '.join(errors)+'. Run make download.')
        if self.loader is None:
            if PROFILE['backend']=='fish':
                from fish_loader import load_fish_model
                loader=load_fish_model
            else:
                from mlx_audio.tts.utils import load_model
                loader = load_model
        else:
            loader = self.loader
        from clean_env import clean_appledouble
        clean_appledouble(MODEL_DIR/MODEL_SPECS[variant]['subdir'])
        self.model = loader(str(MODEL_DIR/MODEL_SPECS[variant]['subdir']))
        if self.model.tokenizer is None or (PROFILE['backend']=='qwen3' and self.model.speech_tokenizer is None):
            self.unload()
            raise RuntimeError('Tokenizer/decoder failed to load. Run make download and check logs.')
        if variant=='custom':
            from directions import PRESETS
            expected={item['id'] for item in PRESETS}
            actual={name.lower() for name in self.model.get_supported_speakers()}
            if not expected.issubset(actual):
                self.unload();raise RuntimeError('Installed CustomVoice speaker catalog does not match pinned configuration')
        self.variant = variant

    def backend_call(self, request, reference, transcript):
        instruction_text = instructions(request,reference)
        common = dict(text=rendered_text(request,PROFILE['backend']), max_tokens=request.max_tokens)
        if PROFILE['backend']=='fish':
            from mlx_audio.utils import load_audio
            waveform=load_audio(str(reference),sample_rate=self.model.sample_rate) if reference else None
            return self.model.generate(**common,ref_audio=waveform,
                ref_text=transcript or None,temperature=request.temperature,stream=False,verbose=False,
                chunk_length=300,top_p=request.fish.top_p if request.fish else .7,top_k=request.fish.top_k if request.fish else 30)
        if PROFILE['backend'] == 'voxcpm2':
            conditioning = {'prompt_audio':str(reference),'prompt_text':transcript.rstrip()+' '} if request.clone_mode in ['continuation','combined'] else {'ref_audio':str(reference) if reference else None}
            if request.clone_mode=='combined':
                conditioning['ref_audio']=str(reference)
            return self.model.generate(**common,instruct=instruction_text or None,**conditioning,
                inference_timesteps=request.steps,cfg_value=request.guidance)
        if PROFILE['backend'] != 'qwen3':
            raise ValueError('Unsupported backend: '+PROFILE['backend'])
        common.update(temperature=request.temperature, verbose=False, stream=False)
        if request.preset_voice:
            return self.model.generate_custom_voice(**common,speaker=request.preset_voice,language=request.language,instruct=instruction_text or None)
        if reference is None:
            return self.model.generate_voice_design(**common,instruct=instruction_text,language=request.language)
        return self.model.generate(**common, ref_audio=str(reference), ref_text=transcript or None,
                                   lang_code=request.language, split_pattern='')

    def check_input_budget(self, request, reference, transcript):
        encoded = self.model.tokenizer.encode('\n'.join([rendered_text(request,PROFILE['backend']), request.description if not reference else '',
            request.emotion, request.accent,
            transcript if PROFILE['backend'] in ('qwen3','fish') or request.clone_mode in ['continuation','combined'] else '']))
        if len(encoded) > capabilities().limits['input_tokens']:
            raise ValueError('Input exceeds the documented token limit; shorten text, description, or transcript.')
        return encoded

    def generate(self, request, reference, destination, on_stage=None, transcript=''):
        import mlx.core as mx
        import numpy as np
        import soundfile as sf
        variant = variant_for(request,reference)
        started = time.monotonic()
        cold = self.variant != variant or self.model is None
        if on_stage: on_stage('Loading model' if cold else 'Generating speech')
        self.load(variant)
        loaded = time.monotonic()
        encoded = self.check_input_budget(request,reference,transcript)
        mx.random.seed(request.seed)
        mx.reset_peak_memory()
        if on_stage: on_stage('Generating speech')
        chunks, token_counts = [], []
        with emotion_context(self.model,request):
            for result in self.backend_call(request, reference, transcript):
                data = np.asarray(result.audio, dtype=np.float32).reshape(-1)
                if not np.isfinite(data).all():
                    raise RuntimeError('Model produced invalid audio; try another seed.')
                chunks.append(data)
                token_counts.append(result.token_count)
        if not chunks or not sum(x.size for x in chunks):
            raise RuntimeError('Model produced no audio; try another seed.')
        data = np.concatenate(chunks)
        if float(np.max(np.abs(data))) < 1e-5:
            raise RuntimeError('Model produced silent audio; try another seed.')
        sr = int(self.model.sample_rate)
        temp = destination.with_suffix('.tmp.wav')
        try:
            sf.write(temp, data, sr, subtype='PCM_16')
            temp.replace(destination)
        finally:
            temp.unlink(missing_ok=True)
        finished = time.monotonic()
        duration = len(data)/sr
        peaks = [round(float(np.max(np.abs(chunk))),4) for chunk in np.array_split(data, min(200,len(data))) if len(chunk)]
        return {'sample_rate':sr, 'channels':1, 'format':'WAV/PCM_16', 'duration':round(duration,3),
                'load_seconds':round(loaded-started,3), 'cold_load':cold,
                'generation_seconds':round(finished-loaded,3), 'total_seconds':round(finished-started,3),
                'real_time_factor':round((finished-loaded)/duration,3),
                'peak_memory_gb':round(mx.get_peak_memory()/1e9,3), 'input_tokens':len(encoded),
                'possibly_truncated':any(t >= request.max_tokens for t in token_counts) if PROFILE['backend'] in ('qwen3','fish')
                    else duration >= request.max_tokens*.16-.5,
                'truncation_detection':'output token count' if PROFILE['backend'] in ('qwen3','fish') else 'duration heuristic',
                'waveform':peaks, 'model':MODEL_SPECS[variant]['repo'],
                'model_revision':MODEL_SPECS[variant]['revision'], 'variant':variant,
                'runtime':{key:version(key) for key in ['mlx','mlx-audio','transformers']},
                **({PROFILE['backend']:applied_performance(request,PROFILE['backend'])} if PROFILE['backend']=='fish' else {'qwen3':qwen_sampling(self.model,request,reference,transcript)} if PROFILE['backend']=='qwen3' else {}),
                'quantization':PROFILE['quantization'],'applied_instructions':instructions(request,reference),
                'extensions':({'emotion_steering':{'emotion':request.steering_emotion,'strength':request.steering_strength,
                    'layers':[21,25],'decay':.985,'revision':__import__('config').EXTENSION_SPECS['emotion_steering']['revision'],
                    'experimental':True}} if request.steering_emotion else {})}
