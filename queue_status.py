"""Bounded queue HTTP diagnostics and consistent SQLite failure responses."""
import logging
from pathlib import Path
from fastapi.responses import JSONResponse
from queue_config import PROJECT_ROOT
from queue_manage import health

STORAGE_ERROR = ('Queue storage is temporarily unavailable. Check the project state directory and free disk space, '
                 'then retry with the same idempotency key.')


def api_status(port, database):
    url=f'http://127.0.0.1:{port}'
    state=health(port,timeout=.3)
    if state is None:
        status='unavailable'
        message=(f'Queue API is unavailable at {url}. Start it with make queue QUEUE_PORT={port}. '
                 'Studio requests and saved jobs can continue through SQLite.')
    elif not (isinstance(state,dict) and state.get('service')=='voicegen-queue' and state.get('role')=='queue-crud'
              and state.get('project')==str(PROJECT_ROOT) and state.get('queue_database')==str(Path(database).resolve())):
        status='mismatch'
        message=(f'The service at {url} does not match this project\'s queue API. Inspect that listener before restarting the queue. '
                 'Studio requests and saved jobs can continue through SQLite.')
    elif state.get('status')!='ready':
        status='unavailable'
        message=f'Queue API at {url} is not ready. Check its logs. Studio requests and saved jobs can continue through SQLite.'
    else:
        status='ready';message=None
    return {'status':status,'url':url,'message':message}


async def storage_error_response(request, exc):
    logging.warning('Queue database request failed: %s',exc)
    return JSONResponse({'detail':STORAGE_ERROR,'code':'queue_storage_unavailable'},
                        status_code=503,headers={'Retry-After':'2'})
