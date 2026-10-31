"""Run the pinned native Metal runtime under JobManager's existing reservation.

MOSS runs one CLI subprocess per take, which releases its weights on exit.
Higgs keeps one loopback audio.cpp server resident between takes; the job
manager releases it when the queue is paused. That server inherits the backend
consumer descriptor, never a per-job GPU descriptor. There is no second GPU
queue, or independent project/UI.
"""
import atexit
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from lifecycle_events import event_pipe, measure

from assets import all_errors
from config import MODEL_DIR, MODEL_SPECS, PROFILE, PROJECT_ROOT, ROOT
from inference_locks import reservation_descriptors

from performance_controls import rendered_text,applied_performance
from punctuation_pacing import moss_instructions

from backend_selection import AUDIOCPP_REVISION

NATIVE_BIN=PROJECT_ROOT/'.runtime-source/audio.cpp/build/bin'
EOC=re.compile(r'Higgs TTS generation reached max_tokens \((\d+)\) before EOC')
SERVER_READY_SECONDS=180


def server_binary():
    return Path(os.environ.get('AUDIOCPP_SERVER_BIN',str(NATIVE_BIN/'audiocpp_server')))


def eoc_error(text,log):
    limit=EOC.search(text)
    if PROFILE['backend']=='higgs' and limit:
        return RuntimeError(f'Higgs TTS generation reached max_tokens ({limit[1]}) before EOC. '
            'This take did not finish. Use a higher Audio limit in Advanced settings, or a smaller Higgs text chunk size. '
            f'Completed options are kept. Native log: {log}')
    return None


