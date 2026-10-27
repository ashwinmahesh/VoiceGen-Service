"""Reproducible whisper.cpp setup; tools/source internal, all weights on configured data storage."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
from transcription_assets import SPEC,SOURCE,BINARY,BUILD_RECORD,model_directory,checksum,check_ready
from config import PROJECT_ROOT


def cmake_binary():
    candidates=[shutil.which('cmake'),*(str(path) for path in PROJECT_ROOT.glob('.venv*/bin/cmake'))]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():return candidate
    tools=PROJECT_ROOT/'.bootstrap/transcription-tools'
    if tools.is_symlink():raise RuntimeError('Keep transcription build tools inside the project, without a symlink.')
    python=tools/'bin/python'
    if not python.exists():subprocess.run([sys.executable,'-m','venv',str(tools)],check=True)
    subprocess.run([str(python),'-m','pip','install','--no-cache-dir','--no-deps','cmake==4.1.3'],check=True)
    return str(tools/'bin/cmake')


def setup():
    folder=model_directory();folder.mkdir(parents=True,exist_ok=True)
    model=folder/SPEC['filename']
    if not model.is_file() or model.stat().st_size!=SPEC['bytes'] or checksum(model)!=SPEC['sha256']:
        temporary=model.with_suffix('.download')
        url=f'https://huggingface.co/{SPEC["repository"]}/resolve/{SPEC["revision"]}/{SPEC["filename"]}'
        print('Downloading small transcription model to',model,flush=True)
        offset=temporary.stat().st_size if temporary.is_file() else 0
        request=urllib.request.Request(url,headers={'Range':f'bytes={offset}-'} if offset else {})
        if offset<SPEC['bytes']:
            with urllib.request.urlopen(request,timeout=60) as source,temporary.open('ab' if offset and source.status==206 else 'wb') as output:
                shutil.copyfileobj(source,output,1024*1024)
        if temporary.stat().st_size!=SPEC['bytes'] or checksum(temporary)!=SPEC['sha256']:
            temporary.unlink(missing_ok=True)
            raise RuntimeError('Transcription model checksum mismatch; no completion record written. Rerun setup to repair.')
        temporary.replace(model)
    else:print('Transcription model already downloaded and verified; reusing in place.',flush=True)
    marker=folder/'verified.json';temporary=folder/'verified.json.tmp'
    temporary.write_text(json.dumps({'repository':SPEC['repository'],'revision':SPEC['revision'],'sha256':SPEC['sha256'],'license':SPEC['license']}));temporary.replace(marker)
    if not SOURCE.exists():
        SOURCE.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['git','clone','--depth','1','--branch',SPEC['source_tag'],SPEC['source_repository'],str(SOURCE)],check=True)
    revision=subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip()
    if revision!=SPEC['source_revision']:raise RuntimeError('Transcriber source revision differs; preserve the checkout and select the pinned revision before setup.')
    if subprocess.check_output(['git','-C',str(SOURCE),'status','--porcelain','--untracked-files=no'],text=True).strip():raise RuntimeError('Transcriber source has local edits; preserve them before building a clean pinned runtime.')
    try:check_ready(full=True);print('CPU transcription runtime already ready; no rebuild needed.',flush=True);return
    except RuntimeError:pass
    cmake=cmake_binary()
    subprocess.run([cmake,'-S',str(SOURCE),'-B',str(SOURCE/'build'),'-DCMAKE_BUILD_TYPE=Release',
        '-DGGML_METAL=OFF','-DWHISPER_COREML=OFF','-DGGML_CUDA=OFF','-DGGML_OPENMP=OFF',
        '-DWHISPER_BUILD_TESTS=OFF','-DWHISPER_BUILD_SERVER=OFF','-DBUILD_SHARED_LIBS=OFF'],check=True)
    subprocess.run([cmake,'--build',str(SOURCE/'build'),'--target','whisper-cli','-j','4'],check=True)
    BUILD_RECORD.write_text(json.dumps({'revision':revision,'cpu_only':True}))
    check_ready(full=True)
    print('Local CPU transcription ready:',model,flush=True)


if __name__=='__main__':
    try:setup()
    except (OSError,RuntimeError,subprocess.CalledProcessError) as exc:raise SystemExit(str(exc)) from None
