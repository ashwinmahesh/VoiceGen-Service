"""Lightweight readiness checks without importing ML runtimes or loading weights."""
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
from functools import lru_cache
import subprocess

from config import PROFILE, PROJECT_ROOT
from backend_selection import AUDIOCPP_REVISION


@lru_cache(maxsize=4)
def native_build_error(binary, stamp):
    try:
        result=subprocess.run([binary,'--version'],capture_output=True,text=True,timeout=5,check=False)
        if result.returncode or AUDIOCPP_REVISION[:7] not in result.stdout or 'metal' not in result.stdout:
            return 'Native binary is not the pinned Metal build; run make setup-env MODEL='+PROFILE['backend']
    except (OSError,subprocess.TimeoutExpired) as exc:
        return 'Cannot inspect native runtime: '+str(exc)
    return None


def runtime_errors():
    backend=PROFILE['backend']
    expected={'mlx-audio':'0.6.1','mlx':'0.32.4'} if backend in ('voxcpm2','qwen3','fish') else {
        'torch':'2.6.0' if backend=='chatterbox' else '2.8.0',
        'transformers':'5.2.0' if backend=='chatterbox' else '4.52.1'} if backend in ('chatterbox','indextts') else {}
    errors=[]
    for package,required in expected.items():
        try:actual=version(package)
        except PackageNotFoundError:actual='not installed'
        if actual!=required:errors.append(f'{package}: expected {required}, found {actual}; run make setup-env MODEL={backend}')
    if backend in ('moss','higgs'):
        binary=Path(os.environ.get('AUDIOCPP_BIN',str(PROJECT_ROOT/'.runtime-source/audio.cpp/build/bin/audiocpp_cli')))
        if not binary.is_file() or not os.access(binary,os.X_OK):
            errors.append('Missing native runtime; run make setup-env MODEL='+backend)
        else:
            error=native_build_error(str(binary),binary.stat().st_mtime_ns)
            if error:errors.append(error)
    return errors