class NativeEngine:
    variant=None

    def __init__(self):
        self.server=None;self.port=None;self.server_output=None
        atexit.register(self._stop_server)

    @property
    def resident(self):return PROFILE['backend']=='higgs'

    @property
    def model(self):return True if self.server and self.server.poll() is None else None

    @property
    def release_when_paused(self):return self.model is not None

    def unload(self):
        if self.server is None:return  # CLI takes exit before their job releases capacity.
        with measure('unload','native_server',PROFILE['backend'],MODEL_SPECS[PROFILE['backend']]['repo']):self._stop_server()

    def weight(self):
        spec=MODEL_SPECS[PROFILE['backend']]
        return MODEL_DIR/next(item['path'] for item in spec['files'] if item['path'].endswith('.gguf'))

    def command(self,request,reference,transcript,destination):
        """MOSS's per-take CLI arguments. It designs a speaker, so it takes no reference."""
        binary=Path(os.environ.get('AUDIOCPP_BIN',str(NATIVE_BIN/'audiocpp_cli')))
        if not binary.is_file():raise RuntimeError('Native Metal runtime missing. Run make setup-env MODEL='+PROFILE['backend'])
        return [str(binary),'--family',PROFILE['family'],'--model',str(self.weight()),
                '--backend','metal','--task','vdes','--text',rendered_text(request,PROFILE['backend']),
                '--out',str(destination),'--metrics','--temperature',str(request.temperature),
                '--request-option','seed='+str(request.seed if request.seed<2**31 else request.seed-2**32),
                '--text-chunk-size','1000','--language',request.language,'--request-option','instruct='+moss_instructions(request),
                '--request-option','max_frames='+str(request.max_tokens)]

    def request_body(self,request,reference,transcript):
        """One Higgs take for the resident server: the clone task with a reference and its transcript."""
        chunk=request.higgs.text_chunk_size if request.higgs and request.higgs.text_chunk_size is not None else 1024
        options={'temperature':request.temperature,'text_chunk_size':chunk}
        if request.higgs:options.update(top_p=request.higgs.top_p,top_k=request.higgs.top_k)
        return {'model':PROFILE['backend'],'input':rendered_text(request,PROFILE['backend']),
                'voice_ref':{'type':'path','path':str(reference)},'reference_text':transcript,
                'seed':str(request.seed),'max_tokens':request.max_tokens,'options':options}

    def _start_server(self):
        binary=server_binary()
        if not binary.is_file():raise RuntimeError('Native Metal server missing. Run make setup-env MODEL='+PROFILE['backend'])
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        state=PROJECT_ROOT/'state';state.mkdir(parents=True,exist_ok=True)
        config=state/f'native-server-{PROFILE["backend"]}.json'
        config.write_text(json.dumps({'host':'127.0.0.1','port':port,'backend':'metal','threads':4,
            'lazy_load':False,'max_loaded_models':1,'log_request_body':False,
            'models':[{'id':PROFILE['backend'],'family':PROFILE['family'],'path':str(self.weight()),'task':'clon','mode':'offline'}]}))
        # Without --log the server prints no dialogue, references or per-step traces.
        self.server_output=state/f'native-server-{PROFILE["backend"]}.log'
        consumer=os.environ.get('VOICEGEN_CONSUMER_FD')
        with self.server_output.open('wb') as log:
            self.server=subprocess.Popen([str(binary),'--config',str(config),'--no-ui'],cwd=PROJECT_ROOT,
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,pass_fds=(int(consumer),) if consumer else ())
        self.port=port
        deadline=time.monotonic()+SERVER_READY_SECONDS
        while True:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/v1/models',timeout=2) as response:
                    if any(item.get('loaded') for item in json.load(response).get('data',[])):return
            except (OSError,ValueError):pass
            if self.server.poll() is not None or time.monotonic()>deadline:
                tail=self.server_output.read_bytes()[-2000:].decode(errors='replace')
                self._stop_server()
                raise RuntimeError(f'Native Metal server did not load {PROFILE["backend"]}: {tail.strip()[-500:]}')
            time.sleep(.1)

    def _stop_server(self):
        server,self.server=self.server,None
        if server is None:return
        if server.poll() is None:
            server.terminate()
            try:server.wait(timeout=20)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=10)

    def generate(self,request,reference,destination,on_stage=None,transcript=''):
        errors=all_errors()
        if errors:raise RuntimeError('; '.join(errors)+'. Run make download MODEL='+PROFILE['backend'])
        if self.resident:return self._generate_resident(request,reference,destination,on_stage,transcript)
        return self._generate_cli(request,reference,destination,on_stage,transcript)

    def _generate_resident(self,request,reference,destination,on_stage,transcript):
        import numpy as np
        import soundfile as sf
        spec=MODEL_SPECS[PROFILE['backend']]
        load_seconds=None
        if self.model is None:
            if on_stage:on_stage('Loading model')
            started=time.monotonic()
            self._stop_server()
            with measure('load','native_server',PROFILE['backend'],spec['repo']):self._start_server()
            load_seconds=round(time.monotonic()-started,3)
        if on_stage:on_stage('Generating speech')
        log=ROOT/'logs'/f'native-{destination.stem}.log'
        log.write_text(f'family={PROFILE["family"]}\ntask=clon\nmode=offline resident server\n'
                       f'model_load={"cold" if load_seconds is not None else "warm"}\n')
        offset=self.server_output.stat().st_size
        started=time.monotonic()
        status,headers,body=self._post(self.request_body(request,reference,transcript))
        elapsed=time.monotonic()-started
        lines=[f'http_status={status}',*(f'metrics.{key[11:].lower().replace("-","_")}={value}'
               for key,value in headers.items() if key.lower().startswith('x-audiocpp-'))]
        if status!=200:lines.append('error='+body.decode(errors='replace')[-16384:])
        with log.open('a') as stream:
            stream.write('\n'.join(lines)+'\n'+self.server_output.read_bytes()[offset:].decode(errors='replace'))
        if status!=200:
            message=body.decode(errors='replace')
            raise eoc_error(message,log) or RuntimeError(f'Native server returned {status}; inspect {log}')
        temporary=destination.with_suffix('.tmp.wav')
        try:
            data,sr=sf.read(io.BytesIO(body),dtype='float32')
            if data.ndim!=1 or not data.size or not np.isfinite(data).all() or float(np.max(np.abs(data)))<1e-5:
                raise RuntimeError('Native runtime returned invalid/silent audio')
            sf.write(temporary,data,sr,subtype='PCM_16')
            temporary.replace(destination)
        finally:temporary.unlink(missing_ok=True)
        duration=len(data)/sr
        total=elapsed+(load_seconds or 0)
        return self._metrics(request,data,sr,duration,log,dict(cold_load=load_seconds is not None,load_seconds=load_seconds,unload_seconds=None,
            generation_seconds=round(elapsed,3),total_seconds=round(total,3),
            timing_scope='resident native server request + output validation'+('; total adds server start + model load' if load_seconds is not None else ''),
            real_time_factor=round(elapsed/duration,3),peak_process_rss_gb=None))

    def _post(self,body):
        request=urllib.request.Request(f'http://127.0.0.1:{self.port}/v1/audio/speech',json.dumps(body).encode(),
                                       {'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request) as response:return response.status,dict(response.headers),response.read()
        except urllib.error.HTTPError as error:return error.code,dict(error.headers),error.read()
        except OSError as error:
            # The server is gone or unreachable; the next take starts a fresh one.
            self._stop_server()
            raise RuntimeError(f'Native Metal server stopped during generation: {error}') from None

    def _generate_cli(self,request,reference,destination,on_stage,transcript):
        import numpy as np
        import soundfile as sf
        if on_stage:on_stage('Generating speech')
        temporary=destination.with_suffix('.tmp.wav')
        log=ROOT/'logs'/f'native-{destination.stem}.log'
        args=self.command(request,reference,transcript,temporary)
        started=time.monotonic()
        lifecycle=[]
        try:
            with log.open('w') as stream:
                with event_pipe(PROFILE['backend'],MODEL_SPECS[PROFILE['backend']]['repo'],lifecycle.append) as event_fd:
                    result=subprocess.run(['/usr/bin/time','-l',*args],stdout=stream,stderr=subprocess.STDOUT,
                                          cwd=PROJECT_ROOT,check=False,pass_fds=(*reservation_descriptors(),event_fd),
                                          env={**os.environ,'VOICEGEN_LIFECYCLE_FD':str(event_fd)})
            if result.returncode:
                with log.open('rb') as stream:
                    stream.seek(max(0,log.stat().st_size-16384));tail=stream.read(16384).decode(errors='replace')
                raise eoc_error(tail,log) or RuntimeError(f'Native runtime exited {result.returncode}; inspect {log}')
            data,sr=sf.read(temporary,dtype='float32')
            if data.ndim!=1 or not data.size or not np.isfinite(data).all() or float(np.max(np.abs(data)))<1e-5:
                raise RuntimeError('Native runtime returned invalid/silent audio')
            sf.write(temporary,data,sr,subtype='PCM_16')
            temporary.replace(destination)
        finally:temporary.unlink(missing_ok=True)
        elapsed=time.monotonic()-started;duration=len(data)/sr
        measured=re.search(r'(\d+)\s+maximum resident set size',log.read_text(errors='replace'))
        durations={event['action']:event['duration_seconds'] for event in lifecycle if event['status']=='success'}
        return self._metrics(request,data,sr,duration,log,dict(
            cold_load=True,load_seconds=durations.get('load'),unload_seconds=durations.get('unload'),generation_seconds=round(elapsed,3),total_seconds=round(elapsed,3),
            timing_scope='subprocess startup + model load + synthesis + model release + output validation',real_time_factor=round(elapsed/duration,3),
            peak_process_rss_gb=round(int(measured[1])/1e9,3) if measured else None))

    def _metrics(self,request,data,sr,duration,log,timing):
        import numpy as np
        spec=MODEL_SPECS[PROFILE['backend']]
        return dict(sample_rate=sr,channels=1,format='WAV/PCM_16',duration=round(duration,3),**timing,
            model=spec['repo'],model_revision=spec['revision'],variant=PROFILE['backend'],
            backend=PROFILE['backend'],runtime={'audio.cpp':AUDIOCPP_REVISION,'backend':'Metal'},
            quantization=PROFILE['quantization'],applied_instructions=moss_instructions(request) if PROFILE['backend']=='moss' else '',
            **{PROFILE['backend']:{'native_log_path':str(log),**applied_performance(request,PROFILE['backend']),
                **({'pacing':{'strategy':'native_description_guidance','enabled':bool(request.moss and request.moss.punctuation_pauses),
                    'requested_targets':request.moss.model_dump() if request.moss else None,'exact_timing':False,'single_pass':True}} if PROFILE['backend']=='moss' else {})}},input_tokens=None,possibly_truncated=None,
            truncation_detection='Higgs errors on missing EOC; MOSS frame ceiling may truncate; audition complete text',
            waveform=[round(float(np.max(np.abs(x))),4) for x in np.array_split(data,min(200,len(data)))],extensions={})
