"""Manage only a verified, idle instance of this checkout."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from config import ROOT, PROJECT_ROOT, PROFILE, DEFAULT_PORT, BACKEND, prepare_storage
from queue_config import QUEUE_DB, DEFAULT_QUEUE_PORT
from durable_queue import QueueStore
from model_readiness import ReadyModels
from backend_selection import BACKENDS
from functools import lru_cache


def health(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2) as response:
            return json.load(response)
    except Exception: return None


def listeners(port):
    result = subprocess.run(['/usr/sbin/lsof','-nP','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True)
    return result.stdout.split()


def matching(state):
    return bool(isinstance(state,dict) and state.get('service')=='voicegen-service'
                and state.get('mode')=='generator' and state.get('api_version')=='4.0.0'
                and state.get('runtime_model_switching') is True and state.get('queue_scope')=='shared'
                and state.get('project')==str(PROJECT_ROOT) and state.get('storage')==str(ROOT)
                and state.get('queue_database')==str(QUEUE_DB.resolve()))


@lru_cache(maxsize=1)
def trusted_executables():
    executables={sys.executable,sys._base_executable,str(Path(sys.base_prefix)/'Resources/Python.app/Contents/MacOS/Python')}
    # An existing generator can have been launched with a different model's env.
    for python in PROJECT_ROOT.glob('.venv*/bin/python'):
        executables.update({str(python),str(python.resolve())})
        base=python.resolve().parent.parent
        executables.add(str(base/'Resources/Python.app/Contents/MacOS/Python'))
    return executables | {str(Path(value).resolve()) for value in executables}


def verified_pid(pid, port):
    if str(pid) not in listeners(port): return False
    command = subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout.strip()
    cwd = subprocess.run(['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'],capture_output=True,text=True).stdout
    executables=trusted_executables()
    valid_executable = any(command.startswith(executable+' ') for executable in executables)
    return valid_executable and str(PROJECT_ROOT/'server.py') in command and f'n{PROJECT_ROOT}\n' in cwd


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['start','stop','status','health'])
    parser.add_argument('--port',type=int,default=DEFAULT_PORT)
    parser.add_argument('--no-llm',action='store_true')
    parser.add_argument('--model',choices=BACKENDS,help='Save the default model; every ready model remains available')
    parser.add_argument('--queue-port',type=int,default=DEFAULT_QUEUE_PORT)
    args=parser.parse_args()
    QUEUE_DB.parent.mkdir(parents=True,exist_ok=True)
    pidfile=QUEUE_DB.parent/f'server-{args.port}.pid'
    state=health(args.port)
    if args.action in ['status','health']:
        print(json.dumps(state or {'status':'offline'},indent=2))
        if args.action=='health' and (not matching(state) or not state.get('downloaded') or state.get('status')=='error'):
            raise SystemExit(1)
        return
    if args.action=='stop':
        if not matching(state): raise SystemExit('This checkout is not serving on that port; leaving the listener alone.')
        if state.get('busy'): raise SystemExit('The service is busy. Wait for it to finish.')
        if not pidfile.is_file(): raise SystemExit('No managed PID file; stop the foreground process with Ctrl-C.')
        pid=int(pidfile.read_text())
        if not verified_pid(pid,args.port): raise SystemExit('PID identity does not match this listener and checkout; leaving it alone.')
        os.kill(pid,signal.SIGTERM)
        for _ in range(100):
            if str(pid) not in listeners(args.port):
                pidfile.unlink(missing_ok=True)
                print('VoiceGen Service stopped.'); return
            time.sleep(.2)
        raise SystemExit('Shutdown is still in progress. Check make status.')
    store=QueueStore(ROOT,QUEUE_DB);store.initialize()
    catalog=ReadyModels()
    if matching(state):
        if bool(state.get('prompt_expansion',{}).get('enabled'))!=(not args.no_llm):
            raise SystemExit('This server has a different prompt LLM startup mode. Stop it when idle, then start with or without NO_LLM=1.')
        if args.model:
            catalog.require(args.model,force=True)
            store.set_setting('default_model',args.model)
        print(f'Already running: http://127.0.0.1:{args.port}');return
    ready=catalog.models(force=True)
    if not ready:raise SystemExit('No set-up voice models are ready. Run make setup or reconnect model storage.')
    default=args.model or store.default_model()
    if args.model:catalog.require(default)
    elif default not in {row['id'] for row in ready}:default=ready[0]['id']
    if not args.no_llm:
        from prompt_assets import check_assets,check_runtime
        check_assets();check_runtime()
    prepare_storage()
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try: probe.bind(('127.0.0.1',args.port))
        except OSError: raise SystemExit(f'Port {args.port} is occupied; select another PORT.')
    log=QUEUE_DB.parent/'server.log'
    from license_notices import warn_at_startup
    warn_at_startup(default)
    with log.open('a') as stream:
        process=subprocess.Popen([str(catalog.python(default)),'-u',str(PROJECT_ROOT/'server.py'),'--port',str(args.port),'--queue-port',str(args.queue_port)] +
                                 (['--no-llm'] if args.no_llm else [])+
                                 ['--model',default],
            cwd=PROJECT_ROOT,env={**os.environ,'MODEL_BACKEND':default,
                'VOICEGEN_OVERRIDE_BACKEND':os.environ.get('VOICEGEN_OVERRIDE_BACKEND',BACKEND)},
            stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,start_new_session=True)
    pidfile.write_text(str(process.pid))
    for _ in range(100):
        if process.poll() is not None: raise SystemExit(f'Server exited; inspect {log}')
        if matching(health(args.port)):
            print(f'VoiceGen Service: http://127.0.0.1:{args.port}\nLogs: {log}');return
        time.sleep(.25)
    raise SystemExit(f'Startup has not completed. Inspect {log} before retrying.')


if __name__=='__main__': main()
