"""Durable FIFO voice jobs; one worker owns recovery and accelerator capacity."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import logging
from pathlib import Path
import threading
import wave
from config import ROOT, GPU_LOCK_FILE, PROFILE, MODEL_SPECS, PROJECT_ROOT, validate_mount
from durable_queue import QueueStore, QueueConflict
from voice_catalog import now, atomic_json, IDENTITY
from schemas import Generation, generation_metadata, MODEL_OPTIONS
from inference_locks import inherit_reservations


class BusyError(QueueConflict):
    pass


class JobManager:
    def __init__(self, engine, root=ROOT, lock_file=GPU_LOCK_FILE, profile=None, db_path=None):
        self.engine, self.root, self.lock_file = engine, Path(root), Path(lock_file)
        self.profile = dict(profile or PROFILE)
        self.store = QueueStore(self.root, db_path)
        self.executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='voice-inference')
        self.guard = threading.RLock()
        self.active = None
        self.unloading = False
        self.last_error = None
        self.stage = 'unloaded'
        self._initialized = False
        self._owner = None
        self.data_owner = None
        self._thread = None
        self._stop = threading.Event()
        self._wake = threading.Event()

    def _ready(self):
        with self.guard:
            if not self._initialized:
                self.store.initialize()
                self._initialized = True

    def recover(self):
        """Call once after storage/data ownership validation, before serving."""
        with self.guard:
            if self._owner:
                return
            validate_mount(self.root/'jobs')
            (self.root/'jobs').mkdir(parents=True,exist_ok=True)
            validate_mount(self.store.path)
            self.store.path.parent.mkdir(parents=True,exist_ok=True)
            handle = self.store.path.with_suffix('.worker.lock').open('a+')
            try:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                raise BusyError('Another queue worker owns this SQLite database') from None
            self._owner = handle
            try:
                self.store.migrate()
                self._ready()
                for row in self.store.interrupted():
                    # The owner lock proves no live worker still owns these records.
                    try:
                        if not self._reconcile(row):
                            self.store.change(row['id'],status='queued',stage='Recovered after restart',error=None)
                    except Exception as exc:
                        self.store.change(row['id'],status='error',stage='Recovery needs review',error=str(exc))
                # Pre-queue JSON lacks the inputs needed to safely replay. Preserve IDs/keys.
                for path in (self.root/'jobs').glob('*.json'):
                    if not IDENTITY.fullmatch(path.stem):
                        continue
                    try:
                        row = json.loads(path.read_text())
                        if row['status'] in ['queued','running']:
                            row.update(status='error',stage='Legacy job interrupted',error=
                                'Legacy job has no durable request; check History before resubmitting.',updated_at=now())
                            atomic_json(path,row)
                    except (OSError,ValueError,KeyError):
                        logging.exception('Cannot recover legacy job %s',path)
                self._thread = threading.Thread(target=self._dispatch,name='voice-queue',daemon=True)
                self._thread.start()
            except BaseException:
                self._owner.close(); self._owner = None
                raise

    def close(self):
        # Stop taking new work. A running inference keeps its slot and lock to completion.
        self._stop.set(); self._wake.set()
        if self._thread:
            self._thread.join()
        self.executor.shutdown(wait=True)
        if self._owner:
            self._owner.close(); self._owner = None

    def public(self, row):
        result = {k:row[k] for k in ['id','status','stage','created_at','updated_at','error','result',
                  'sequence','backend','model','mode','generate_immediately','attempts','started_at','completed_at']}
        result['paused'] = row['status']=='queued' and row['backend'] != self.profile['backend']
        if result['paused']:
            result['stage'] = 'Paused: select '+row['backend']+' at startup'
        result['requested'] = {k:v for k,v in row['payload']['requested'].items() if k!='idempotency_key'}
        result['effective'] = {k:v for k,v in row['payload']['effective'].items() if k!='idempotency_key'}
        return result

    def read(self, identity):
        if not IDENTITY.fullmatch(identity):
            raise FileNotFoundError('Job not found')
        self._ready()
        try:
            return self.public(self.store.read(identity))
        except FileNotFoundError:
            return json.loads((self.root/'jobs'/f'{identity}.json').read_text())

    def update(self, identity, **changes):
        return self.public(self.store.change(identity,**changes))

    @staticmethod
    def fingerprint(request, exclude=()):
        return hashlib.sha256(request.model_dump_json(exclude={'idempotency_key',*exclude}).encode()).hexdigest()

    def existing(self, request):
        if not request.idempotency_key:
            return None
        self._ready()
        key = hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        try:
            row = self.store.existing(key,self.fingerprint(request))
        except QueueConflict as exc:
            raise BusyError(str(exc)) from None
        if row:
            if row['backend'] != self.profile['backend']:
                raise BusyError('This idempotency key belongs to another backend')
            return self.public(row)
        path = self.root/'idempotency'/f'{key}.json'
        if path.is_file():
            saved = json.loads(path.read_text())
            original_backend=json.loads((PROJECT_ROOT/'service.json').read_text())['backend']
            previous_backend=saved.get('backend')
            if previous_backend is None:
                prior=self.read(saved['job'])
                result=prior.get('result') or {}
                previous_backend=result.get('backend')
                if previous_backend is None:
                    model=result.get('model','').lower()
                    previous_backend='qwen3' if 'qwen3-tts' in model else 'voxcpm2' if 'voxcpm2' in model else None
                if previous_backend is None:
                    previous_backend=original_backend if prior['status']=='done' else self.profile['backend']
            if previous_backend != self.profile['backend']:
                raise BusyError('This idempotency key belongs to another backend. Recover that job before choosing a new key.')
            added={'preset_voice':None,'accent':'','clone_mode':'reference','steering_emotion':None,'steering_strength':None}
            new={'exaggeration','cfg_weight','emotion_reference_id','emotion_vector','emotion_alpha','duration_factor','interval_silence','chatterbox','indextts'}
            exclusions=[set()]
            if not request.generate_immediately:exclusions.append({'generate_immediately'})
            fingerprints=set()
            for excluded in exclusions:
                fingerprints.add(self.fingerprint(request,excluded))
                if request.chatterbox is None and request.indextts is None:
                    fingerprints.add(self.fingerprint(request,excluded|{'chatterbox','indextts'}))
                if all(getattr(request,key) is None for key in new):
                    fingerprints.add(self.fingerprint(request,excluded|new))
                    if all(getattr(request,key)==value for key,value in added.items()):
                        fingerprints.add(self.fingerprint(request,excluded|new|added.keys()))
                if all(getattr(request,key)==value for key,value in added.items()):
                    fingerprints.add(self.fingerprint(request,excluded|added.keys()))
            if saved['fingerprint'] not in fingerprints:
                raise BusyError('This idempotency key was used with different settings')
            return self.read(saved['job'])
        return None

    def reserve(self):
        if self.active or self.unloading:
            raise BusyError('The model is busy. Wait for the current operation.')
        validate_mount(self.lock_file)
        handle = self.lock_file.open('a+')
        try:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise BusyError('Another local voice studio is generating or unloading. Wait for it to finish.') from None
        return handle

    def submit(self, request, effective, reference, transcript, destination, reference_info=None):
        self._ready()
        with self.guard:
            existing = self.existing(request)
            if existing:
                return existing
            payload = dict(requested=generation_metadata(request),effective=generation_metadata(effective),
                reference=str(reference) if reference else None,
                reference_sha256=hashlib.sha256(Path(reference).read_bytes()).hexdigest() if reference else None,transcript=transcript,
                destination=str(destination),reference_info=reference_info)
            mode = 'reference' if reference else 'preset' if request.preset_voice else 'design'
            model = self.profile.get('modes',{}).get(mode,self.profile['id'])
            spec = MODEL_SPECS.get(model,{})
            payload['model_identity'] = {k:spec.get(k) for k in ['repo','revision']}
            payload['aux_references'] = {}
            if effective.emotion_reference_id:
                path = validate_mount(self.root/'references'/f'{effective.emotion_reference_id}.wav')
                payload['aux_references']['emotion'] = {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            key = hashlib.sha256(request.idempotency_key.encode()).hexdigest() if request.idempotency_key else None
            try:
                row = self.store.admit(self.profile['backend'],model,mode,request.generate_immediately,
                    payload,key,self.fingerprint(request))
            except QueueConflict as exc:
                raise BusyError(str(exc)) from None
            self._wake.set()
            return self.public(row)

    def _reconcile(self, row):
        destination = Path(row['payload']['destination'])
        validate_mount(destination)
        sidecar = destination.with_suffix('.json')
        if not sidecar.is_file():
            return False
        # A sidecar is a completion marker only alongside valid matching audio.
        # An invalid marker is an error requiring review, never an automatic replay.
        result = json.loads(sidecar.read_text())
        with wave.open(str(destination),'rb') as audio:
            expected = audio.getnframes()*audio.getnchannels()*audio.getsampwidth()
            actual = 0
            while chunk := audio.readframes(16384):
                actual += len(chunk)
            valid = expected>0 and actual==expected and audio.getframerate()==result.get('sample_rate')
        if not valid or result.get('id') != row['id'] or result.get('backend',row['backend']) != row['backend']:
            raise ValueError('Existing artifact does not match this queue entry; inspect it before retrying')
        result.update(path=str(destination),metadata_path=str(sidecar),audio_url=f'/api/audio/outputs/{row["id"]}')
        atomic_json(self.root/'artifact-index'/f'{row["id"]}.json',{'path':str(destination)})
        self.store.change(row['id'],status='done',stage='Recovered completed artifact',error=None,
                          completed_at=now(),result=result)
        return True

    def _dispatch(self):
        while not self._stop.is_set():
            self._wake.clear()
            handle = None
            try:
                with self.guard:
                    if not self._stop.is_set() and self.store.has_pending(self.profile['backend']):
                        handle = self.reserve()
                        row = self.store.claim(self.profile['backend'])
                        if row:
                            self.active = row['id']
                            self.last_error = None
                        else:
                            handle.close(); handle = None
                if handle:
                    # The dispatcher waits for capacity, not an HTTP connection.
                    self.executor.submit(self.run,row,handle).result()
                    handle = None
                    continue
            except BusyError:
                pass
            except Exception as exc:
                logging.exception('Queue dispatch failed')
                self.last_error = str(exc)
            finally:
                if handle:
                    handle.close()
            self._wake.wait(.2)

    def run(self, queued, handle):
        identity = queued['id']
        payload = queued['payload']
        destination = Path(payload['destination'])
        try:
            if self._reconcile(queued):
                self.stage = 'ready'
                return
            # Revalidate persisted structure, but never recompute backend defaults.
            request = Generation.model_validate(payload['requested'])
            effective = Generation.model_validate(payload['effective'])
            options = getattr(effective,self.profile['backend'],None)
            if self.profile['backend'] in MODEL_OPTIONS and options is not None:
                effective = effective.model_copy(update=options.model_dump())
            current_model = self.profile.get('modes',{}).get(queued['mode'],self.profile['id'])
            spec = MODEL_SPECS.get(current_model,{})
            if payload['model_identity'] != {k:spec.get(k) for k in ['repo','revision']}:
                raise ValueError('Queued model revision changed; inspect this entry before retrying')
            if queued['model'] != current_model:
                raise ValueError('Queued model mapping changed; inspect this entry before retrying')
            if queued['backend'] != self.profile['backend']:
                raise ValueError('Queue backend differs from the selected backend')
            validate_mount(destination)
            destination.parent.mkdir(parents=True,exist_ok=True)
            reference = Path(payload['reference']) if payload['reference'] else None
            if reference:
                validate_mount(reference)
                if not reference.is_file():
                    raise ValueError('Queued reference is unavailable; reconnect its storage before retrying')
                if hashlib.sha256(reference.read_bytes()).hexdigest() != payload['reference_sha256']:
                    raise ValueError('Queued reference bytes changed; inspect the recording before resubmitting')
            for auxiliary in payload.get('aux_references',{}).values():
                path = validate_mount(auxiliary['path'])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != auxiliary['sha256']:
                    raise ValueError('Queued emotion reference is missing or changed; restore admitted bytes before retrying')
            def stage(value):
                self.stage = 'loading' if value=='Loading model' else 'generating'
                self.update(identity,stage=value)
            with inherit_reservations(self._owner,handle,self.data_owner):
                metrics = self.engine.generate(effective,reference,destination,stage,transcript=payload['transcript'])
            metadata_settings = generation_metadata(effective)
            metadata_settings.pop('generate_immediately',None)
            applied = dict(metadata_settings)
            applied['description'] = effective.description if reference is None else ''
            applied['ref_text'] = (payload['transcript'] or None) if self.profile['backend'] in ('qwen3','fish','higgs') or effective.clone_mode in ['continuation','combined'] else None
            applied['output_dir'] = str(destination.parent)
            result = {**metadata_settings,**metrics,'id':identity,'backend':queued['backend'],
                'model':metrics.get('model',queued['model']),'queue_model':queued['model'],
                'created_at':now(),'api_version':'2.1.0',
                'requested':payload['requested'],'effective':applied,
                'audio_url':f'/api/audio/outputs/{identity}','path':str(destination),
                'metadata_path':str(destination.with_suffix('.json')),
                'reference':{'path':str(reference),'transcript':payload['transcript'],**(payload['reference_info'] or {})} if reference else None}
            result['input_references'] = payload.get('aux_references',{})
            backend=self.profile['backend']
            if isinstance(applied.get(backend),dict) and isinstance(metrics.get(backend),dict):
                result[backend]={**applied[backend],**metrics[backend]}
            atomic_json(destination.with_suffix('.json'),result)
            atomic_json(self.root/'artifact-index'/f'{identity}.json',{'path':str(destination)})
            self.update(identity,status='done',stage='Finished',result=result,completed_at=now())
            self.stage = 'ready'
        except Exception as exc:
            logging.exception('Generation failed')
            self.last_error, self.stage = str(exc),'error'
            if not destination.with_suffix('.json').is_file():
                destination.unlink(missing_ok=True)
            self.update(identity,status='error',stage='Generation failed',error=str(exc),completed_at=now())
        finally:
            with self.guard:
                self.active = None
                handle.close()

    def listing(self, **kwargs):
        self._ready()
        page = self.store.listing(**kwargs)
        page['data'] = [self.public(row) for row in page['data']]
        return page

    def remove(self, identity=None, backend=None):
        self._ready()
        try:
            removed = self.store.remove(identity,backend)
        except QueueConflict as exc:
            raise BusyError(str(exc)) from None
        return dict(deleted=removed,active_job=self.active)

    def retry(self, identity):
        self._ready()
        try:
            row = self.store.retry(identity,self.profile['backend'])
        except QueueConflict as exc:
            raise BusyError(str(exc)) from None
        self._wake.set()
        return self.public(row)

    def unload(self):
        with self.guard:
            handle = self.reserve()
            self.unloading, self.stage = True,'unloading'
        def work():
            try:
                self.engine.unload()
                self.stage, self.last_error = 'unloaded',None
            except Exception as exc:
                self.stage, self.last_error = 'error',str(exc)
                raise
            finally:
                with self.guard:
                    self.unloading = False
                    handle.close()
                    self._wake.set()
        return self.executor.submit(work)
