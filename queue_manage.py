"""Start/reuse/stop only this checkout's independent queue CRUD service."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from queue_config import PROJECT_ROOT, QUEUE_DB, DEFAULT_QUEUE_PORT


def health(port, timeout=1):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=timeout) as response:
            return json.load(response)
    except (OSError,ValueError):
        return None


def matching(state):
    return bool(isinstance(state,dict) and state.get('service')=='voicegen-queue' and state.get('role')=='queue-crud'
                and state.get('api_version')=='4.0.0' and state.get('queue_scope')=='shared'
                and state.get('project')==str(PROJECT_ROOT) and state.get('queue_database')==str(QUEUE_DB.resolve()))


def pidfile(port):
    return QUEUE_DB.parent/f'queue-{port}.pid'


def queue_python():
    return os.environ.get('QUEUE_PYTHON') or (str(PROJECT_ROOT/'.venv-queue/bin/python')
        if (PROJECT_ROOT/'.venv-queue/bin/python').is_file() else sys.executable)


def listeners(port):
    return subprocess.run(['/usr/sbin/lsof','-nP','-t',f'-iTCP:{port}','-sTCP:LISTEN'],
                          capture_output=True,text=True).stdout.split()


def verified_pid(pid,port):
    if str(pid) not in listeners(port):return False
    command = subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout.strip()
    cwd = subprocess.run(['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'],capture_output=True,text=True).stdout
    executables = {sys.executable,sys._base_executable,queue_python(),str(Path(sys.base_prefix)/'Resources/Python.app/Contents/MacOS/Python')}
    if queue_python() != sys.executable:
        runtime = subprocess.run([queue_python(),'-c','import sys; from pathlib import Path; print(sys._base_executable); print(Path(sys.base_prefix)/"Resources/Python.app/Contents/MacOS/Python")'],
                                 capture_output=True,text=True,timeout=5)
        if runtime.returncode==0:executables.update(runtime.stdout.splitlines())
    executables |= {str(Path(value).resolve()) for value in executables}
    return (any(command.startswith(value+' ') for value in executables)
            and str(PROJECT_ROOT/'queue_server.py') in command and f'n{PROJECT_ROOT}\n' in cwd)


def check_port(port):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:probe.bind(('127.0.0.1',port))
        except OSError:
            raise RuntimeError(f'Queue port {port} is occupied by another service; leaving it alone. Set QUEUE_PORT explicitly.') from None


@contextmanager
def launch_lock():
    QUEUE_DB.parent.mkdir(parents=True,exist_ok=True)
    with QUEUE_DB.with_suffix('.launch.lock').open('a+') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        yield


def ensure_queue(port=DEFAULT_QUEUE_PORT):
    """No generator imports, model checks, recovery, inference or lifecycle coupling."""
    with launch_lock():
        state = health(port)
        if matching(state):return False
        check_port(port)
        log = QUEUE_DB.parent/'queue.log'
        with log.open('a') as stream:
            process = subprocess.Popen([queue_python(),'-u',str(PROJECT_ROOT/'queue_server.py'),'--port',str(port)],
                cwd=PROJECT_ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,start_new_session=True)
        pidfile(port).write_text(str(process.pid))
        for _ in range(100):
            if process.poll() is not None:
                if pidfile(port).read_text()==str(process.pid):pidfile(port).unlink(missing_ok=True)
                raise RuntimeError(f'Queue API exited; inspect {log}')
            if matching(health(port)):
                print(f'Queue CRUD: http://127.0.0.1:{port} (independent; make stop-queue to stop)',flush=True)
                return True
            time.sleep(.1)
        # Only our newly created process, never a pre-existing listener.
        process.terminate()
        process.wait(timeout=10)
        pidfile(port).unlink(missing_ok=True)
        raise RuntimeError(f'Queue API did not become ready; inspect {log}')


def stop_queue(port=DEFAULT_QUEUE_PORT):
    with launch_lock():
        if not matching(health(port)):
            raise RuntimeError('No matching queue CRUD service on this port; leaving any listener alone.')
        record = pidfile(port)
        try:pid = int(record.read_text())
        except (OSError,ValueError):
            raise RuntimeError('No managed queue PID; stop its foreground terminal with Ctrl-C.') from None
        if not verified_pid(pid,port):
            raise RuntimeError('Queue PID identity does not match this checkout; leaving it alone.')
        os.kill(pid,signal.SIGTERM)
        for _ in range(100):
            if str(pid) not in listeners(port):
                record.unlink(missing_ok=True)
                print('Queue CRUD stopped; persisted jobs are retained.')
                return
            time.sleep(.1)
        raise RuntimeError('Queue shutdown is still in progress.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['serve','start','stop','status'])
    parser.add_argument('--port',type=int,default=DEFAULT_QUEUE_PORT)
    args=parser.parse_args()
    try:
        if args.action=='stop':stop_queue(args.port)
        elif args.action=='status':print(json.dumps(health(args.port) or {'status':'offline'},indent=2))
        elif args.action=='start':ensure_queue(args.port)
        else:
            with launch_lock():
                if matching(health(args.port)):
                    print(f'Queue CRUD already running: http://127.0.0.1:{args.port}')
                    return
                check_port(args.port)
                pidfile(args.port).write_text(str(os.getpid()))
            os.chdir(PROJECT_ROOT)
            os.execv(sys.executable,[sys.executable,'-u',str(PROJECT_ROOT/'queue_server.py'),'--port',str(args.port)])
    except (RuntimeError,OSError) as exc:
        raise SystemExit(str(exc)) from None


if __name__=='__main__':main()
