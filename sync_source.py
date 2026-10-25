"""Explicitly copy/check only common source. Never touches models, config, or user data."""
import argparse
from pathlib import Path
import shutil
from config import PROJECT_ROOT

SHARED = ['VOICE_QUALITY.md','test_inference_locks.py','inference_locks.py','durable_queue.py','test_queue.py','pause_edits.py','test_pause_edits.py','config.py','assets.py','download.py','schemas.py','capabilities.py','engine.py','storage.py',
          'jobs.py','body_limit.py','server.py','voice_catalog.py','preflight.py','manage.py','Makefile',
          '.gitignore','clean_env.py','requirements.txt','requirements.lock.txt','smoke_test.py',
          'sync_source.py','storage.json.example','configure_storage.py','test_storage_configuration.py','directions.py','emotion_steering.py','extension_assets.py','test_extensions.py','test_contract.py','ui/index.html','ui/app.js','API.md','EXTENSIONS.md','THIRD_PARTY_NOTICES.md']
SHARED += ['performance_controls.py','test_performance_controls.py','license_notices.py','startup_select.py','.gitattributes','backend_selection.py','torch_engine.py','native_engine.py','fish_loader.py','runtime_setup.py','runtime_checks.py','data_owner.py','test_backends.py','BACKENDS.md']
SHARED += [str(path.relative_to(PROJECT_ROOT)) for folder in ('backends','third_party')
           for path in sorted((PROJECT_ROOT/folder).rglob('*')) if path.is_file()]

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true')
    parser.add_argument('peer',type=Path)
    args=parser.parse_args()
    if not (args.peer/'service.json').is_file():raise SystemExit('Peer service.json is required')
    differences=[]
    for name in SHARED:
        source,target=PROJECT_ROOT/name,args.peer/name
        if args.check:
            if not target.is_file() or source.read_bytes()!=target.read_bytes():differences.append(name)
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
    print('Differences: '+', '.join(differences) if differences else 'Shared source matches.' if args.check else 'Shared source copied.')
    raise SystemExit(bool(differences))
