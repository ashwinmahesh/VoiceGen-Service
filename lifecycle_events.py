"""Low-volume model boundary events; callbacks never control inference success."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import os
import threading
import time
from uuid import uuid4

_observer = ContextVar('model_lifecycle_observer', default=None)
_reason = ContextVar('model_lifecycle_reason', default=None)


def emit(event):
    callback = _observer.get()
    if callback:
        try: callback(event)
        except Exception: logging.warning('Could not record model lifecycle event', exc_info=True)


def current_reason(): return _reason.get()


@contextmanager
def observe(callback):
    token = _observer.set(callback)
    try: yield
    finally: _observer.reset(token)


@contextmanager
def reason(value):
    token = _reason.set(value)
    try: yield
    finally: _reason.reset(token)


@contextmanager
def measure(action, scope, backend, model, why=None):
    event = dict(id=uuid4().hex, action=action, scope=scope, backend=backend, model=model,
                 reason=why or _reason.get() or ('first_use' if action == 'load' else 'memory_release'),
                 started_at=time.time(), ended_at=None, duration_seconds=None, status='running')
    emit(event.copy())
    # Persisting the start boundary can wait briefly for SQLite. Do not charge
    # that observer work to the model constructor/destructor being measured.
    started = time.monotonic()
    try:
        yield
    except BaseException:
        event['status'] = 'error'
        raise
    else: event['status'] = 'success'
    finally:
        event.update(ended_at=time.time(), duration_seconds=max(0., time.monotonic()-started))
        emit(event)


@contextmanager
def event_pipe(backend=None, model=None, on_event=None):
    """A private inherited event FD for short-lived native/LLM children."""
    callback = _observer.get()
    read_fd, write_fd = os.pipe()
    identities = {}
    def read_events():
        with os.fdopen(read_fd, 'rb') as stream:
            while line := stream.readline(4097):
                if len(line) > 4096: continue
                try:
                    event = json.loads(line)
                    if backend is not None:
                        event.update(backend=backend,model=model)
                        event['id']=identities.setdefault(event['id'],uuid4().hex)
                    if on_event: on_event(event)
                    if callback: callback(event)
                except Exception: logging.warning('Could not read a child model lifecycle event')
    thread = threading.Thread(target=read_events, name='model-lifecycle-events', daemon=True)
    thread.start()
    try: yield write_fd
    finally:
        os.close(write_fd)
        thread.join(timeout=2)


def pipe_emitter():
    descriptor = os.environ.get('VOICEGEN_LIFECYCLE_FD')
    def report(event):
        if descriptor is not None:
            os.write(int(descriptor), (json.dumps(event, allow_nan=False)+'\n').encode())
    return report
