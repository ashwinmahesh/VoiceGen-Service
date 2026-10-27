"""Small CPU-only transcriber: local build, external model storage, no serving downloads."""
import hashlib
import json
import os
from pathlib import Path
from config import PROJECT_ROOT, ROOT, LOCAL_STORAGE, validate_mount

SPEC=json.loads((PROJECT_ROOT/'transcription_specs.json').read_text())
SOURCE=PROJECT_ROOT/'.runtime-source/whisper.cpp'
BINARY=SOURCE/'build/bin/whisper-cli'
BUILD_RECORD=SOURCE/'build/voicegen-transcription.json'


def model_directory():
    folder=Path(os.environ.get('TRANSCRIPTION_MODEL_DIR',LOCAL_STORAGE.get('transcription_model_dir',str(ROOT/'transcription')))).expanduser()
    if not folder.is_absolute():raise ValueError('TRANSCRIPTION_MODEL_DIR must be absolute')
    return validate_mount(folder)


def checksum(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(4*1024*1024):digest.update(chunk)
    return digest.hexdigest()


def check_ready(full=False):
    folder=model_directory();model=folder/SPEC['filename']
    message='Automatic transcription is not set up. Run make setup-transcriber, supply a transcript, or turn automatic transcription off.'
    try:
        build=json.loads(BUILD_RECORD.read_text());download=json.loads((folder/'verified.json').read_text())
        if not BINARY.is_file() or not os.access(BINARY,os.X_OK):raise ValueError()
        if build.get('revision')!=SPEC['source_revision'] or build.get('cpu_only') is not True:raise ValueError()
        if download.get('revision')!=SPEC['revision'] or download.get('sha256')!=SPEC['sha256']:raise ValueError()
        if model.stat().st_size!=SPEC['bytes']:raise ValueError()
        if full and checksum(model)!=SPEC['sha256']:raise ValueError()
    except (OSError,ValueError,TypeError):raise RuntimeError(message) from None
    return BINARY,model


def status():
    try:check_ready();return {'available':True,'engine':SPEC['engine'],'model':SPEC['model'],'device':'CPU','max_seconds':30,'languages':SPEC['languages'],'message':'Local transcription; review the detected words before saving.'}
    except (OSError,RuntimeError,ValueError) as exc:return {'available':False,'engine':SPEC['engine'],'model':SPEC['model'],'device':'CPU','max_seconds':30,'languages':SPEC['languages'],'message':str(exc)}
