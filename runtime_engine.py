"""Serial resident backend switching; metadata/API threads never load speech weights."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
from config import PROJECT_ROOT,PROFILE,OVERRIDE_BACKEND
from inference_locks import reservation_descriptors
from backend_protocol import send,receive
from lifecycle_events import emit, measure, reason, current_reason


class RuntimeEngine:
    runtime_model_switching=True

    def __init__(self,catalog,worker_script=None):
        self.catalog=catalog
        self.worker_script=Path(worker_script or PROJECT_ROOT/'backend_worker.py')
        self.process=None;self.channel=None;self.backend=None
        self.consumer_owner=None;self.fatal_error=None
        self.loaded=False;self.variant=None;self.switching=False;self.queue_resident=False
        self.lock=threading.RLock()

    @property
    def model(self):return True if self.loaded else None

    @property
    def release_when_paused(self):
        """The loaded backend keeps its weights only while the queue is processing."""
        return bool(self.process and self.loaded and self.queue_resident and not self.switching)

    @property
    def active_model(self):return self.backend if self.process and (self.loaded or self.switching) else None

    @staticmethod
    def _live_group(group):
        result=subprocess.run(['/bin/ps','-axo','pid=,pgid=,stat='],capture_output=True,text=True,timeout=3)
        if result.returncode:raise RuntimeError('Cannot verify the owned backend process group')
        return [int(parts[0]) for line in result.stdout.splitlines()
                if len(parts:=line.split())>=3 and int(parts[1])==group and not parts[2].startswith('Z')]

    def _reap_group(self,process):
        # ps distinguishes live processes from zombies, including signal-zero EPERM.
        for sig in (signal.SIGTERM,signal.SIGKILL):
            if not self._live_group(process.pid):return
            try:os.killpg(process.pid,sig)
            except ProcessLookupError:pass
            except PermissionError:pass  # Verify state; never interpret EPERM as exit.
            end=time.monotonic()+3
            while time.monotonic()<end:
                if not self._live_group(process.pid):return
                time.sleep(.05)
        if self._live_group(process.pid):raise RuntimeError('Owned backend processes remain alive; generation is stopped')

    def _stop(self,failed=False):
        if self.process is None:return
        with measure('unload','runtime_process',self.backend or PROFILE['backend'],self.variant or self.backend or PROFILE['id'],
                     'failure_cleanup' if failed else None):
            self._stop_process(failed)

    def _stop_process(self,failed=False):
        process,channel=self.process,self.channel
        if process is None:return
        try:
            try:
                if not failed and process.poll() is None:
                    channel.settimeout(10);send(channel,{'command':'close','reason':current_reason()})
                    while True:
                        response,fds=receive(channel)
                        for fd in fds:os.close(fd)
                        if 'lifecycle' in response:emit(response['lifecycle']);continue
                        break
                if channel:channel.close()
                process.wait(timeout=10)
            except (OSError,EOFError,ValueError,subprocess.TimeoutExpired):
                self._reap_group(process)
                process.wait(timeout=3)
            self._reap_group(process)
        except Exception as error:
            self.fatal_error='Backend cleanup could not be verified: '+str(error)
            raise RuntimeError(self.fatal_error) from error
        finally:
            if channel:channel.close()
        self.process=self.channel=self.backend=None;self.loaded=False;self.variant=None;self.queue_resident=False

    def _start(self,backend,on_stage):
        if self.fatal_error:raise RuntimeError(self.fatal_error)
        if self.process and self.backend==backend and self.process.poll() is None:return
        if on_stage:on_stage('Switching model')
        with reason('model_switch'):self._stop()
        self.catalog.require(backend,force=True)
        from license_notices import warn_at_startup
        warn_at_startup(backend)
        if self.consumer_owner is None:raise RuntimeError('A backend requires the global queue consumer lease')
        with measure('load','runtime_process',backend,backend):self._launch(backend)

    def _launch(self,backend):
        owner_fd=self.consumer_owner.fileno()
        parent,child=socket.socketpair()
        environment={**os.environ,'MODEL_BACKEND':backend,'VOICEGEN_OVERRIDE_BACKEND':OVERRIDE_BACKEND}
        if backend!=OVERRIDE_BACKEND:
            for key in ('WEIGHTS_DIR','EXTENSIONS_DIR'):environment.pop(key,None)
        log_dir=PROJECT_ROOT/'state';log_dir.mkdir(parents=True,exist_ok=True)
        try:
            with (log_dir/'backend.log').open('ab') as log:
                self.process=subprocess.Popen([str(self.catalog.python(backend)),'-u',str(self.worker_script),'--fd',str(child.fileno()),'--owner-fd',str(owner_fd)],
                    cwd=PROJECT_ROOT,env=environment,pass_fds=(child.fileno(),owner_fd),stdin=subprocess.DEVNULL,
                    stdout=log,stderr=log,start_new_session=True)
            self.channel=parent;self.backend=backend
            child.close();parent.settimeout(90)
            response,fds=receive(parent)
            for fd in fds:os.close(fd)
            if response!={'ready':True}:raise RuntimeError('Backend did not complete initialization')
            parent.settimeout(None)
        except BaseException:
            child.close();parent.close();self._stop(failed=True);raise

    def generate(self,request,reference,destination,on_stage=None,transcript=''):
        with self.lock:
            self.switching=True
            try:
                self._start(PROFILE['backend'],on_stage)
                send(self.channel,dict(command='generate',request=request.model_dump(),reference=str(reference) if reference else None,
                     destination=str(destination),transcript=transcript),reservation_descriptors())
                while True:
                    response,fds=receive(self.channel)
                    for fd in fds:os.close(fd)
                    if 'lifecycle' in response:emit(response['lifecycle']);continue
                    if 'event' in response:
                        if on_stage:on_stage(response['event'])
                        continue
                    self.loaded=response.get('loaded',False);self.variant=response.get('variant')
                    self.queue_resident=response.get('release_when_paused',False)
                    if 'error' in response:raise RuntimeError(response['error'])
                    return response['result']
            except BaseException:
                self._stop(failed=True);raise
            finally:self.switching=False

    def unload(self):
        with self.lock:self._stop()
