"""Run the pinned native Metal runtime under JobManager's existing reservation.

Each job owns one subprocess which releases its weights on exit. There is no
second HTTP server, GPU queue, or independent project/UI.
"""
import os
from pathlib import Path
import re
import subprocess
import time

from assets import all_errors
from config import MODEL_DIR, MODEL_SPECS, PROFILE, PROJECT_ROOT, ROOT
from inference_locks import reservation_descriptors

from performance_controls import rendered_text,applied_performance
from punctuation_pacing import moss_instructions

from backend_selection import AUDIOCPP_REVISION


class NativeEngine:
    model=None
    variant=None

    def unload(self):
        pass  # Every generation process exits before its job releases capacity.

    def command(self,request,reference,transcript,destination):
        binary=Path(os.environ.get('AUDIOCPP_BIN',str(PROJECT_ROOT/'.runtime-source/audio.cpp/build/bin/audiocpp_cli')))
        if not binary.is_file():raise RuntimeError('Native Metal runtime missing. Run make setup-env MODEL='+PROFILE['backend'])
        spec=MODEL_SPECS[PROFILE['backend']]
        weight=next(item['path'] for item in spec['files'] if item['path'].endswith('.gguf'))
        moss=PROFILE['backend']=='moss'
        args=[str(binary),'--family',PROFILE['family'],'--model',str(MODEL_DIR/weight),
              '--backend','metal','--task','vdes' if moss else 'clon','--text',rendered_text(request,PROFILE['backend']),
              '--out',str(destination),'--metrics','--temperature',str(request.temperature),
              '--request-option','seed='+str(request.seed if not moss or request.seed<2**31 else request.seed-2**32),
              '--text-chunk-size','1000' if moss else str(request.higgs.text_chunk_size if request.higgs and request.higgs.text_chunk_size is not None else 1024)]
        if moss:
            args+=['--language',request.language,'--request-option','instruct='+moss_instructions(request),
                   '--request-option','max_frames='+str(request.max_tokens)]
        else:
            if request.higgs:
                args+=['--top-p',str(request.higgs.top_p),'--top-k',str(request.higgs.top_k)]
            args+=['--voice-ref',str(reference),'--reference-text',transcript,'--max-tokens',str(request.max_tokens)]
        return args

    def generate(self,request,reference,destination,on_stage=None,transcript=''):
        import numpy as np
        import soundfile as sf
        errors=all_errors()
        if errors:raise RuntimeError('; '.join(errors)+'. Run make download MODEL='+PROFILE['backend'])
        if on_stage:on_stage('Generating speech')
        temporary=destination.with_suffix('.tmp.wav')
        log=ROOT/'logs'/f'native-{destination.stem}.log'
        args=self.command(request,reference,transcript,temporary)
        started=time.monotonic()
        try:
            with log.open('w') as stream:
                result=subprocess.run(['/usr/bin/time','-l',*args],stdout=stream,stderr=subprocess.STDOUT,
                                      cwd=PROJECT_ROOT,check=False,pass_fds=reservation_descriptors())
            if result.returncode:
                with log.open('rb') as stream:
                    stream.seek(max(0,log.stat().st_size-16384));tail=stream.read(16384).decode(errors='replace')
                limit=re.search(r'Higgs TTS generation reached max_tokens \((\d+)\) before EOC',tail)
                if PROFILE['backend']=='higgs' and limit:
                    raise RuntimeError(f'Higgs TTS generation reached max_tokens ({limit[1]}) before EOC. '
                        'This take did not finish. Use a higher Audio limit in Advanced settings, or a smaller Higgs text chunk size. '
                        f'Completed options are kept. Native log: {log}')
                raise RuntimeError(f'Native runtime exited {result.returncode}; inspect {log}')
            data,sr=sf.read(temporary,dtype='float32')
            if data.ndim!=1 or not data.size or not np.isfinite(data).all() or float(np.max(np.abs(data)))<1e-5:
                raise RuntimeError('Native runtime returned invalid/silent audio')
            sf.write(temporary,data,sr,subtype='PCM_16')
            temporary.replace(destination)
        finally:temporary.unlink(missing_ok=True)
        elapsed=time.monotonic()-started;duration=len(data)/sr
        measured=re.search(r'(\d+)\s+maximum resident set size',log.read_text(errors='replace'))
        spec=MODEL_SPECS[PROFILE['backend']]
        return dict(sample_rate=sr,channels=1,format='WAV/PCM_16',duration=round(duration,3),
            cold_load=True,load_seconds=None,generation_seconds=round(elapsed,3),total_seconds=round(elapsed,3),
            timing_scope='subprocess startup + model load + synthesis + output validation',real_time_factor=round(elapsed/duration,3),
            peak_process_rss_gb=round(int(measured[1])/1e9,3) if measured else None,
            model=spec['repo'],model_revision=spec['revision'],variant=PROFILE['backend'],
            backend=PROFILE['backend'],runtime={'audio.cpp':AUDIOCPP_REVISION,'backend':'Metal'},
            quantization=PROFILE['quantization'],applied_instructions=moss_instructions(request) if PROFILE['backend']=='moss' else '',
            **{PROFILE['backend']:{'native_log_path':str(log),**applied_performance(request,PROFILE['backend']),
                **({'pacing':{'strategy':'native_description_guidance','enabled':bool(request.moss and request.moss.punctuation_pauses),
                    'requested_targets':request.moss.model_dump() if request.moss else None,'exact_timing':False,'single_pass':True}} if PROFILE['backend']=='moss' else {})}},input_tokens=None,possibly_truncated=None,
            truncation_detection='Higgs errors on missing EOC; MOSS frame ceiling may truncate; audition complete text',
            waveform=[round(float(np.max(np.abs(x))),4) for x in np.array_split(data,min(200,len(data)))],extensions={})
