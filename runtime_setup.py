"""Install one pinned runtime; keep source/environments local and data external."""
import os
from pathlib import Path
import subprocess

from config import BACKEND, PROFILE, ORIGINAL_PROFILE, PROJECT_ROOT, ROOT, prepare_storage
from python_runtime import ensure_environment, runtime_for


def main():
    prepare_storage()
    backend=PROFILE['backend']
    mlx=backend in ('voxcpm2','qwen3','fish')
    import platform
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise SystemExit('These runtime locks target Apple Silicon macOS.')
    target=Path(os.environ.get('VENV',str(PROJECT_ROOT/('.venv' if not BACKEND or BACKEND==ORIGINAL_PROFILE['backend'] else '.venv-mlx' if mlx else '.venv-native' if backend in ('moss','higgs') else '.venv-'+backend))))
    if not target.is_absolute():target=PROJECT_ROOT/target
    python=ensure_environment(PROJECT_ROOT,runtime_for(backend),target,os.environ.get('BOOTSTRAP_PYTHON'))
    lock=PROJECT_ROOT/'requirements.lock.txt' if mlx else PROJECT_ROOT/'backends'/('native' if backend in ('moss','higgs') else backend)/'requirements.lock.txt'
    env=os.environ.copy()
    # sdist builds on exFAT produce AppleDouble build failures. This bounded,
    # ephemeral scratch holds build tools only, never weights or user media.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='voice-build-',dir='/private/tmp') as scratch:
        env['TMPDIR']=scratch
        subprocess.run([str(python),'-m','pip','install','--no-cache-dir','--no-deps','-r',str(lock)],env=env,check=True)
    subprocess.run([str(python),str(PROJECT_ROOT/'clean_env.py'),str(target)],check=True)
    subprocess.run([str(python),'-m','pip','check'],check=True)
    if backend in ('moss','higgs'):
        from backend_selection import AUDIOCPP_REVISION
        source=PROJECT_ROOT/'.runtime-source/audio.cpp'
        if not source.exists():
            source.parent.mkdir(exist_ok=True)
            subprocess.run(['git','clone','--no-checkout','https://github.com/0xShug0/audio.cpp.git',str(source)],check=True)
            subprocess.run(['git','-C',str(source),'checkout','--detach',AUDIOCPP_REVISION],check=True)
        actual=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
        if actual!=AUDIOCPP_REVISION:raise SystemExit('Native source revision differs; preserve it and select a clean pinned checkout.')
        from native_instrumentation import prepare_native
        prepare_native(source,PROJECT_ROOT)
        cmake=target/'bin/cmake'
        subprocess.run([str(cmake),'-S',str(source),'-B',str(source/'build'),'-DCMAKE_BUILD_TYPE=Release',
            '-DENGINE_ENABLE_METAL=ON','-DENGINE_ENABLE_OPENMP=OFF','-DAUDIOCPP_DEPLOYMENT_BUILD=ON',
            '-DAUDIOCPP_MODEL_SET=custom','-DAUDIOCPP_MODELS=moss_voicegen,higgs_audio_tts'],check=True)
        # Higgs runs the resident server; MOSS runs the per-take CLI.
        subprocess.run([str(cmake),'--build',str(source/'build'),'--target','audiocpp_cli','audiocpp_server','-j','4'],check=True)
    print(f'{backend} runtime ready: {python}\nData: {ROOT}')


if __name__=='__main__':
    try:main()
    except (RuntimeError,subprocess.CalledProcessError) as error:raise SystemExit(str(error)) from None
