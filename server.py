"""Shared Voice Studio v4 API. Service-specific behavior is in service.json."""
import argparse
import asyncio
from contextlib import asynccontextmanager, nullcontext
import json
import hashlib
import sqlite3
from pathlib import Path
from typing import Literal
from uuid import uuid4
from config import ROOT, PROJECT_ROOT, PROFILE, DEFAULT_PORT, prepare_storage,model_context,validate_mount
from queue_config import QUEUE_DB, DEFAULT_QUEUE_PORT
from queue_schemas import QueueMove, QueueState, ModelList
from resource_schemas import ResourceOverview, ResourceJobPage, ResourceJobHistory, JobLogs, ModelLifecyclePage
from queue_status import api_status, storage_error_response, STORAGE_ERROR
from model_readiness import ReadyModels,ModelUnavailable
from runtime_engine import RuntimeEngine
from assets import all_errors
from runtime_checks import runtime_errors
from capabilities import capabilities, validate_controls
from jobs import JobManager, BusyError
import storage
import pause_edits
from transcription_schemas import TranscriptionStatus
from transcription import transcribe_reference,TranscriptionError
from transcription_assets import status as transcription_status
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from schemas import Generation, CandidateGeneration, CandidateBatch, ExpandVoiceGuidance, PromptExpansion, Health, Capabilities, Job, Artifact, ArtifactPage, Reference, Unloaded, TagsPage, PauseAdjustment, QueuePage, QueueRemoval, CollectionChange
from starlette.middleware.trustedhost import TrustedHostMiddleware
from body_limit import BodyLimit
import voice_catalog
from voice_catalog import SaveVoice, VoiceUpdate, Voice, VoicePage

engine = RuntimeEngine(ReadyModels())
manager = JobManager(engine, db_path=QUEUE_DB)


@asynccontextmanager
async def lifespan(app):
    prepare_storage()
    manager.llm_enabled=app.state.llm_enabled
    manager.prepare_opaque=prepare_queued_request
    from prompt_expansion import capability
    capability(app.state.llm_enabled)  # Validate all published profile specifications before queue recovery.
    if app.state.llm_enabled:
        from prompt_assets import check_assets,check_runtime
        check_assets();check_runtime()
    from license_notices import warn_at_startup
    warn_at_startup(PROFILE['backend'])
    print('Local voice prompt expansion: '+('enabled · Qwen3.5-2B 4-bit' if app.state.llm_enabled else 'disabled · restart without NO_LLM=1 to enable'),flush=True)
    from data_owner import own_data_directory
    with own_data_directory(ROOT,manager.store.path.with_suffix('.data.lock')) as owner:
        manager.data_owner = owner
        if manager.runtime is True:
            manager._ready()
            ready=manager.catalog.models(force=True)
            if not ready:raise RuntimeError('No fully set-up voice models are ready. Run make setup or reconnect model storage.')
            preferred=app.state.requested_default or manager.store.default_model()
            if app.state.requested_default:manager.catalog.require(preferred)
            if preferred not in {row['id'] for row in ready}:preferred=ready[0]['id']
            manager.store.set_setting('default_model',preferred)
            app.state.default_model_snapshot=preferred
        from observability import ResourceMonitor
        manager.resources=ResourceMonitor(manager.store.path,PROFILE['backend'],PROFILE['id'],lambda:manager.stage,
            selection=(lambda:{'backend':engine.backend or app.state.default_model_snapshot,'model':engine.variant or engine.backend or app.state.default_model_snapshot}) if manager.runtime is True else None)
        manager.recover()
        try:
            yield
        finally:
            await asyncio.to_thread(manager.close)
            manager.data_owner = None


app = FastAPI(title='Local Voice Studio API',version='4.0.0',lifespan=lifespan)
app.state.llm_enabled=True
app.state.queue_port=DEFAULT_QUEUE_PORT
app.state.requested_default=None
app.add_exception_handler(sqlite3.Error,storage_error_response)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
app.add_middleware(BodyLimit)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Invalid NaN/Infinity inputs cannot be echoed by JSONResponse. Keep errors JSON-safe
    # and avoid reflecting submitted text/audio metadata in validation responses.
    return JSONResponse(status_code=422,content={'detail':[
        {key:error[key] for key in ['loc','msg','type']} for error in exc.errors()]})


