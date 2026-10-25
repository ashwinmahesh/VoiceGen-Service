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
from directions import instructions

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
              '--backend','metal','--task','vdes' if moss else 'clon','--text',request.text,
              '--out',str(destination),'--metrics','--temperature',str(request.temperature),
              '--request-option','seed='+str(request.seed if not moss or request.seed<2**31 else request.seed-2**32),
              '--text-chunk-size','1000' if moss else '1024']
        if moss:
            args+=['--language',request.language,'--request-option','instruct='+instructions(request,None),
                   '--request-option','max_frames='+str(request.max_tokens)]
        else:
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
                                      cwd=PROJECT_ROOT,check=False)
            if result.returncode:raise RuntimeError(f'Native runtime exited {result.returncode}; inspect {log}')
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
            quantization=PROFILE['quantization'],applied_instructions=instructions(request,None) if PROFILE['backend']=='moss' else '',
            native_log_path=str(log),input_tokens=None,possibly_truncated=None,
            truncation_detection='Higgs errors on missing EOC; MOSS frame ceiling may truncate; audition complete text',
            waveform=[round(float(np.max(np.abs(x))),4) for x in np.array_split(data,min(200,len(data)))],extensions={})
