"""Shared, read-only setup discovery. No inference, model loads or downloads."""
import json
import os
from pathlib import Path
import threading
import time

from backend_selection import BACKENDS
from startup_select import setup_errors, runtime_python

PROJECT=Path(__file__).resolve().parent


class ModelUnavailable(ValueError):pass


class ReadyModels:
    def __init__(self,root=PROJECT,probe=setup_errors,ttl=5):
        self.root=Path(root);self.probe=probe;self.ttl=ttl
        self.original=json.loads((self.root/'service.json').read_text())['backend']
        self.metadata=json.loads((self.root/'model_registry.json').read_text())
        self.metadata=[{**row,'modes':list(json.loads((self.root/'backends'/row['id']/'service.json').read_text()).get('modes',{}))} for row in self.metadata]
        self.cache={};self.lock=threading.RLock()
        self.override_backend=os.environ.get('VOICEGEN_OVERRIDE_BACKEND',os.environ.get('MODEL_BACKEND'))

    def venv(self,model):return os.environ.get('VENV') if model==self.override_backend else None

    def errors(self,model,force=False):
        if model not in BACKENDS:return ['Unknown model. Run make setup to choose a supported voice model.']
        with self.lock:
            previous=self.cache.get(model)
            if not force and previous and time.monotonic()-previous[0]<self.ttl:return list(previous[1])
            errors=self.probe(self.root,model,self.original,self.venv(model))
            self.cache[model]=(time.monotonic(),list(errors))
            return list(errors)

    def require(self,model,force=False):
        errors=self.errors(model,force)
        if errors:raise ModelUnavailable(f'{model} is not ready: {errors[0]} Run make setup MODEL={model}; reconnect its storage if needed.')
        return model

    def models(self,force=False):
        return [dict(row) for row in self.metadata if not self.errors(row['id'],force)]

    def unavailable(self):
        return {row['id']:self.errors(row['id']) for row in self.metadata if self.errors(row['id'])}

    def snapshot(self):
        """Health reads the latest probe results without spawning subprocesses."""
        cache=self.cache.copy()
        ready=[dict(row) for row in self.metadata if row['id'] in cache and not cache[row['id']][1]]
        unavailable={row['id']:list(cache[row['id']][1]) for row in self.metadata if row['id'] in cache and cache[row['id']][1]}
        return ready,unavailable

    def python(self,model):return runtime_python(self.root,model,self.original,self.venv(model))
