"""Explicitly copy/check only common source. Never touches models, config, or user data."""
import argparse
from pathlib import Path
import shutil
from config import PROJECT_ROOT

SHARED = ['AGENTS.md','VOICE_QUALITY.md','tests/test_inference_locks.py','inference_locks.py','durable_queue.py','tests/test_queue.py','pause_edits.py','tests/test_pause_edits.py','config.py','assets.py','download.py','schemas.py','capabilities.py','engine.py','storage.py',
          'jobs.py','body_limit.py','server.py','voice_catalog.py','preflight.py','manage.py','Makefile',
          '.gitignore','clean_env.py','requirements.txt','requirements.lock.txt','tests/smoke_test.py',
          'sync_source.py','storage.json.example','configure_storage.py','tests/test_storage_configuration.py','directions.py','emotion_steering.py','extension_assets.py','tests/test_extensions.py','tests/test_contract.py','ui/index.html','ui/app.js','API.md','EXTENSIONS.md','THIRD_PARTY_NOTICES.md']
SHARED += ['punctuation_pacing.py','tests/test_punctuation_pacing.py','performance_controls.py','tests/test_performance_controls.py','license_notices.py','startup_select.py','.gitattributes','backend_selection.py','torch_engine.py','native_engine.py','fish_loader.py','runtime_setup.py','runtime_checks.py','data_owner.py','tests/test_backends.py','BACKENDS.md']
SHARED += ['prompt_assets.py','prompt_expansion.py','prompt_policy.py','prompt_profiles.py','prompt_worker.py','setup_prompt_llm.py','requirements.enhancer.lock.txt','tests/test_prompt_expansion.py','docs/voice-prompt-expansion.md']
SHARED += ['candidate_diversity.py','candidate_policy.py','tests/test_candidate_diversity.py']
SHARED += ['queue_config.py','queue_server.py','queue_manage.py','queue_service.json','service_defaults.json','model_registry.json','requirements-queue.lock.txt','tests/test_queue_service.py']
SHARED += ['queue_status.py','queue_schemas.py','docs/api-behavior.md','tests/__init__.py','tests/test_api.py','tests/test_queue_order.py']
SHARED += ['bootstrap_uv.sh','bootstrap_python.sh','python_runtime.py','tests/test_python_runtime.py']
SHARED += ['setup_lifecycle.py','tests/test_setup_lifecycle.py']
SHARED += ['backend_protocol.py','backend_worker.py','runtime_engine.py','model_readiness.py','tests/test_runtime_models.py']
SHARED += ['resource_probe.py','resource_schemas.py','observability.py','tests/test_observability.py','ui/observability.html','ui/observability.css','ui/observability.js','docs/observability.md']
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
