"""Standalone SQLite CRUD service. Payloads are opaque; only consumers interpret them."""
import argparse
from contextlib import asynccontextmanager
import fcntl
import sqlite3
from typing import Literal
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from body_limit import BodyLimit
from queue_status import storage_error_response
from queue_schemas import QueueMove, QueueState, ModelDescriptor, ModelList, JobProgress
from progress_estimate import take_progress
from durable_queue import QueueStore, QueueConflict
from queue_config import PROJECT_ROOT, QUEUE_DB, DEFAULT_QUEUE_PORT, DEFAULT_MODEL, MODELS
from model_readiness import ReadyModels,ModelUnavailable

store = QueueStore(PROJECT_ROOT, QUEUE_DB)
catalog=ReadyModels()


@asynccontextmanager
async def lifespan(app):
    store.path.parent.mkdir(parents=True,exist_ok=True)
    # Separate from consumer ownership: API restarts must never recover live work.
    with store.path.with_suffix('.api.lock').open('a+') as owner:
        try:
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another queue API owns this database') from None
        store.initialize()
        yield


app = FastAPI(title='VoiceGen Queue CRUD API',version='4.0.0',lifespan=lifespan)
app.add_exception_handler(sqlite3.Error,storage_error_response)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
app.add_middleware(BodyLimit)


