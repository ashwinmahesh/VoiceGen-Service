"""Queue-only paths and port. No model configuration or external assets required."""
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
STATE_DIR = PROJECT_ROOT/'state'
DEFAULT_MODEL = json.loads((PROJECT_ROOT/'service_defaults.json').read_text())['model']
DEFAULT_QUEUE_PORT = int(os.environ.get('QUEUE_PORT', json.loads((PROJECT_ROOT/'queue_service.json').read_text())['port']))
QUEUE_DB = Path(os.environ.get('QUEUE_DB', str(STATE_DIR/'queue.sqlite3'))).expanduser().absolute()
if not QUEUE_DB.resolve().is_relative_to(STATE_DIR.resolve()) or STATE_DIR.is_symlink():
    raise ValueError('QUEUE_DB must be inside the serving project state/ directory, not on model storage')


def check_legacy_queue():
    """Avoid creating an empty competing DB before a legacy generator migration."""
    settings = PROJECT_ROOT/'storage.json'
    root = Path(os.environ.get('DATA_DIR', json.loads(settings.read_text()).get('data_dir',str(PROJECT_ROOT))
                              if settings.is_file() else str(PROJECT_ROOT)))
    legacy = root/'jobs/queue.sqlite3'
    # Explicit test/isolated queues do not migrate production state.
    if QUEUE_DB == STATE_DIR/'queue.sqlite3' and legacy.exists():
        raise RuntimeError('Legacy queue found. Run the generator once to migrate it before starting make queue; no database was replaced.')
