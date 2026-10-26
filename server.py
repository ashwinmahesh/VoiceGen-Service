"""Shared Voice Studio v2 API. Service-specific behavior is in service.json."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4
from config import ROOT, PROJECT_ROOT, PROFILE, DEFAULT_PORT, prepare_storage
from engine import create_engine
from assets import all_errors
from runtime_checks import runtime_errors
from capabilities import capabilities, validate_controls
from jobs import JobManager, BusyError
import storage
import pause_edits
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from schemas import Generation, Health, Capabilities, Job, Artifact, ArtifactPage, Reference, Unloaded, TagsPage, PauseAdjustment, QueuePage, QueueRemoval
from starlette.middleware.trustedhost import TrustedHostMiddleware
from body_limit import BodyLimit
import voice_catalog
from voice_catalog import SaveVoice, VoiceUpdate, Voice, VoicePage

engine = create_engine()
manager = JobManager(engine, db_path=PROJECT_ROOT/'state/queue.sqlite3')


@asynccontextmanager
async def lifespan(app):
    prepare_storage()
    from license_notices import warn_at_startup
    warn_at_startup(PROFILE['backend'])
    from data_owner import own_data_directory
    with own_data_directory(ROOT) as owner:
        manager.data_owner = owner
        manager.recover()
        try:
            yield
        finally:
            await asyncio.to_thread(manager.close)
            manager.data_owner = None


app = FastAPI(title='Local Voice Studio API',version='2.1.0',lifespan=lifespan)
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


@app.get('/',include_in_schema=False)
@app.get('/collection',include_in_schema=False)
@app.get('/collection/',include_in_schema=False)
def index(): return FileResponse(PROJECT_ROOT/'ui/index.html')


@app.get('/assets/app.js',include_in_schema=False)
def app_script(): return FileResponse(PROJECT_ROOT/'ui/app.js',media_type='text/javascript')


@app.get('/api/capabilities',response_model=Capabilities)
def get_capabilities(): return capabilities()


@app.get('/health',response_model=Health)
def health():
    errors = all_errors()
    runtime = runtime_errors()
    status = manager.stage
    if errors or runtime: status = 'error'
    elif not manager.active and not manager.unloading and not manager.last_error:
        status = 'ready' if engine.model is not None else 'unloaded'
    return Health(status=status,service=PROFILE['id'],name=PROFILE['name'],project=str(PROJECT_ROOT),
        storage=str(ROOT),downloaded=not errors,asset_errors=errors,runtime_errors=runtime,model_loaded=engine.model is not None,
        loaded_variant=engine.variant,busy=bool(manager.active or manager.unloading),
        active_job=manager.active,last_error=manager.last_error,
        queue_database=str(manager.store.path),queue_counts=manager.listing(limit=1)['counts'])


@app.post('/api/jobs',status_code=202,response_model=Job)
@app.post('/api/queue',status_code=202,response_model=Job)
@app.post('/api/generate',status_code=202,response_model=Job)
def generate(request: Generation):
    try:
        existing = manager.existing(request)
        if existing: return existing
        effective = validate_controls(request)
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
        errors = all_errors()+runtime_errors()
        if errors: raise HTTPException(503,'Incomplete setup. Run make setup for the selected MODEL. '+ '; '.join(errors))
        destination = storage.output_folder(request.output_dir)
        return manager.submit(request,effective,reference,transcript,destination,reference_info)
    except BusyError as exc:
        raise HTTPException(409,str(exc)) from None
    except (ValueError,OSError,RuntimeError) as exc:
        raise HTTPException(422,str(exc)) from None


@app.get('/api/jobs/{identity}',response_model=Job)
def job_status(identity: str):
    try: return manager.read(identity)
    except (OSError,ValueError): raise HTTPException(404,'Job not found') from None


@app.get('/api/jobs',response_model=QueuePage)
@app.get('/api/queue',response_model=QueuePage)
def queue_list(status: Literal['pending','all','queued','running','done','error','deleted']='pending',
               backend: str | None=Query(default=None,max_length=80),
               limit: int=Query(default=24,ge=1,le=100),offset: int=Query(default=0,ge=0)):
    statuses = ('queued','running') if status=='pending' else () if status=='all' else (status,)
    return manager.listing(statuses=statuses,backend=backend,limit=limit,offset=offset)


@app.get('/api/queue/{identity}',response_model=Job)
def queue_detail(identity: str):
    return job_status(identity)


@app.delete('/api/jobs',response_model=QueueRemoval)
@app.delete('/api/queue',response_model=QueueRemoval)
def queue_clear(backend: str | None=Query(default=None,max_length=80)):
    return manager.remove(backend=backend)


@app.delete('/api/jobs/{identity}',response_model=QueueRemoval)
@app.delete('/api/queue/{identity}',response_model=QueueRemoval)
def queue_delete(identity: str):
    try: return manager.remove(identity)
    except FileNotFoundError: raise HTTPException(404,'Queue entry not found') from None
    except BusyError as exc: raise HTTPException(409,str(exc)) from None


@app.post('/api/jobs/{identity}/retry',status_code=202,response_model=Job)
@app.post('/api/queue/{identity}/retry',status_code=202,response_model=Job)
def queue_retry(identity: str):
    try: return manager.retry(identity)
    except FileNotFoundError: raise HTTPException(404,'Queue entry not found') from None
    except BusyError as exc: raise HTTPException(409,str(exc)) from None


@app.get('/api/history',response_model=ArtifactPage)
def history(q: str=Query(default='',max_length=300),limit: int=Query(default=24,ge=1,le=100),
            offset: int=Query(default=0,ge=0)):
    words = voice_catalog.searchable(q).split()
    rows = [r for r in storage.artifact_rows() if all(word in voice_catalog.searchable(' '.join(
        str(r.get(k,'')) for k in ['text','description','emotion'])) for word in words)]
    total = len(rows); more = offset+limit < total
    return ArtifactPage(data=rows[offset:offset+limit],total=total,offset=offset,limit=limit,
                        has_more=more,next_offset=offset+limit if more else None)


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


@app.post('/api/references',status_code=201,response_model=Reference)
async def upload_reference(file: UploadFile=File(...), transcript: str=Form(default='',max_length=3000)):
    raw = await file.read(25*1024*1024+1)
    if len(raw)>25*1024*1024: raise HTTPException(413,'Choose a file under 25 MB')
    try: return await asyncio.to_thread(storage.preserve_reference,raw,file.filename,transcript)
    except (OSError,ValueError,RuntimeError) as exc:
        raise HTTPException(422,'Cannot use this reference: '+str(exc)) from None


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
           limit: int = Query(default=24, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return voice_catalog.search(ROOT, q, tag, sort, limit, offset)


@app.get("/api/voice-tags", response_model=TagsPage)
def voice_tags():
    counts = {}
    for voice in voice_catalog.all_voices(ROOT):
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
               (__import__("performance_controls").spoken_transcript(original) if original.get("duration",0) <= 30 else "")}
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



if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=DEFAULT_PORT)
    parser.add_argument('--backend',choices=__import__('backend_selection').BACKENDS)
    args = parser.parse_args()
    # Fail before lifespan recovery when another listener already owns the port.
    import socket
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:probe.bind(('127.0.0.1',args.port))
        except OSError:raise SystemExit(f'Port {args.port} is occupied; select another port or inspect the existing service.') from None
    uvicorn.run(app,host='127.0.0.1',port=args.port,workers=1)
