"""Storage and service profile. Import before model libraries."""
import json
import os
from pathlib import Path
from backend_selection import selected_backend, weights_directory
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND = selected_backend()
ORIGINAL_PROFILE = json.loads((PROJECT_ROOT/'service.json').read_text())
PROFILE_ROOT = PROJECT_ROOT/'backends'/BACKEND if BACKEND else PROJECT_ROOT
PROFILE = json.loads((PROFILE_ROOT / 'service.json').read_text())
MODEL_SPECS = json.loads((PROFILE_ROOT / 'model_specs.json').read_text())
EXTENSION_SPECS = json.loads((PROFILE_ROOT / 'extension_specs.json').read_text()) if (PROFILE_ROOT/'extension_specs.json').is_file() else {}
LOCAL_STORAGE = json.loads((PROJECT_ROOT/'storage.json').read_text()) if (PROJECT_ROOT/'storage.json').is_file() else {}
ROOT = Path(os.environ.get('DATA_DIR', LOCAL_STORAGE.get('data_dir', str(PROJECT_ROOT)))).expanduser().absolute()
OUTPUT_DIR = Path(os.environ.get('OUTPUT_DIR', str(ROOT / 'outputs'))).expanduser().absolute()
MODEL_DIR = weights_directory(ROOT,BACKEND,ORIGINAL_PROFILE['backend'],
    os.environ.get('WEIGHTS_DIR',LOCAL_STORAGE.get('backend_weights',{}).get(PROFILE['backend'])))
EXTENSION_DIR = Path(os.environ.get('EXTENSIONS_DIR',LOCAL_STORAGE.get('backend_extensions',{}).get(PROFILE['backend'],str(ROOT/'extensions')))).expanduser()
if not EXTENSION_DIR.is_absolute():raise ValueError('EXTENSIONS_DIR/backend_extensions must be an absolute directory')
DEFAULT_PORT = int(os.environ.get('PORT', PROFILE['port']))
GPU_LOCK_FILE = Path(os.environ.get('GPU_LOCK_FILE', LOCAL_STORAGE.get('gpu_lock_file', str(PROJECT_ROOT.parent / '.voice-studio' / 'gpu.lock'))))

# Requests and the single inference executor bind their own metadata. Runtime
# libraries remain isolated in the selected backend subprocess/environment.
_selected_model = ContextVar('voice_model',default=BACKEND)
OVERRIDE_BACKEND = os.environ.get('VOICEGEN_OVERRIDE_BACKEND',BACKEND)


@lru_cache(maxsize=16)
def backend_values(backend):
    folder=PROJECT_ROOT/'backends'/backend
    profile=json.loads((folder/'service.json').read_text())
    specs=json.loads((folder/'model_specs.json').read_text())
    extensions=json.loads((folder/'extension_specs.json').read_text()) if (folder/'extension_specs.json').is_file() else {}
    weights=weights_directory(ROOT,backend,ORIGINAL_PROFILE['backend'],
        os.environ.get('WEIGHTS_DIR') if backend==OVERRIDE_BACKEND and os.environ.get('WEIGHTS_DIR') else LOCAL_STORAGE.get('backend_weights',{}).get(backend))
    extension=Path(os.environ.get('EXTENSIONS_DIR') if backend==OVERRIDE_BACKEND and os.environ.get('EXTENSIONS_DIR') else LOCAL_STORAGE.get('backend_extensions',{}).get(backend,str(ROOT/'extensions'))).expanduser()
    return dict(profile=profile,specs=specs,extensions=extensions,weights=weights,extension_dir=extension)


@contextmanager
def model_context(backend):
    from backend_selection import BACKENDS
    if backend not in BACKENDS:raise ValueError('Unknown voice model: '+str(backend))
    token=_selected_model.set(backend)
    try:yield backend_values(backend)
    finally:_selected_model.reset(token)


class _ModelMapping(Mapping):
    def __init__(self,key):self.key=key
    def value(self):return backend_values(_selected_model.get())[self.key]
    def __getitem__(self,key):return self.value()[key]
    def __iter__(self):return iter(self.value())
    def __len__(self):return len(self.value())
    def copy(self):return self.value().copy()
    def __deepcopy__(self,memo):
        from copy import deepcopy
        return deepcopy(self.value(),memo)


class _ModelPath(os.PathLike):
    def __init__(self,key):self.key=key
    def value(self):return backend_values(_selected_model.get())[self.key]
    def __fspath__(self):return str(self.value())
    def __str__(self):return str(self.value())
    def __truediv__(self,other):return self.value()/other
    def __getattr__(self,key):return getattr(self.value(),key)


PROFILE,MODEL_SPECS,EXTENSION_SPECS=(_ModelMapping(key) for key in ('profile','specs','extensions'))
MODEL_DIR,EXTENSION_DIR=(_ModelPath(key) for key in ('weights','extension_dir'))
# Explicitly keep this process's caches with its configured data root.
for key, value in {'HF_HOME':ROOT/'cache/huggingface','XDG_CACHE_HOME':ROOT/'cache',
                   'TORCH_HOME':ROOT/'cache/torch','TMPDIR':ROOT/'cache/tmp',
                   'HF_HUB_CACHE':ROOT/'cache/huggingface/hub',
                   'NUMBA_CACHE_DIR':ROOT/'cache/numba','NLTK_DATA':ROOT/'cache/nltk'}.items():
    os.environ[key] = str(value)
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['HF_HUB_DISABLE_XET'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'


def validate_mount(path):
    path = Path(path).expanduser().resolve()
    parts = path.parts
    if len(parts) >= 3 and parts[1] == 'Volumes' and not Path('/Volumes', parts[2]).is_mount():
        raise RuntimeError(f'Storage volume is not mounted: /Volumes/{parts[2]}')
    return path


def prepare_storage():
    for path in [ROOT, MODEL_DIR, EXTENSION_DIR, OUTPUT_DIR, GPU_LOCK_FILE.parent] + [ROOT/name for name in ['weights','extensions','cache','logs','outputs','voices','references','jobs','artifact-index','idempotency']]:
        validate_mount(path)
    for name in ['outputs','voices','references/originals','cache/tmp','logs','jobs','artifact-index','idempotency','extensions']:
        (ROOT/name).mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    GPU_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