@app.middleware('http')
async def local_writes(request: Request, call_next):
    origin = request.headers.get('origin')
    if request.method not in ('GET','HEAD','OPTIONS') and origin:
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        if parsed.netloc != request.headers.get('host') or parsed.scheme != 'http':
            return JSONResponse({'detail':'Use the local studio to make changes'},status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def asset_path(kind, identity):
    try: return storage.asset_path(kind,identity)
    except (OSError,ValueError,KeyError,RuntimeError):
        raise HTTPException(404,'Audio is unavailable; check its storage volume') from None


def ui_revision():
    # Bind HTML and its scripts to one revision so a returning browser cannot mix them.
    names=('index.html','app.js','recording-session.js','voice-upload.js','observability.js','observability.css')
    return hashlib.sha256(b'\0'.join((PROJECT_ROOT/'ui'/name).read_bytes() for name in names)).hexdigest()[:12]


def ui_shell(revision=None):
    return (PROJECT_ROOT/'ui/index.html').read_text().replace('__UI_VERSION__',revision or ui_revision())


@app.get('/',include_in_schema=False)
@app.get('/collection',include_in_schema=False)
@app.get('/collection/',include_in_schema=False)
def index(): return HTMLResponse(ui_shell(),headers={'Cache-Control':'no-cache'})


@app.get('/assets/voice-upload.js',include_in_schema=False)
def voice_upload_script(): return FileResponse(PROJECT_ROOT/'ui/voice-upload.js',media_type='text/javascript',headers={'Cache-Control':'no-cache'})


@app.get('/assets/recording-session.js',include_in_schema=False)
def recording_session_script(): return FileResponse(PROJECT_ROOT/'ui/recording-session.js',media_type='text/javascript',headers={'Cache-Control':'no-cache'})


@app.get('/assets/app.js',include_in_schema=False)
def app_script(): return FileResponse(PROJECT_ROOT/'ui/app.js',media_type='text/javascript',headers={'Cache-Control':'no-cache'})


@app.get('/assets/icon.svg',include_in_schema=False)
def app_icon(): return FileResponse(PROJECT_ROOT/'ui/icon.svg',media_type='image/svg+xml')


@app.get('/apple-touch-icon.png',include_in_schema=False)
def touch_icon(): return FileResponse(PROJECT_ROOT/'ui/apple-touch-icon.png',media_type='image/png')


@app.get('/favicon.ico',include_in_schema=False)
def favicon(): return FileResponse(PROJECT_ROOT/'ui/favicon.ico',media_type='image/vnd.microsoft.icon')


@app.get('/observability',include_in_schema=False)
@app.get('/observability/',include_in_schema=False)
def observability_page():
    # Use the generator's complete shell so header layout and controls have one source.
    revision=ui_revision()
    shell=ui_shell(revision)
    return HTMLResponse(shell.replace('<!-- page-assets -->',f'<link rel="stylesheet" href="/assets/observability.css?v={revision}">')
        .replace('<!-- page-content -->',(PROJECT_ROOT/'ui/observability.html').read_text())
        .replace('<!-- page-script -->',f'<script src="/assets/observability.js?v={revision}"></script>'),headers={'Cache-Control':'no-cache'})


@app.get('/assets/observability.js',include_in_schema=False)
def observability_script(): return FileResponse(PROJECT_ROOT/'ui/observability.js',media_type='text/javascript',headers={'Cache-Control':'no-cache'})


@app.get('/assets/observability.css',include_in_schema=False)
def observability_styles(): return FileResponse(PROJECT_ROOT/'ui/observability.css',media_type='text/css',headers={'Cache-Control':'no-cache'})


def resource_monitor():
    if manager.resources is None:raise HTTPException(503,'Resource monitoring has not started yet')
    return manager.resources


@app.get('/api/observability',response_model=ResourceOverview,tags=['Observability'],summary='Resource history for a time window')
def resource_history(window: Literal['5m','30m','1h','6h','24h','3d','7d']='5m', model: str | None=Query(None,max_length=80)):
    from observability import WINDOWS, SCOPES, RETENTION_SECONDS
    import time,os
    from queue_config import MODELS
    monitor=resource_monitor();end=time.time()
    return {**monitor.store.series(end-WINDOWS[window],end,model),'window':window,'scopes':SCOPES,
            'retention_seconds':RETENTION_SECONDS,'collector':monitor.status(),'available_models':MODELS,'cpu_cores':os.cpu_count()}


@app.get('/api/observability/jobs',response_model=ResourceJobPage,tags=['Observability'],summary='Recorded job attempts and resource peaks')
def resource_jobs(model: str | None=Query(None,max_length=80), limit: int=Query(25,ge=1,le=100), offset: int=Query(0,ge=0)):
    return resource_monitor().store.jobs(model,limit,offset)


@app.get('/api/observability/models',response_model=ModelLifecyclePage,tags=['Observability'],summary='Model load and unload history')
def model_lifecycle(window: Literal['24h','7d','30d','90d','all']='30d',
                    model: Literal['higgs','voxcpm2','qwen3','chatterbox','indextts','moss','fish'] | None=None,
                    action: Literal['load','unload'] | None=None,
                    scope: Literal['runtime_process','weights','native_weights','native_release','llm_weights'] | None=None,
                    job_id: str | None=Query(None,pattern='^[a-f0-9]{32}$'),
                    run_id: str | None=Query(None,pattern='^[a-f0-9]{32}$'),
                    limit: int=Query(25,ge=1,le=100),offset: int=Query(0,ge=0)):
    from lifecycle_store import LIFECYCLE_WINDOWS, LIFECYCLE_RETENTION, LIFECYCLE_SCOPES
    import time
    monitor=resource_monitor();end=time.time();seconds=LIFECYCLE_WINDOWS[window]
    start=end-seconds if seconds is not None else None
    return {**monitor.store.lifecycle(start,end,model,action,scope,job_id,run_id,limit,offset),
            'window':window,'start':start,'end':end,'retention_seconds':LIFECYCLE_RETENTION,
            'scopes':LIFECYCLE_SCOPES,'collector':monitor.status()}


@app.get('/api/observability/jobs/{identity}',response_model=ResourceJobHistory,tags=['Observability'],summary='Resource charts for one job or selected attempt')
def resource_job(identity: str, run_id: str | None=Query(None,pattern='^[a-f0-9]{32}$')):
    from observability import SCOPES, RETENTION_SECONDS
    from time import time
    if not voice_catalog.IDENTITY.fullmatch(identity):raise HTTPException(404,'Job resource history not found')
    monitor=resource_monitor()
    runs=monitor.store.jobs(job_id=identity,limit=100)['data']
    if not runs:raise HTTPException(404,'No retained resource samples for this job. Recording starts after observability is installed.')
    selected=next((row for row in runs if row['id']==run_id),None) if run_id else runs[0]
    if selected is None:raise HTTPException(404,'Recorded attempt not found for this job')
    start=max(selected['started_at'],time()-RETENTION_SECONDS)
    end=selected['ended_at'] or time()
    return {**monitor.store.series(start,end,job_id=identity,run_id=selected['id']),'job_id':identity,'runs':runs,'selected_run':selected,
            'scopes':SCOPES,'collector':monitor.status()}


@app.get('/api/jobs/{identity}/logs',response_model=JobLogs,tags=['Observability'],summary='Read the available job-specific runtime log')
def generation_logs(identity: str, attempt: int | None=Query(None,ge=1)):
    from job_logs import job_log
    if not voice_catalog.IDENTITY.fullmatch(identity):raise HTTPException(404,'Job not found')
    manager._ready()
    try:
        row=manager.store.read(identity)
        result=job_log(row,manager.root,attempt)
        # A retry can start while the file is being read; never label its output as the prior attempt.
        current=manager.store.read(identity)
        if (current['attempts'],current['started_at']) != (row['attempts'],row['started_at']):
            result.update(available=False,text='',truncated=False,source=None,updated_at=None,error=None,
                          notice='The job changed attempts while reading its log. Refresh to view the selected attempt.')
        return result
    except FileNotFoundError as exc:raise HTTPException(404,str(exc)) from None


def requested_model(model=None):
    if manager.runtime is not True:return PROFILE['backend']
    manager._ready()
    target=model or manager.store.default_model()
    try:return manager.catalog.require(target,force=True)
    except ModelUnavailable as error:raise HTTPException(422,str(error)) from None


def request_context(model=None):
    target=requested_model(model)
    return model_context(target) if manager.runtime is True else nullcontext()


@app.get('/v1/models',response_model=ModelList)
def model_discovery():
    if manager.runtime is not True:
        rows=[{'id':PROFILE['backend'],'name':PROFILE['name'],'modes':list(PROFILE.get('modes',{}))}];default=PROFILE['backend']
    else:
        manager._ready();rows=manager.catalog.models();default=manager.store.default_model()
    return {'data':[{**row,'is_default':row['id']==default} for row in rows],
            'default_model':default,'active_model':engine.active_model if manager.runtime is True else None,
            'runtime_model_switching':manager.runtime}


@app.get('/api/capabilities',response_model=Capabilities)
def get_capabilities(model: str | None=Query(None,max_length=80)):
    with request_context(model):return capabilities_for_model()


def capabilities_for_model():
    from prompt_expansion import capability
    from prompt_profiles import load_profile
    caps=capabilities();caps.limits['prompt_expansion']=capability(app.state.llm_enabled)
    if manager.runtime is True:
        ready={row['id'] for row in manager.catalog.models()}
        design=caps.modes['design'];design['ready_models']=[row for row in design['supported_models'] if row['id'] in ready]
    caps.models[PROFILE['backend']]={**caps.models.get(PROFILE['backend'],{}),'prompt_expansion':load_profile(PROFILE['backend'])}
    return caps


@app.get('/health',response_model=Health)
def health():
    if manager.runtime is True:return runtime_health()
    errors = all_errors()
    runtime = runtime_errors()
    status = manager.stage
    if errors or runtime: status = 'error'
    elif not manager.active and not manager.unloading and not manager.expanding and not manager.last_error:
        status = 'ready' if engine.model is not None else 'unloaded'
    queue_storage={'status':'ready','message':None}
    try:
        # Diagnostics must not wait on the dispatcher's inference/admission guard.
        counts=manager.store.listing(limit=1,timeout=.25)['counts']
    except sqlite3.Error:
        counts={};queue_storage={'status':'unavailable','message':STORAGE_ERROR}
    return Health(status=status,service=PROFILE['id'],name=PROFILE['name'],project=str(PROJECT_ROOT),
        storage=str(ROOT),downloaded=not errors,asset_errors=errors,runtime_errors=runtime,model_loaded=engine.model is not None,
        loaded_variant=engine.variant,busy=bool(manager.active or manager.unloading or manager.expanding),
        active_job=manager.active,last_error=manager.last_error,
        queue_database=str(manager.store.path),queue_counts=counts,queue_storage=queue_storage,
        queue_service=api_status(app.state.queue_port,manager.store.path),
        prompt_expansion=__import__('prompt_expansion').capability(app.state.llm_enabled))


@app.post('/api/prompts/expand',response_model=PromptExpansion,response_model_exclude_unset=True)
async def expand_voice_guidance(payload: ExpandVoiceGuidance):
    if not app.state.llm_enabled:raise HTTPException(422,'Local prompt expansion is disabled. Start with make serve without NO_LLM=1.')
    try:
        with request_context(payload.generation.model):
            effective,reference,transcript,info=prepare_generation(payload.generation)
            future=manager.preview_expansion(payload.generation,effective,payload.guidance,transcript,
                higgs_control=payload.higgs.control if payload.higgs else None)
        return await asyncio.shield(asyncio.wrap_future(future))
    except BusyError as exc:raise HTTPException(409,str(exc)) from None
    except (ValueError,OSError,RuntimeError) as exc:raise HTTPException(422,str(exc)) from None


@app.post('/api/generate',status_code=202,response_model=Job)
def generate(request: Generation):
    return admit_generation(request)


@app.post('/api/candidates',status_code=202,response_model=CandidateBatch)
def generate_candidates(request: CandidateGeneration):
    return admit_generation(request,candidates=True)


@app.get('/api/candidates/{identity}',response_model=CandidateBatch)
def candidate_status(identity: str):
    try: return manager.read_candidates(identity)
    except (OSError,ValueError): raise HTTPException(404,'Candidate batch not found') from None


def prepare_generation(request):
    try:effective = validate_controls(request)
    except ValueError:
        if manager.runtime is True and not (request.voice_id or request.reference_id or request.preset_voice):
            caps=capabilities_for_model()
            if not caps.modes['design']['available']:
                raise ValueError(f'{caps.name} requires a saved voice or reference recording. '+caps.modes['design']['unavailable_reason']) from None
        raise
    reference = None
    reference_info = None
    transcript = request.ref_text or ''
    if request.voice_id or request.reference_id:
        reference = asset_path('voices' if request.voice_id else 'references',request.voice_id or request.reference_id)
        source = json.loads(reference.with_suffix('.json').read_text())
        transcript = request.ref_text if request.ref_text is not None else source.get('transcript','')
        reference_info = {key:source[key] for key in ['id','name','duration','original','effective','sample_rate','channels','source_id','source_kind'] if key in source}
        import hashlib
        reference_info['sha256']=hashlib.sha256(reference.read_bytes()).hexdigest()
        effective = effective.model_copy(update={'description':source.get('design_description',source.get('description',''))})
    from capabilities import resolve_clone_mode
    effective = resolve_clone_mode(effective,PROFILE['backend'],reference,transcript)
    if effective.clone_mode in ['continuation','combined'] and not transcript.strip():
        raise ValueError('Continuation/combined cloning requires the exact transcript of the reference recording')
    if PROFILE['backend'] in ('fish','higgs') and reference and not transcript.strip():
        raise ValueError('This backend requires the exact reference transcript')
    if effective.emotion_reference_id:
        asset_path('references',effective.emotion_reference_id)
    return effective,reference,transcript,reference_info


def prepare_queued_request(payload):
    """Only the generator understands the opaque CRUD service's payloads."""
    request = Generation.model_validate(payload)
    if manager.runtime is True:
        if request.model is not None and request.model!=PROFILE['backend']:
            raise ValueError('The payload model must match the immutable queue model')
        request=request.model_copy(update={'model':PROFILE['backend']})
    if request.idempotency_key:
        raise ValueError('Use the queue envelope idempotency_key, not a key inside its payload')
    effective,reference,transcript,info = prepare_generation(request)
    errors = [] if manager.runtime is True else all_errors()+runtime_errors()
    if errors:
        raise ValueError('Incomplete generator setup: '+'; '.join(errors))
    return request,effective,reference,transcript,storage.output_folder(request.output_dir),info


def admit_generation(request, candidates=False):
    try:
        existing = manager.existing_candidates(request) if candidates else manager.existing(request)
        if existing: return existing
        with request_context(request.model):
            effective,reference,transcript,reference_info=prepare_generation(request)
            errors = [] if manager.runtime is True else all_errors()+runtime_errors()
            if errors: raise HTTPException(503,'Incomplete setup. Run make setup for the selected MODEL. '+ '; '.join(errors))
            destination = storage.output_folder(request.output_dir)
            if candidates:return manager.submit_candidates(request,effective,reference,transcript,destination,reference_info,llm_enabled=app.state.llm_enabled)
            return manager.submit(request,effective,reference,transcript,destination,reference_info)
    except BusyError as exc:
        raise HTTPException(409,str(exc)) from None
    except (ValueError,OSError,RuntimeError) as exc:
        raise HTTPException(422,str(exc)) from None


def runtime_health():
    manager._ready()
    ready,blocked=manager.catalog.snapshot()
    default=app.state.default_model_snapshot;paused=False
    queue_storage={'status':'ready','message':None}
    try:
        counts=manager.store.listing(limit=1,timeout=.25)['counts']
        default=manager.store.default_model(timeout=.25)
        paused=manager.store.setting('paused',False,timeout=.25)
        app.state.default_model_snapshot=default
    except sqlite3.Error:counts={};queue_storage={'status':'unavailable','message':STORAGE_ERROR}
    return Health(service='voicegen-service',name='VoiceGen Service',project=str(PROJECT_ROOT),storage=str(ROOT),
        runtime_model_switching=True,default_model=default,active_model=engine.active_model,
        queue_scope='shared',api_version='4.0.0',mode='generator',queue_paused=paused,blocked_models=blocked,
        status='error' if engine.fatal_error else manager.stage if manager.active or manager.unloading or manager.expanding else 'ready' if ready else 'error',
        downloaded=bool(ready),asset_errors=[] if ready else ['No ready voice models. Run make setup.'],runtime_errors=[engine.fatal_error] if engine.fatal_error else [],
        model_loaded=engine.model is not None,loaded_variant=engine.variant,busy=bool(manager.active or manager.unloading or manager.expanding or engine.switching),
        active_job=manager.active,last_error=manager.last_error,queue_database=str(manager.store.path),queue_counts=counts,queue_storage=queue_storage,
        queue_service=api_status(app.state.queue_port,manager.store.path),prompt_expansion=__import__('prompt_expansion').capability(app.state.llm_enabled))


@app.post('/api/queue/pause',response_model=QueueState)
def queue_pause():return manager.store.pause(True)


@app.post('/api/queue/resume',response_model=QueueState)
def queue_resume():
    result=manager.store.pause(False);manager._wake.set();return result


@app.get('/api/jobs/{identity}',response_model=Job)
def job_status(identity: str):
    try: return manager.read(identity)
    except (OSError,ValueError): raise HTTPException(404,'Job not found') from None


@app.get('/api/queue',response_model=QueuePage)
def queue_list(status: Literal['pending','all','queued','running','done','error','deleted']='pending',
               model: str | None=Query(default=None,max_length=80),
               limit: int=Query(default=24,ge=1,le=100),offset: int=Query(default=0,ge=0)):
    statuses = ('queued','running') if status=='pending' else () if status=='all' else (status,)
    return manager.listing(statuses=statuses,backend=model,limit=limit,offset=offset)


@app.delete('/api/queue',response_model=QueueRemoval)
def queue_clear(model: str | None=Query(default=None,max_length=80)):
    return manager.remove(backend=model)


@app.delete('/api/queue/{identity}',response_model=QueueRemoval)
def queue_delete(identity: str):
    try: return manager.remove(identity)
    except FileNotFoundError: raise HTTPException(404,'Queue entry not found') from None
    except BusyError as exc: raise HTTPException(409,str(exc)) from None


@app.post('/api/jobs/{identity}/retry',status_code=202,response_model=Job)
def queue_retry(identity: str):
    try: return manager.retry(identity)
    except ModelUnavailable as exc: raise HTTPException(422,str(exc)) from None
    except FileNotFoundError: raise HTTPException(404,'Queue entry not found') from None
    except BusyError as exc: raise HTTPException(409,str(exc)) from None


@app.post('/api/queue/{identity}/move',response_model=Job)
def queue_move(identity: str, request: QueueMove):
    try:return manager.move(identity,**request.model_dump(exclude_none=True))
    except FileNotFoundError:raise HTTPException(404,'Queue entry or target not found') from None
    except BusyError as exc:raise HTTPException(409,str(exc)) from None
    except ValueError as exc:raise HTTPException(422,str(exc)) from None


@app.post('/api/queue/{identity}/run-now',response_model=Job)
def queue_run_now(identity: str):
    try:return manager.run_now(identity)
    except FileNotFoundError:raise HTTPException(404,'Queue entry not found') from None
    except BusyError as exc:raise HTTPException(409,str(exc)) from None


@app.get('/api/history',response_model=ArtifactPage)
def history(q: str=Query(default='',max_length=300),limit: int=Query(default=24,ge=1,le=100),
            offset: int=Query(default=0,ge=0),deleted: bool=False):
    words = voice_catalog.searchable(q).split()
    rows = [r for r in storage.artifact_rows(deleted=deleted) if all(word in voice_catalog.searchable(' '.join(
        str(r.get(k,'')) for k in ['text','description','emotion'])) for word in words)]
    total = len(rows); more = offset+limit < total
    return ArtifactPage(data=rows[offset:offset+limit],total=total,offset=offset,limit=limit,
                        has_more=more,next_offset=offset+limit if more else None)


def change_collection(identity,kind,deleted):
    try:
        validate_mount(ROOT)
        if kind=='voices':voice_catalog.read_voice(ROOT,identity,include_deleted=True)
        else:storage.read_artifact(identity)
        changed=voice_catalog.set_deleted(ROOT,kind,identity,deleted)
        return dict(id=identity,kind=kind,deleted=deleted,changed=changed)
    except FileNotFoundError:raise HTTPException(404,'Collection entry or its recording is unavailable') from None
    except (OSError,ValueError,TypeError,KeyError,RuntimeError) as exc:
        raise HTTPException(422,'Could not update Collection. Check that its storage is available and writable: '+str(exc)) from None


@app.delete('/api/artifacts/{identity}',response_model=CollectionChange)
def delete_artifact(identity: str):return change_collection(identity,'outputs',True)


@app.post('/api/artifacts/{identity}/restore',response_model=CollectionChange)
def restore_artifact(identity: str):return change_collection(identity,'outputs',False)


@app.get('/api/artifacts/{identity}',response_model=Artifact)
def get_artifact(identity: str):
    try: return storage.read_artifact(identity)
    except (OSError,ValueError,KeyError,RuntimeError): raise HTTPException(404,'Artifact unavailable') from None


@app.post('/api/artifacts/{identity}/pauses',status_code=201,response_model=Artifact)
def adjust_artifact_pauses(identity: str, request: PauseAdjustment):
    try:
        return pause_edits.adjust_pauses(identity,request)
    except pause_edits.PauseEditBusy as exc:
        raise HTTPException(409,str(exc)) from None
    except FileNotFoundError:
        raise HTTPException(404,'Source audio is unavailable; check its storage volume') from None
    except (OSError,ValueError,KeyError,RuntimeError) as exc:
        raise HTTPException(422,str(exc)) from None


@app.get('/api/metadata/{identity}')
def metadata(identity: str): return FileResponse(asset_path('outputs',identity).with_suffix('.json'),media_type='application/json')


@app.get('/api/audio/{kind}/{identity}')
def audio(kind: str,identity: str,download: bool=False):
    path = asset_path(kind,identity)
    return FileResponse(path,media_type='audio/wav',filename=path.name if download else None)


@app.get('/api/transcription',response_model=TranscriptionStatus,tags=['Voices'],summary='Local automatic transcription readiness')
def transcription_readiness():return transcription_status()


def remove_new_reference(reference):
    for path in [ROOT/'references'/f'{reference["id"]}.wav',ROOT/'references'/f'{reference["id"]}.json',ROOT/'references/originals'/f'{reference["id"]}.bin']:
        path.unlink(missing_ok=True)


async def prepare_uploaded_reference(raw,filename,transcript,transcribe,language,request):
    try:reference=await asyncio.to_thread(storage.preserve_reference,raw,filename,transcript)
    except (OSError,ValueError,RuntimeError) as exc:raise HTTPException(422,'Cannot use this reference: '+str(exc)) from None
    if not transcribe or transcript.strip():return reference
    try:
        text,receipt=await transcribe_reference(ROOT/'references'/f'{reference["id"]}.wav',ROOT,language,request.is_disconnected)
        reference.update(transcript=text,transcription=receipt)
        await asyncio.to_thread(voice_catalog.atomic_json,ROOT/'references'/f'{reference["id"]}.json',reference)
        return reference
    except BaseException as exc:
        await asyncio.to_thread(remove_new_reference,reference)
        if isinstance(exc,TranscriptionError):raise HTTPException(exc.status,str(exc)) from None
        raise


@app.post('/api/references',status_code=201,response_model=Reference)
async def upload_reference(request: Request,file: UploadFile=File(...), transcript: str=Form(default='',max_length=3000),
                           transcribe: bool=Form(default=False),transcription_language: str=Form(default='auto',max_length=8)):
    raw = await file.read(25*1024*1024+1)
    if len(raw)>25*1024*1024: raise HTTPException(413,'Choose a file under 25 MB')
    return await prepare_uploaded_reference(raw,file.filename,transcript,transcribe,transcription_language,request)


@app.get('/api/references/{identity}/original')
def original_reference(identity: str):
    path = asset_path('references',identity)
    try:
        row = json.loads(path.with_suffix('.json').read_text())
        original = ROOT/'references/originals'/f'{identity}.bin'
        if not original.is_file(): raise FileNotFoundError()
        return FileResponse(original,media_type='application/octet-stream',filename=row['original']['filename'])
    except (OSError,KeyError,ValueError): raise HTTPException(404,'Original is unavailable for this reference') from None


@app.post('/api/model/unload',response_model=Unloaded)
async def unload():
    try: await asyncio.wrap_future(manager.unload())
    except BusyError as exc: raise HTTPException(409,str(exc)) from None
    return Unloaded(model_loaded=False)
@app.get("/api/voices", response_model=VoicePage)
def voices(q: str = Query(default="", max_length=300), tag: str = Query(default="", max_length=40),
           sort: Literal["newest", "updated", "name"] = "newest",
           limit: int = Query(default=24, ge=1, le=100), offset: int = Query(default=0, ge=0),deleted: bool=False):
    return voice_catalog.search(ROOT, q, tag, sort, limit, offset,deleted=deleted)


@app.delete('/api/voices/{identity}',response_model=CollectionChange)
def delete_voice(identity: str):return change_collection(identity,'voices',True)


@app.post('/api/voices/{identity}/restore',response_model=CollectionChange)
def restore_voice(identity: str):return change_collection(identity,'voices',False)


@app.post('/api/voices/upload',status_code=201,response_model=Voice)
async def upload_voice(request: Request,file: UploadFile=File(...),name: str=Form(...,min_length=1,max_length=100),
                       description: str=Form(default='',max_length=2000),
                       transcript: str=Form(default='',max_length=3000),tags: list[voice_catalog.Tag]=Form(default=[],max_length=20),
                       transcribe: bool=Form(default=True),transcription_language: str=Form(default='auto',max_length=8)):
    try:
        details=SaveVoice(name=name,description=description,transcript=transcript,tags=tags)
    except ValueError as exc:
        message=exc.errors()[0]['msg'] if hasattr(exc,'errors') else str(exc)
        raise HTTPException(422,'Invalid voice details: '+message) from None
    raw=await file.read(25*1024*1024+1)
    if len(raw)>25*1024*1024:raise HTTPException(413,'Choose a file under 25 MB')
    reference=await prepare_uploaded_reference(raw,file.filename,details.transcript or '',transcribe,transcription_language,request)
    try:return await asyncio.to_thread(save_voice,details.model_copy(update={'reference_id':reference['id'],'transcript':reference['transcript']}))
    except BaseException as exc:
        await asyncio.to_thread(remove_new_reference,reference)
        if isinstance(exc,(OSError,ValueError,RuntimeError)):raise HTTPException(422,'Cannot upload this voice: '+str(exc)) from None
        raise


@app.get("/api/voice-tags", response_model=TagsPage)
def voice_tags(deleted: bool=False):
    counts = {}
    for voice in voice_catalog.all_voices(ROOT,deleted=deleted):
        for tag in set(voice.tags):
            counts[tag] = counts.get(tag, 0) + 1
    return {"data": [{"name": tag, "count": counts[tag]} for tag in sorted(counts)]}


@app.get("/api/voices/{identity}", response_model=Voice)
def get_voice(identity: str):
    try:
        return voice_catalog.read_voice(ROOT, identity)
    except (FileNotFoundError, ValueError, TypeError):
        raise HTTPException(404, "Voice or its reference recording is unavailable") from None
    except OSError:
        raise HTTPException(500, "Could not read voice details. Check that the SSD is available.") from None


@app.patch("/api/voices/{identity}", response_model=Voice)
def edit_voice(identity: str, request: VoiceUpdate):
    changes = request.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "Provide a name, description, or tags to update")
    try:
        return voice_catalog.update_voice(ROOT, identity, changes)
    except (FileNotFoundError, ValueError, TypeError):
        raise HTTPException(404, "Voice or its reference recording is unavailable") from None
    except OSError:
        raise HTTPException(500, "Could not save voice details. Check that the SSD is writable.") from None


