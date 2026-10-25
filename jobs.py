"""Durable jobs and idempotent retry recovery; capacity belongs to the worker."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import logging
import threading
from uuid import uuid4
from config import ROOT, GPU_LOCK_FILE, PROFILE
from voice_catalog import now, atomic_json, IDENTITY
from storage import persist_artifact


class BusyError(ValueError): pass


class JobManager:
    def __init__(self, engine, root=ROOT, lock_file=GPU_LOCK_FILE):
        self.engine, self.root, self.lock_file = engine, root, lock_file
        self.executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='voice-inference')
        self.guard = threading.RLock()
        self.active = None
        self.unloading = False
        self.last_error = None
        self.stage = 'unloaded'

    def recover(self):
        for path in (self.root/'jobs').glob('*.json'):
            if not IDENTITY.fullmatch(path.stem): continue
            try:
                row = json.loads(path.read_text())
                if row['status'] in ['queued','running']:
                    row.update(status='error',stage='Interrupted by server restart',
                        error='Server stopped before this job completed. Check History before submitting a new key.',updated_at=now())
                    atomic_json(path,row)
            except (OSError,ValueError,KeyError):
                logging.exception('Cannot recover job %s',path)

    def read(self, identity):
        if not IDENTITY.fullmatch(identity): raise FileNotFoundError('Job not found')
        return json.loads((self.root/'jobs'/f'{identity}.json').read_text())

    def update(self, identity, **changes):
        with self.guard:
            row = self.read(identity)
            row.update(changes,updated_at=now())
            atomic_json(self.root/'jobs'/f'{identity}.json',row)
            return row

    def existing(self, request):
        if not request.idempotency_key: return None
        key = hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        path = self.root/'idempotency'/f'{key}.json'
        fingerprint = hashlib.sha256(request.model_dump_json(exclude={'idempotency_key'}).encode()).hexdigest()
        if path.is_file():
            saved = json.loads(path.read_text())
            if saved['fingerprint'] != fingerprint:
                added={'preset_voice':None,'accent':'','clone_mode':'reference','steering_emotion':None,'steering_strength':None}
                legacy = None
                if all(getattr(request,key)==value for key,value in added.items()):
                    legacy=hashlib.sha256(request.model_dump_json(exclude={'idempotency_key',*added}).encode()).hexdigest()
                if saved['fingerprint'] != legacy:
                    raise BusyError('This idempotency key was used with different settings')
            return self.read(saved['job'])
        return None

    def reserve(self):
        if self.active or self.unloading: raise BusyError('The model is busy. Wait for the current operation.')
        handle = self.lock_file.open('a+')
        try:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise BusyError('Another local voice studio is generating or unloading. Wait for it to finish.') from None
        return handle

    def submit(self, request, effective, reference, transcript, destination, reference_info=None):
        with self.guard:
            existing = self.existing(request)
            if existing: return existing
            handle = self.reserve()
            identity = uuid4().hex
            destination = destination/f'{identity}.wav'
            row = {'id':identity,'status':'queued','stage':'Starting','created_at':now(),'updated_at':now()}
            try:
                atomic_json(self.root/'jobs'/f'{identity}.json',row)
                if request.idempotency_key:
                    key = hashlib.sha256(request.idempotency_key.encode()).hexdigest()
                    fingerprint = hashlib.sha256(request.model_dump_json(exclude={'idempotency_key'}).encode()).hexdigest()
                    atomic_json(self.root/'idempotency'/f'{key}.json',{'fingerprint':fingerprint,'job':identity})
                self.active = identity
                self.last_error = None
                self.executor.submit(self.run,identity,request,effective,reference,transcript,destination,handle,reference_info)
            except Exception:
                self.active = None
                handle.close()
                raise
            return row

    def run(self, identity, request, effective, reference, transcript, destination, handle, reference_info=None):
        try:
            self.update(identity,status='running')
            def stage(value):
                self.stage = 'loading' if value=='Loading model' else 'generating'
                self.update(identity,stage=value)
            metrics = self.engine.generate(effective,reference,destination,stage,transcript=transcript)
            applied = effective.model_dump(exclude={'idempotency_key'})
            applied['description'] = effective.description if reference is None else ''
            applied['ref_text'] = (transcript or None) if PROFILE['backend']=='qwen3' or effective.clone_mode in ['continuation','combined'] else None
            applied['output_dir'] = str(destination.parent)
            row = {**effective.model_dump(exclude={'idempotency_key'}),**metrics,'id':identity,
                'created_at':now(),'api_version':'2.1.0', 'requested':request.model_dump(exclude={'idempotency_key'}),
                'effective':applied, 'audio_url':f'/api/audio/outputs/{identity}',
                'path':str(destination),'metadata_path':str(destination.with_suffix('.json')),
                'reference':{'path':str(reference),'transcript':transcript,**(reference_info or {})} if reference else None}
            persist_artifact(destination,row)
            self.update(identity,status='done',stage='Finished',result=row)
            self.stage = 'ready'
        except Exception as exc:
            logging.exception('Generation failed')
            self.last_error = str(exc)
            self.stage = 'error'
            # Complete WAV+sidecar may already exist if the final index/job write failed; History can recover it.
            if not destination.with_suffix('.json').is_file(): destination.unlink(missing_ok=True)
            self.update(identity,status='error',stage='Generation failed',error=str(exc))
        finally:
            with self.guard:
                self.active = None
                handle.close()

    def unload(self):
        with self.guard:
            handle = self.reserve()
            self.unloading = True
            self.stage = 'unloading'
        def work():
            try:
                self.engine.unload()
                self.stage = 'unloaded'
                self.last_error = None
            except Exception as exc:
                self.stage, self.last_error = 'error',str(exc)
                raise
            finally:
                with self.guard:
                    self.unloading = False
                    handle.close()
        return self.executor.submit(work)
