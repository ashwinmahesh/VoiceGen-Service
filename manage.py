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


def health(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2) as response:
            return json.load(response)
    except Exception: return None


def listeners(port):
    result = subprocess.run(['/usr/sbin/lsof','-nP','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True)
    return result.stdout.split()


def matching(state):
    return bool(state and state.get('service')==PROFILE['id'] and state.get('project')==str(PROJECT_ROOT)
                and state.get('storage')==str(ROOT))


def verified_pid(pid, port):
    if str(pid) not in listeners(port): return False
    command = subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout.strip()
    cwd = subprocess.run(['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'],capture_output=True,text=True).stdout
    executables = {sys.executable, sys._base_executable, str(Path(sys.base_prefix)/'Resources/Python.app/Contents/MacOS/Python')}
    executables |= {str(Path(executable).resolve()) for executable in executables}
    valid_executable = any(command.startswith(executable+' ') for executable in executables)
    return valid_executable and str(PROJECT_ROOT/'server.py') in command and f'n{PROJECT_ROOT}\n' in cwd


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['start','stop','status','health'])
    parser.add_argument('--port',type=int,default=DEFAULT_PORT)
    parser.add_argument('--no-llm',action='store_true')
    parser.add_argument('--backend',choices=__import__('backend_selection').BACKENDS)
    args=parser.parse_args()
    prepare_storage()
    pidfile=ROOT/'logs'/f'server-{args.port}.pid'
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
                print(f'{PROFILE["name"]} stopped.'); return
            time.sleep(.2)
        raise SystemExit('Shutdown is still in progress. Check make status.')
    if not args.no_llm:
        from prompt_assets import check_assets,check_runtime
        check_assets();check_runtime()
    if matching(state):
        if bool(state.get('prompt_expansion',{}).get('enabled'))!=(not args.no_llm):
            raise SystemExit('This server has a different prompt LLM startup mode. Stop it when idle, then start with or without NO_LLM=1.')
        print(f'Already running: http://127.0.0.1:{args.port}'); return
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try: probe.bind(('127.0.0.1',args.port))
        except OSError: raise SystemExit(f'Port {args.port} is occupied; select another PORT.')
    log=ROOT/'logs/server.log'
    from license_notices import warn_at_startup
    warn_at_startup(PROFILE['backend'])
    with log.open('a') as stream:
        process=subprocess.Popen([sys.executable,'-u',str(PROJECT_ROOT/'server.py'),'--port',str(args.port)] +
                                 (['--no-llm'] if args.no_llm else [])+
                                 (['--backend',BACKEND] if BACKEND else []),
            cwd=PROJECT_ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,start_new_session=True)
    pidfile.write_text(str(process.pid))
    for _ in range(100):
        if process.poll() is not None: raise SystemExit(f'Server exited; inspect {log}')
        if matching(health(args.port)):
            print(f'{PROFILE["name"]}: http://127.0.0.1:{args.port}\nLogs: {log}');return
        time.sleep(.25)
    raise SystemExit(f'Startup has not completed. Inspect {log} before retrying.')


if __name__=='__main__': main()