@app.post("/api/voices", status_code=201, response_model=Voice)
def save_voice(request: SaveVoice):
    if not request.name.strip():
        raise HTTPException(422, "Give this voice a name")
    if bool(request.output_id) == bool(request.reference_id):
        raise HTTPException(422, "Choose one generated sample or uploaded reference")
    source_kind = "outputs" if request.output_id else "references"
    source = asset_path(source_kind, request.output_id or request.reference_id)
    import soundfile as sf
    import numpy as np
    try:
        original = json.loads(source.with_suffix(".json").read_text())
        with sf.SoundFile(source) as stream:
            sr = stream.samplerate
            source_duration = stream.frames / sr
            # Read only the reference portion, even for long generated takes.
            data = stream.read(frames=sr * 30, dtype="float32", always_2d=True).mean(axis=1)
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(422, "The source audio or metadata cannot be read") from None
    if len(data) < sr or not np.isfinite(data).all() or float(np.max(np.abs(data))) < 1e-5:
        raise HTTPException(422, "Use a reference with at least one second of audible speech")
    identity = uuid4().hex
    target = ROOT / "voices" / f"{identity}.wav"
    timestamp = voice_catalog.now()
    description = request.description if request.description is not None else original.get("description", "")
    row = {"id": identity, "name": request.name, "description": description, "tags": request.tags,
           "design_description": original.get("design_description", original.get("description", "")),
           "created_at": timestamp, "updated_at": timestamp,
           "source_id": source.stem, "source_kind": source_kind,
           "duration": round(len(data) / sr, 3), "sample_rate": sr, "channels": 1,
           "audio_url": f"/api/audio/voices/{identity}",
           "transcript": request.transcript if request.transcript is not None else
               (__import__("performance_controls").spoken_transcript(original) if source_duration <= 30 and not original.get("possibly_truncated") else "")}
    if original.get('transcription') and row['transcript']==original.get('transcript'):
        row['transcription']=original['transcription']
    temporary = target.with_suffix(".tmp.wav")
    try:
        sf.write(temporary, data, sr, subtype="PCM_16")
        temporary.replace(target)
        voice_catalog.atomic_json(target.with_suffix(".json"), row)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return voice_catalog.read_voice(ROOT, identity)



