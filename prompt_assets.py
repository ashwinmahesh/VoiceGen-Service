"""Pinned optional text LLM assets, independent of every speech runtime."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from config import ROOT, PROJECT_ROOT, LOCAL_STORAGE, validate_mount

REPOSITORY='mlx-community/Qwen3.5-2B-4bit'
REVISION='674aaa7240b91e8012fcad5d791b7dfe5ba90207'
WEIGHTS_SHA256='713fe7e5d3c3965f7106b0d0ee17615f7869c23c8d327996df8c1196fbcf07d5'
WEIGHTS_BYTES=1722271785
FILES=('config.json','model.safetensors','model.safetensors.index.json','tokenizer.json',
       'tokenizer_config.json','chat_template.jinja','vocab.json')


def weights_directory():
    path=Path(os.environ.get('PROMPT_LLM_DIR',LOCAL_STORAGE.get('prompt_llm_dir',str(ROOT/'prompt-enhancer/weights')))).expanduser()
    if not path.is_absolute():raise ValueError('PROMPT_LLM_DIR must be absolute')
    return validate_mount(path)


def runtime_python():
    return PROJECT_ROOT/'.venv-enhancer/bin/python'


def check_assets(full=False):
    folder=weights_directory()
    if any(not (folder/name).is_file() or not (folder/name).stat().st_size for name in (*FILES,'download.json')):
        raise RuntimeError('Prompt LLM assets missing; run make setup-llm or make download-llm')
    if (folder/'model.safetensors').stat().st_size!=WEIGHTS_BYTES:
        raise RuntimeError('Prompt LLM weight size differs from the pinned checkpoint; repair its assets')
    for name in ('config.json','tokenizer_config.json','model.safetensors.index.json'):
        json.loads((folder/name).read_text())
    record=json.loads((folder/'download.json').read_text())
    if record.get('repository')!=REPOSITORY or record.get('revision')!=REVISION or record.get('weights_sha256')!=WEIGHTS_SHA256:
        raise RuntimeError('Prompt LLM provenance differs from the pinned model; inspect it before setup')
    if full:
        digest=hashlib.sha256()
        with (folder/'model.safetensors').open('rb') as stream:
            while chunk:=stream.read(8*1024*1024):digest.update(chunk)
        if digest.hexdigest()!=WEIGHTS_SHA256:raise RuntimeError('Prompt LLM checksum mismatch; repair its assets')
    return folder


def check_runtime():
    python=runtime_python()
    if not python.is_file():raise RuntimeError('Prompt LLM runtime missing; run make setup-llm')
    probe="import importlib.metadata as m; assert m.version('mlx-lm')=='0.32.2'; assert m.version('mlx')=='0.32.4'"
    result=subprocess.run([str(python),'-c',probe],capture_output=True,timeout=15)
    if result.returncode:raise RuntimeError('Prompt LLM runtime differs from its lock; run make setup-llm')
    return python


def download():
    folder=weights_directory()
    if (folder/'download.json').exists():
        check_assets(full=True);print('Reusing shared prompt LLM:',folder);return
    folder.mkdir(parents=True,exist_ok=True)
    os.environ['HF_HUB_OFFLINE']='0';os.environ['TRANSFORMERS_OFFLINE']='0'
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=REPOSITORY,revision=REVISION,local_dir=str(folder),allow_patterns=list(FILES),max_workers=2)
    digest=hashlib.sha256()
    with (folder/'model.safetensors').open('rb') as stream:
        while chunk:=stream.read(8*1024*1024):digest.update(chunk)
    if digest.hexdigest()!=WEIGHTS_SHA256:raise RuntimeError('Prompt LLM checksum mismatch; no completion marker written')
    if any(not (folder/name).is_file() or not (folder/name).stat().st_size for name in FILES):raise RuntimeError('Prompt LLM download is incomplete')
    for name in ('config.json','tokenizer_config.json','model.safetensors.index.json'):json.loads((folder/name).read_text())
    from uuid import uuid4
    temporary=folder/f'download-{uuid4().hex}.tmp'
    temporary.write_text(json.dumps({'repository':REPOSITORY,'revision':REVISION,'license':'Apache-2.0','weights_sha256':WEIGHTS_SHA256})+'\n')
    temporary.replace(folder/'download.json')
    print('Prompt LLM ready:',folder)


if __name__=='__main__':download()
