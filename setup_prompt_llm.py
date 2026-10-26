"""Install the isolated optional LLM runtime without modifying speech environments."""
from pathlib import Path
import subprocess
import sys
import tempfile
from config import PROJECT_ROOT, prepare_storage


def main():
    if sys.version_info[:2]!=(3,14):raise SystemExit('The optional LLM lock requires Python 3.14. Set LLM_PYTHON.')
    import platform
    if platform.system()!='Darwin' or platform.machine()!='arm64':raise SystemExit('The LLM runtime lock targets Apple Silicon macOS.')
    prepare_storage()
    target=PROJECT_ROOT/'.venv-enhancer';python=target/'bin/python'
    if not python.is_file():subprocess.run([sys.executable,'-m','venv','--copies',str(target)],check=True)
    actual=subprocess.check_output([str(python),'-c','import sys; print("%d.%d" % sys.version_info[:2])'],text=True).strip()
    if actual!='3.14':raise SystemExit('The existing .venv-enhancer must use Python 3.14; preserve/rebuild it before setup.')
    with tempfile.TemporaryDirectory(prefix='voice-llm-build-',dir='/private/tmp') as scratch:
        import os
        env={**os.environ,'TMPDIR':scratch}
        subprocess.run([str(python),'-m','pip','install','--no-cache-dir','--no-deps','-r',str(PROJECT_ROOT/'requirements.enhancer.lock.txt')],env=env,check=True)
    subprocess.run([str(python),'-m','pip','check'],check=True)
    subprocess.run([str(python),str(PROJECT_ROOT/'prompt_assets.py')],check=True)


if __name__=='__main__':main()
