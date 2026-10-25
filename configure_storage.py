"""Persist optional external storage without moving or replacing existing data."""
import argparse
import json
import os
from pathlib import Path
import sys
from config import ROOT, PROJECT_ROOT, GPU_LOCK_FILE, validate_mount

DATA_NAMES = ('weights', 'extensions', 'cache', 'logs', 'outputs', 'voices',
              'references', 'jobs', 'artifact-index', 'idempotency')


def configure(project, data, gpu_lock):
    project = Path(project).resolve()
    data = validate_mount(data)
    validate_mount(Path(gpu_lock).parent)
    if data != project:
        # Check every collision before creating links or changing settings.
        for name in DATA_NAMES:
            local = project/name
            if local.is_symlink():
                if local.resolve() != data/name:
                    raise ValueError(f'{local} links elsewhere; preserve/migrate it explicitly first')
            elif local.exists():
                raise ValueError(f'{local} already exists; preserve/migrate it explicitly first')
        data.mkdir(parents=True, exist_ok=True)
        for name in DATA_NAMES:
            (data/name).mkdir(parents=True, exist_ok=True)
            if not (project/name).is_symlink():
                (project/name).symlink_to(data/name, target_is_directory=True)
    else:
        # Existing external links must fail clearly while their volume is absent.
        for name in DATA_NAMES:
            validate_mount(project/name)
    settings = {'data_dir': str(data), 'gpu_lock_file': str(Path(gpu_lock).absolute())}
    temporary = project/'storage.json.tmp'
    temporary.write_text(json.dumps(settings, indent=2)+'\n')
    temporary.replace(project/'storage.json')
    return settings


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', help='Absolute model/data folder; skips the first-use question')
    parser.add_argument('--non-interactive', action='store_true', help='Use remembered storage or the project default')
    args = parser.parse_args()
    try:
        selected = args.data_dir or str(ROOT)
        first_use = not (PROJECT_ROOT/'storage.json').is_file()
        explicit = args.data_dir is not None or 'DATA_DIR' in os.environ
        if first_use and not explicit and not args.non_interactive and sys.stdin.isatty():
            answer = input(f'Storage folder for models and recordings [{PROJECT_ROOT}]: ').strip()
            selected = answer or str(PROJECT_ROOT)
        data = Path(selected).expanduser()
        if not data.is_absolute():
            raise ValueError('Choose an absolute storage folder, or press Enter for this project')
        print(json.dumps(configure(PROJECT_ROOT, data, GPU_LOCK_FILE), indent=2))
    except (ValueError, OSError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