def run_server(port=DEFAULT_PORT, queue_port=DEFAULT_QUEUE_PORT):
    """Run the generator UI/API; only the CRUD child is detached."""
    import uvicorn
    app.state.queue_port=queue_port
    if app.state.llm_enabled:
        from prompt_assets import check_assets,check_runtime
        check_assets();check_runtime()
    # Fail before lifespan recovery when another listener already owns the port.
    import socket
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:probe.bind(('127.0.0.1',port))
        except OSError:raise SystemExit(f'Port {port} is occupied; select another port or inspect the existing service.') from None
    # Queue process has its own session and survives this generator's Ctrl-C.
    from queue_manage import ensure_queue
    manager._ready()
    ensure_queue(queue_port)
    uvicorn.run(app,host='127.0.0.1',port=port,workers=1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=DEFAULT_PORT)
    parser.add_argument('--no-llm',action='store_true',help='Disable the local prompt LLM (enabled by default)')
    parser.add_argument('--model',choices=__import__('backend_selection').BACKENDS,help='Saved default for omitted model; all ready models remain available')
    parser.add_argument('--queue-port',type=int,default=DEFAULT_QUEUE_PORT,help='Independent SQLite CRUD API port')
    args = parser.parse_args()
    app.state.llm_enabled=not args.no_llm
    app.state.requested_default=args.model
    run_server(args.port,args.queue_port)
