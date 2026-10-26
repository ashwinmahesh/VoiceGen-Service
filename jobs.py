"""Durable FIFO voice jobs; one worker owns recovery and accelerator capacity."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import logging
from pathlib import Path
import threading
import wave
from config import ROOT, GPU_LOCK_FILE, PROFILE, MODEL_SPECS, PROJECT_ROOT, validate_mount,model_context
from durable_queue import QueueStore, QueueConflict
from voice_catalog import now, atomic_json, IDENTITY
from schemas import Generation, generation_metadata, MODEL_OPTIONS, PACING_FIELDS, LEGACY_BACKEND_FIELDS
from inference_locks import inherit_reservations
from model_readiness import ModelUnavailable


class BusyError(QueueConflict):
    pass


class JobManager:
    def __init__(self, engine, root=ROOT, lock_file=GPU_LOCK_FILE, profile=None, db_path=None):
        self.engine, self.root, self.lock_file = engine, Path(root), Path(lock_file)
        self.profile = dict(profile or PROFILE)
        self.runtime=getattr(engine,'runtime_model_switching',False) is True
        self.catalog=engine.catalog if self.runtime else None
        self.store = QueueStore(self.root, db_path)
        self.executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='voice-inference')
        self.guard = threading.RLock()
        self.active = None
        self.unloading = False
        self.expanding = False
        self.llm_enabled = False
        self.last_error = None
        self.stage = 'unloaded'
        self._initialized = False
        self._owner = None
        self.data_owner = None
        self._thread = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.prepare_opaque = None  # Installed by the generator; never by the CRUD service.
        self.resources = None  # Generator-only optional resource monitoring; never blocks admission.

    @property
    def current_profile(self):return dict(PROFILE) if self.runtime else self.profile

    def _ready(self):
        with self.guard:
            if not self._initialized:
                self.store.initialize()
                self._initialized = True

    def migrate_legacy(self):
        """Before starting CRUD, migrate only when a legacy queue actually exists."""
        if not self.store.legacy_path.exists() or self.store.legacy_path == self.store.path:
            return
        from data_owner import own_data_directory
        self.store.path.parent.mkdir(parents=True,exist_ok=True)
        with own_data_directory(self.root), self.store.legacy_path.with_suffix('.worker.lock').open('a+') as old_owner, self.store.path.with_suffix('.worker.lock').open('a+') as new_owner:
            try:
                for handle in (old_owner,new_owner):fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise BusyError('A generator still owns the queue; wait before migrating') from None
            self.store.migrate()

    def recover(self):
        """Call once after storage/data ownership validation, before serving."""
        with self.guard:
            if self._owner:
                return
            if not self.runtime:self.migrate_legacy()
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
            if self.runtime:self.engine.consumer_owner=handle
            try:
                if not self.runtime:self.store.migrate()
                self._ready()
                for row in self.store.interrupted():
                    # The owner lock proves no live worker still owns these records.
                    try:
                        if row['payload'].get('queue_format') == 'opaque-v1' or not self._reconcile(row):
                            self.store.change(row['id'],status='queued',stage='Recovered after restart',error=None)
                    except Exception as exc:
                        self.store.change(row['id'],status='error',stage='Recovery needs review',error=str(exc))
                # Pre-queue JSON lacks the inputs needed to safely replay. Preserve IDs/keys.
                for path in (() if self.runtime else (self.root/'jobs').glob('*.json')):
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
                self.observe('start')
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
        if self.runtime:self.engine.unload()
        self.observe('close')
        if self._owner:
            self._owner.close(); self._owner = None

    def observe(self, method, *args):
        if self.resources is not None:
            try:getattr(self.resources,method)(*args)
            except Exception:logging.warning('Resource monitoring failed; generation continues',exc_info=True)

    def public(self, row):
        result = {k:row[k] for k in ['id','status','stage','created_at','updated_at','error','result',
                  'sequence','queue_order','backend','model','mode','generate_immediately','attempts','started_at','completed_at']}
        result['blocked_reason']=row.get('blocked_reason')
        result['position']=row.get('position')
        if self.runtime:
            result['model_variant']=row['model'];result['model']=row['backend']
        result['paused'] = row['status']=='queued' and (bool(row.get('blocked_reason')) if self.runtime else row['backend'] != self.current_profile['backend'])
        if result['paused']:
            result['stage'] = 'Blocked: '+row['blocked_reason'] if self.runtime else 'Paused: select '+row['backend']+' at startup'
        plan=row['payload'].get('candidate_plan',{})
        if row['status']=='queued' and plan.get('method')=='llm' and not plan.get('ready') and not self.llm_enabled:
            result['paused']=True;result['stage']='Paused: start without NO_LLM=1 to plan distinct voices'
        result['requested'] = {k:v for k,v in row['payload'].get('requested',row['payload'].get('queue_request',{})).items() if k!='idempotency_key'}
        result['effective'] = {k:v for k,v in row['payload'].get('effective',{}).items() if k!='idempotency_key'}
        result.update({key:row['payload'][key] for key in ('candidate_batch_id','candidate_index','candidate_count','candidate_variation')
                       if key in row['payload']})
        if row['payload'].get('prompt_expansion'):result['prompt_expansion']=row['payload']['prompt_expansion']
        if row['payload'].get('candidate_variant'):result['candidate_variant']=row['payload']['candidate_variant']
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
        # New omitted pacing controls must hash exactly like pre-pacing submissions.
        exclusions={key:True for key in {'idempotency_key',*exclude}}
        if request.model is None:exclusions['model']=True
        if request.prompt_expansion_id is None:exclusions['prompt_expansion_id']=True
        for backend in ('qwen3','moss'):
            if getattr(request,backend) is None:exclusions[backend]=True
        for backend in ('qwen3','chatterbox','indextts','fish'):
            options=getattr(request,backend)
            if options is not None and backend not in exclusions:
                exclusions[backend]={key for key in PACING_FIELDS if getattr(options,key) is None}
        return hashlib.sha256(request.model_dump_json(exclude=exclusions).encode()).hexdigest()

    def existing(self, request):
        if not request.idempotency_key:
            return None
        self._ready()
        key = hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        try:
            compatible = [self.fingerprint(request,{'fish','higgs'})] if not self.runtime and request.fish is None and request.higgs is None else []
            legacy=[]
            if not self.runtime and request.clone_mode=='auto' and request.fish is None and request.higgs is None:
                prior=request.model_copy(update={'clone_mode':'reference'})
                legacy=[self.fingerprint(prior)]
                if request.fish is None and request.higgs is None:
                    legacy.append(self.fingerprint(prior,{'fish','higgs'}))
            row = self.store.existing(key,self.fingerprint(request),compatible,legacy)
        except QueueConflict as exc:
            raise BusyError(str(exc)) from None
        if row:
            if row['backend'] != (request.model if self.runtime else self.current_profile['backend']) and (not self.runtime or request.model is not None):
                raise BusyError('This idempotency key belongs to another backend')
            return self.public(row)
        if self.runtime:return None
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
                    previous_backend=original_backend if prior['status']=='done' else self.current_profile['backend']
            if previous_backend != self.current_profile['backend']:
                raise BusyError('This idempotency key belongs to another backend. Recover that job before choosing a new key.')
            candidates=[request]
            if not self.runtime and request.clone_mode=='auto' and request.fish is None and request.higgs is None:candidates.append(request.model_copy(update={'clone_mode':'reference'}))
            fingerprints=set()
            for candidate in candidates:
                added={'preset_voice':None,'accent':'','clone_mode':'reference','steering_emotion':None,'steering_strength':None}
                new={'exaggeration','cfg_weight','emotion_reference_id','emotion_vector','emotion_alpha','duration_factor','interval_silence','chatterbox','indextts','fish','higgs'}
                exclusions=[set()]
                if not candidate.generate_immediately:exclusions.append({'generate_immediately'})
                if candidate.fish is None and candidate.higgs is None:
                    exclusions += [excluded|{'fish','higgs'} for excluded in exclusions.copy()]
                for excluded in exclusions:
                    fingerprints.add(self.fingerprint(candidate,excluded))
                    if candidate.chatterbox is None and candidate.indextts is None:
                        fingerprints.add(self.fingerprint(candidate,excluded|{'chatterbox','indextts'}))
                    if all(getattr(candidate,key) is None for key in new):
                        fingerprints.add(self.fingerprint(candidate,excluded|new))
                        if all(getattr(candidate,key)==value for key,value in added.items()):
                            fingerprints.add(self.fingerprint(candidate,excluded|new|added.keys()))
                    if all(getattr(candidate,key)==value for key,value in added.items()):
                        fingerprints.add(self.fingerprint(candidate,excluded|added.keys()))
            if saved['fingerprint'] not in fingerprints:
                raise BusyError('This idempotency key was used with different settings')
            return self.read(saved['job'])
        return None

    def reserve(self):
        if self.active or self.unloading or self.expanding:
            raise BusyError('The model is busy. Wait for the current operation.')
        validate_mount(self.lock_file)
        handle = self.lock_file.open('a+')
        try:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise BusyError('Another local voice studio is generating or unloading. Wait for it to finish.') from None
        return handle

    def _payload(self, request, effective, reference, transcript, destination, reference_info):
        payload = dict(fingerprint_version=2,requested=generation_metadata(request),effective=generation_metadata(effective),
            reference=str(reference) if reference else None,
            reference_sha256=hashlib.sha256(Path(reference).read_bytes()).hexdigest() if reference else None,transcript=transcript,
            destination=str(destination),reference_info=reference_info)
        if self.runtime:
            payload['requested']['model']=self.current_profile['backend'];payload['effective']['model']=self.current_profile['backend']
        mode = 'reference' if reference else 'preset' if request.preset_voice else 'design'
        model = self.current_profile.get('modes',{}).get(mode,self.current_profile['id'])
        spec = MODEL_SPECS.get(model,{})
        payload['model_identity'] = {k:spec.get(k) for k in ['repo','revision']}
        payload['aux_references'] = {}
        if request.prompt_expansion_id:
            from prompt_expansion import applied_record
            payload['prompt_expansion']=applied_record(request,effective,self.current_profile['backend'],self.root)
        if effective.emotion_reference_id:
            path = validate_mount(self.root/'references'/f'{effective.emotion_reference_id}.wav')
            payload['aux_references']['emotion'] = {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        return payload, mode, model

    def submit(self, request, effective, reference, transcript, destination, reference_info=None):
        self._ready()
        with self.guard:
            existing = self.existing(request)
            if existing:
                return existing
            payload, mode, model = self._payload(request,effective,reference,transcript,destination,reference_info)
            key = hashlib.sha256(request.idempotency_key.encode()).hexdigest() if request.idempotency_key else None
            try:
                row = self.store.admit(self.current_profile['backend'],model,mode,request.generate_immediately,
                    payload,key,self.fingerprint(request))
            except QueueConflict as exc:
                raise BusyError(str(exc)) from None
            self._wake.set()
            return self.public(row)

    @staticmethod
    def candidate_fingerprint(request):
        excluded={'idempotency_key'}|({'prompt_expansion_id'} if request.prompt_expansion_id is None else set())
        if request.model is None:excluded.add('model')
        if request.candidate_variation=='auto':excluded.add('candidate_variation')
        if request.candidate_use_llm:excluded.add('candidate_use_llm')
        return hashlib.sha256(request.model_dump_json(exclude=excluded).encode()).hexdigest()

    def public_candidates(self, batch):
        jobs = [self.public(row) for row in batch['jobs']]
        statuses = [row['status'] for row in jobs]
        done = statuses.count('done')
        status = ('running' if 'running' in statuses else 'queued' if 'queued' in statuses else
                  'done' if done == len(jobs) else 'partial' if done else
                  'deleted' if all(value=='deleted' for value in statuses) else 'error')
        return dict(id=batch['id'],backend=batch['backend'],created_at=batch['created_at'],
                    candidate_count=len(jobs),candidate_variation=jobs[0].get('candidate_variation') or 'seed_only',status=status,done_count=done,jobs=jobs)

    def read_candidates(self, identity):
        if not IDENTITY.fullmatch(identity):
            raise FileNotFoundError('Candidate batch not found')
        self._ready()
        return self.public_candidates(self.store.read_candidates(identity))

    def existing_candidates(self, request):
        if not request.idempotency_key:
            return None
        self._ready()
        key = hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        try:
            row = self.store.existing_candidates(request.model if self.runtime else self.current_profile['backend'],key,self.candidate_fingerprint(request))
        except QueueConflict as exc:
            raise BusyError(str(exc)) from None
        return self.public_candidates(row) if row else None

    def submit_candidates(self, request, effective, reference, transcript, destination, reference_info=None, llm_enabled=False):
        self._ready()
        from candidate_diversity import definition,built_in
        plan=definition(request,effective,self.current_profile['backend'],llm_enabled)
        variants=built_in(plan) if plan and plan['method']=='built_in' else None
        with self.guard:
            payload, mode, model = self._payload(request,effective,reference,transcript,destination,reference_info)
            from copy import deepcopy
            payloads = []
            for index in range(request.candidate_count):
                item = deepcopy(payload)
                for key in ('requested','effective'):
                    item[key].pop('candidate_count',None)
                    item[key].pop('candidate_variation',None)
                    item[key].pop('candidate_use_llm',None)
                    item[key]['seed'] = (request.seed + index) % (2**32)
                item['candidate_variation']='distinct' if plan else 'seed_only'
                if plan:
                    item['candidate_plan']={**plan,'ready':bool(variants)}
                    if variants:
                        item['candidate_variant']=variants[index];item['effective']['description']=variants[index]['description']
                payloads.append(item)
            key = hashlib.sha256(request.idempotency_key.encode()).hexdigest() if request.idempotency_key else None
            try:
                row = self.store.admit_candidates(self.current_profile['backend'],model,mode,request.generate_immediately,
                                                  payloads,key,self.candidate_fingerprint(request))
            except QueueConflict as exc:
                raise BusyError(str(exc)) from None
            self._wake.set()
            return self.public_candidates(row)

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
                ready=None
                if self.runtime and self.store.has_pending():
                    reasons={}
                    for model in self.store.pending_models():
                        errors=self.catalog.errors(model,force=True)
                        reasons[model]='; '.join(errors)+f' Run make setup MODEL={model}.' if errors else None
                    self.store.mark_blocked(reasons)
                    ready=[model for model,reason in reasons.items() if not reason]
                with self.guard:
                    if not self._stop.is_set() and self.store.has_pending(None if self.runtime else self.current_profile['backend']):
                        handle = self.reserve()
                        row = self.store.claim_global(ready,allow_llm=self.llm_enabled) if self.runtime else self.store.claim(self.current_profile['backend'],allow_llm=self.llm_enabled)
                        if row:
                            self.active = row['id']
                            self.last_error = None
                        else:
                            handle.close(); handle = None
                if handle:
                    # The dispatcher waits for capacity, not an HTTP connection.
                    self.executor.submit(self._run_bound,row,handle).result()
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
            self._wake.wait(2 if self.runtime else .2)

    def _run_bound(self,row,handle):
        if self.runtime:
            from model_readiness import ModelUnavailable
            try:self.catalog.require(row['backend'],force=True)
            except ModelUnavailable as error:
                self.store.defer_blocked(row['id'],str(error))
                with self.guard:self.active=None;handle.close()
                return
            with model_context(row['backend']):return self.run(row,handle)
        return self.run(row,handle)

    def run(self, queued, handle):
        identity = queued['id']
        payload = queued['payload']
        destination = None
        resource_status='error'
        try:
            self.observe('job_started',queued)
            if payload.get('queue_format') == 'opaque-v1':
                if self.prepare_opaque is None:
                    raise ValueError('This generator has no request validator for opaque queue payloads')
                request,effective,reference,transcript,folder,info = self.prepare_opaque(payload['queue_request'])
                request = request.model_copy(update={'generate_immediately':queued['generate_immediately']})
                effective = effective.model_copy(update={'generate_immediately':queued['generate_immediately']})
                payload,mode,model = self._payload(request,effective,reference,transcript,folder,info)
                payload['destination'] = str(Path(folder)/f'{identity}.wav')
                queued = self.store.prepare_opaque(identity,payload,model,mode)
                payload = queued['payload']
            destination = Path(payload['destination'])
            if self._reconcile(queued):
                self.stage = 'ready'
                resource_status='done'
                return
            # Revalidate persisted structure, but never recompute backend defaults.
            request = Generation.model_validate(payload['requested'])
            effective = Generation.model_validate(payload['effective'])
            options = getattr(effective,self.current_profile['backend'],None)
            if self.current_profile['backend'] in ('chatterbox','indextts') and options is not None:
                effective = effective.model_copy(update={key:value for key,value in options.model_dump().items() if key in LEGACY_BACKEND_FIELDS})
            current_model = self.current_profile.get('modes',{}).get(queued['mode'],self.current_profile['id'])
            spec = MODEL_SPECS.get(current_model,{})
            if payload['model_identity'] != {k:spec.get(k) for k in ['repo','revision']}:
                raise ValueError('Queued model revision changed; inspect this entry before retrying')
            if queued['model'] != current_model:
                raise ValueError('Queued model mapping changed; inspect this entry before retrying')
            if queued['backend'] != self.current_profile['backend']:
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
            if payload.get('candidate_plan') and not payload['candidate_plan']['ready']:
                self.stage='expanding';self.update(identity,stage='Planning distinct voice designs')
                try:
                    from candidate_diversity import generate
                    with inherit_reservations(self._owner,handle,self.data_owner):
                        self.engine.unload()
                        variants=generate(payload['candidate_plan'])
                    self.store.freeze_candidate_variants(payload['candidate_batch_id'],variants)
                except Exception as exc:
                    self.store.fail_candidate_planning(payload['candidate_batch_id'],str(exc));raise
                payload=self.store.read(identity)['payload']
                effective=Generation.model_validate(payload['effective'])
            def stage(value):
                self.stage = 'loading' if value in ('Loading model','Switching model') else 'generating'
                self.update(identity,stage=value)
            with inherit_reservations(self._owner,handle,self.data_owner):
                metrics = self.engine.generate(effective,reference,destination,stage,transcript=payload['transcript'])
            metadata_settings = generation_metadata(effective)
            metadata_settings.pop('generate_immediately',None)
            applied = dict(metadata_settings)
            applied['description'] = effective.description if reference is None else ''
            applied['ref_text'] = (payload['transcript'] or None) if self.current_profile['backend'] in ('qwen3','fish','higgs') or effective.clone_mode in ['continuation','combined'] else None
            applied['output_dir'] = str(destination.parent)
            backend=self.current_profile['backend']
            if isinstance(metrics.get(backend),dict):
                applied[backend]={**(applied.get(backend) or {}),**metrics[backend]}
            result = {**metadata_settings,**metrics,'id':identity,'backend':queued['backend'],
                'model':metrics.get('model',queued['model']),'queue_model':queued['model'],
                'created_at':now(),'api_version':'4.0.0',
                'requested':payload['requested'],'effective':applied,
                'audio_url':f'/api/audio/outputs/{identity}','path':str(destination),
                'metadata_path':str(destination.with_suffix('.json')),
                'reference':{'path':str(reference),'transcript':payload['transcript'],**(payload['reference_info'] or {})} if reference else None}
            result['input_references'] = payload.get('aux_references',{})
            if payload.get('prompt_expansion'):result['prompt_expansion']=payload['prompt_expansion']
            if payload.get('candidate_variant'):result['candidate_variant']=payload['candidate_variant']
            result.update({key:payload[key] for key in ('candidate_batch_id','candidate_index','candidate_count','candidate_variation') if key in payload})
            backend=self.current_profile['backend']
            if isinstance(applied.get(backend),dict) and isinstance(metrics.get(backend),dict):
                result[backend]={**applied[backend],**metrics[backend]}
            atomic_json(destination.with_suffix('.json'),result)
            atomic_json(self.root/'artifact-index'/f'{identity}.json',{'path':str(destination)})
            self.update(identity,status='done',stage='Finished',result=result,completed_at=now())
            self.stage = 'ready'
            resource_status='done'
        except ModelUnavailable as exc:
            self.store.defer_blocked(identity,str(exc))
            self.stage='ready'
        except Exception as exc:
            logging.exception('Generation failed')
            self.last_error, self.stage = str(exc),'error'
            if self.runtime and self.engine.fatal_error:self._stop.set()
            if destination is not None and not destination.with_suffix('.json').is_file():
                destination.unlink(missing_ok=True)
            self.update(identity,status='error',stage='Generation failed',error=str(exc),completed_at=now())
        finally:
            self.observe('job_finished',resource_status)
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

    def move(self, identity, **target):
        self._ready()
        try:row=self.store.move(identity,**target)
        except QueueConflict as exc:raise BusyError(str(exc)) from None
        self._wake.set()
        return self.public(row)

    def run_now(self, identity):
        self._ready()
        try:row=self.store.run_now(identity)
        except QueueConflict as exc:raise BusyError(str(exc)) from None
        self._wake.set()
        return self.public(row)

    def retry(self, identity):
        self._ready()
        try:
            if self.runtime:self.catalog.require(self.store.read(identity)['backend'],force=True)
            row = self.store.retry(identity,None if self.runtime else self.current_profile['backend'])
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

    def preview_expansion(self,request,effective,guidance,transcript=''):
        from prompt_expansion import plan,expand
        backend=self.current_profile['backend']
        plan(request,effective,backend,guidance)
        with self.guard:
            handle=self.reserve();self.expanding=True;self.stage='expanding'
        def work():
            try:
                with model_context(backend),inherit_reservations(self._owner,handle,self.data_owner):
                    self.engine.unload()
                    return expand(request,effective,backend,guidance,self.root,transcript)
            finally:
                handle.close()
                with self.guard:
                    self.expanding=False;self.stage='unloaded';self._wake.set()
        return self.executor.submit(work)
