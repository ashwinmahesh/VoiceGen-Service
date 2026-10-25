"""Storage and service profile. Import before model libraries."""
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PROFILE = json.loads((PROJECT_ROOT / 'service.json').read_text())
MODEL_SPECS = json.loads((PROJECT_ROOT / 'model_specs.json').read_text())
EXTENSION_SPECS = json.loads((PROJECT_ROOT / 'extension_specs.json').read_text()) if (PROJECT_ROOT/'extension_specs.json').is_file() else {}
LOCAL_STORAGE = json.loads((PROJECT_ROOT/'storage.json').read_text()) if (PROJECT_ROOT/'storage.json').is_file() else {}
ROOT = Path(os.environ.get('DATA_DIR', LOCAL_STORAGE.get('data_dir', str(PROJECT_ROOT)))).expanduser().absolute()
OUTPUT_DIR = Path(os.environ.get('OUTPUT_DIR', str(ROOT / 'outputs'))).expanduser().absolute()
MODEL_DIR = ROOT / 'weights'
EXTENSION_DIR = ROOT / 'extensions'
DEFAULT_PORT = int(os.environ.get('PORT', PROFILE['port']))
GPU_LOCK_FILE = Path(os.environ.get('GPU_LOCK_FILE', LOCAL_STORAGE.get('gpu_lock_file', str(PROJECT_ROOT.parent / '.voice-studio' / 'gpu.lock'))))
# Explicitly keep this process's caches with its configured data root.
for key, value in {'HF_HOME':ROOT/'cache/huggingface','XDG_CACHE_HOME':ROOT/'cache',
                   'TORCH_HOME':ROOT/'cache/torch','TMPDIR':ROOT/'cache/tmp'}.items():
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
    for path in [ROOT, OUTPUT_DIR, GPU_LOCK_FILE.parent] + [ROOT/name for name in ['weights','extensions','cache','logs','outputs','voices','references','jobs','artifact-index','idempotency']]:
        validate_mount(path)
    for name in ['outputs','voices','references/originals','cache/tmp','logs','jobs','artifact-index','idempotency','extensions']:
        (ROOT/name).mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    GPU_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