@app.middleware('http')
async def local_writes(request: Request, call_next):
    if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('origin'):
        from urllib.parse import urlparse
        origin = urlparse(request.headers['origin'])
        if origin.scheme != 'http' or origin.netloc != request.headers.get('host'):
            return JSONResponse({'detail':'Use a same-origin local client'},status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


class Submission(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    model: str | None = Field(default=None,min_length=1,max_length=80,pattern=r'^[a-zA-Z0-9_.-]+$')
    payload: dict
    generate_immediately: StrictBool = False
    idempotency_key: str | None = Field(default=None,min_length=1,max_length=200)


class Edit(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    payload: dict | None = None
    generate_immediately: StrictBool | None = None


class Entry(BaseModel):
    position: int | None = None
    blocked_reason: str | None = None
    id: str
    sequence: int
    queue_order: int
    model: str
    payload: dict
    prepared: bool
    generate_immediately: bool
    status: Literal['queued','running','done','error','deleted']
    stage: str
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None
    attempts: int
    error: str | None
    result: dict | None
    progress: JobProgress | None = Field(default=None,description='Estimated progress while running and 100 when done; null otherwise.')


class Page(BaseModel):
    data: list[Entry]
    total: int
    counts: dict[str,int]
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None


class Removal(BaseModel):
    deleted: int


class QueueHealth(BaseModel):
    api_version: str = '4.0.0'
    queue_scope: Literal['shared'] = 'shared'
    paused: bool = False
    service: Literal['voicegen-queue']
    role: Literal['queue-crud']
    project: str
    queue_database: str
    status: Literal['ready']
    default_model: str



def public(row):
    reason=None
    if row['status']=='queued':
        errors=catalog.errors(row['backend'])
        if errors:reason=f"{row['backend']} is not ready: {errors[0]} Run make setup MODEL={row['backend']}."
    return {key:row[key] for key in ('id','sequence','queue_order','status','stage','created_at','updated_at',
            'started_at','completed_at','attempts','generate_immediately','error','result')} | {
        'model':row['backend'],
        'blocked_reason':reason,'position':row.get('position'),'progress':take_progress(row['status'],row.get('progress')),
        'payload':row['payload'].get('queue_request',row['payload'].get('queue_submission',row['payload'])),
        'prepared':row['payload'].get('queue_format') != 'opaque-v1'}


@app.exception_handler(QueueConflict)
async def conflict(request, exc):
    return JSONResponse({'detail':str(exc)},status_code=409)


@app.exception_handler(RequestValidationError)
async def invalid(request, exc):
    return JSONResponse({'detail':[{key:error[key] for key in ('loc','msg','type')}
                                  for error in exc.errors()]},status_code=422)


@app.exception_handler(FileNotFoundError)
async def missing(request, exc):
    return JSONResponse({'detail':'Queue entry not found'},status_code=404)


@app.get('/health',response_model=QueueHealth)
def health():
    return {'service':'voicegen-queue','role':'queue-crud','project':str(PROJECT_ROOT),
            'queue_database':str(store.path),'status':'ready','default_model':store.default_model(),'paused':store.setting('paused',False)}


@app.get('/v1/models',response_model=ModelList,
         description='List only models passing read-only setup checks. Never loads model weights.')
def list_models():
    default=store.default_model()
    return {'default_model':default,'data':[{**model,'is_default':model['id']==default} for model in catalog.models()]}


@app.post('/api/queue',status_code=202,response_model=Entry)
def submit(request: Submission):
    try:
        old=store.existing_opaque(request.payload,request.generate_immediately,request.idempotency_key,request.model)
        if old:return public(old)
        model=request.model or store.default_model()
        catalog.require(model,force=True)
        return public(store.admit_opaque(model,request.payload,
                                        request.generate_immediately,request.idempotency_key))
    except ModelUnavailable as error:raise HTTPException(422,str(error)) from None
    except (TypeError,ValueError) as exc:
        if isinstance(exc,QueueConflict):
            old=store.existing_opaque(request.payload,request.generate_immediately,request.idempotency_key,request.model)
            if old:return public(old)
            raise
        raise HTTPException(422,'Payload must be finite JSON') from None


@app.post('/api/queue/pause',response_model=QueueState)
def pause():return store.pause(True)


@app.post('/api/queue/resume',response_model=QueueState)
def resume():return store.pause(False)


@app.get('/api/queue',response_model=Page)
def listing(status: Literal['pending','all','queued','running','done','error','deleted']='pending',
            model: str | None=Query(default=None,max_length=80),
            limit: int=Query(default=24,ge=1,le=100),offset: int=Query(default=0,ge=0)):
    statuses = ('queued','running') if status=='pending' else () if status=='all' else (status,)
    page = store.listing(statuses=statuses,backend=model,limit=limit,offset=offset)
    page['data'] = [public(row) for row in page['data']]
    return page


@app.get('/api/queue/{identity}',response_model=Entry)
def detail(identity: str):
    return public(store.read(identity))


@app.patch('/api/queue/{identity}',response_model=Entry)
def update(identity: str, request: Edit):
    if not request.model_fields_set or any(getattr(request,key) is None for key in request.model_fields_set):
        raise HTTPException(422,'Supply payload and/or generate_immediately with non-null values')
    try:
        return public(store.edit_pending(identity,request.payload,request.generate_immediately))
    except (TypeError,ValueError) as exc:
        if isinstance(exc,QueueConflict):raise
        raise HTTPException(422,'Payload must be finite JSON') from None


@app.post('/api/queue/{identity}/move',response_model=Entry)
def move(identity: str, request: QueueMove):
    try:return public(store.move(identity,**request.model_dump(exclude_none=True)))
    except QueueConflict:raise
    except ValueError as exc:raise HTTPException(422,str(exc)) from None


@app.post('/api/queue/{identity}/run-now',response_model=Entry)
def run_now(identity: str):
    return public(store.run_now(identity))


@app.delete('/api/queue',response_model=Removal)
def clear(model: str | None=Query(default=None,max_length=80)):
    return {'deleted':store.remove(backend=model)}


@app.delete('/api/queue/{identity}',response_model=Removal)
def remove(identity: str):
    return {'deleted':store.remove(identity)}


if __name__ == '__main__':
    import uvicorn
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=DEFAULT_QUEUE_PORT)
    args=parser.parse_args()
    uvicorn.run(app,host='127.0.0.1',port=args.port,workers=1)
