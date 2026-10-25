"""Resume only pinned, required files and verify before marking complete."""
import os
from config import MODEL_DIR, MODEL_SPECS, prepare_storage
from assets import verify_assets


def download_models(downloader=None):
    prepare_storage()
    if downloader is None:
        os.environ['HF_HUB_OFFLINE'] = '0'
        os.environ['TRANSFORMERS_OFFLINE'] = '0'
        from huggingface_hub import snapshot_download
        downloader = snapshot_download
    for key, spec in MODEL_SPECS.items():
        folder = MODEL_DIR/spec['subdir']
        folder.mkdir(parents=True, exist_ok=True)
        if verify_assets(key):
            downloader(repo_id=spec['repo'], revision=spec['revision'], local_dir=str(folder),
                       allow_patterns=[item['path'] for item in spec['files']], max_workers=2)
        from clean_env import clean_appledouble
        clean_appledouble(folder)
        errors = verify_assets(key, full=True)
        if errors:
            (folder/'verified.json').unlink(missing_ok=True)
            for item in spec['files']:
                if any(error.startswith(f'{key}/{item["path"]}:') for error in errors):
                    (folder/item['path']).unlink(missing_ok=True)
            raise RuntimeError('\n'.join(errors)+'\nRun make download to resume/repair.')
        print(f'Verified {key}: {spec["repo"]}@{spec["revision"]}', flush=True)


if __name__ == '__main__':
    download_models()
    from extension_assets import download_extensions
    download_extensions()
