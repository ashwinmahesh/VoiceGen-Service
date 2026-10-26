"""Install the isolated optional LLM runtime without modifying speech environments."""
import os
import subprocess
import tempfile
from config import PROJECT_ROOT, prepare_storage
from python_runtime import ensure_environment


def main():
    import platform
    if platform.system()!='Darwin' or platform.machine()!='arm64':raise SystemExit('The LLM runtime lock targets Apple Silicon macOS.')
    prepare_storage()
    target=PROJECT_ROOT/'.venv-enhancer'
    python=ensure_environment(PROJECT_ROOT,'enhancer',target,os.environ.get('LLM_PYTHON'))
    with tempfile.TemporaryDirectory(prefix='voice-llm-build-',dir='/private/tmp') as scratch:
        env={**os.environ,'TMPDIR':scratch}
        subprocess.run([str(python),'-m','pip','install','--no-cache-dir','--no-deps','-r',str(PROJECT_ROOT/'requirements.enhancer.lock.txt')],env=env,check=True)
    subprocess.run([str(python),'-m','pip','check'],check=True)
    subprocess.run([str(python),str(PROJECT_ROOT/'prompt_assets.py')],check=True)


if __name__=='__main__':
    try:main()
    except (RuntimeError,subprocess.CalledProcessError) as error:raise SystemExit(str(error)) from None
