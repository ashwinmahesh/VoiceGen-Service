"""Select compatible CPython without importing inference libraries or changing system Python."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

# Bounds come from the complete pinned locks, not just a model's Python metadata.
# New Python minors need wheel/dependency validation before being added here.
RUNTIMES = {
    'mlx': ((12, 13, 14), '3.14', 'The pinned NumPy/SciPy stack needs Python 3.12 or newer.'),
    'enhancer': ((12, 13, 14), '3.14', 'The pinned NumPy stack needs Python 3.12 or newer.'),
    'native': ((11, 12), '3.11', 'Pinned SciPy needs 3.11+, and NumPy 1.26 provides wheels through 3.12.'),
    'chatterbox': ((11, 12), '3.11', 'The pinned Torch/NumPy stack uses Python 3.11 or 3.12.'),
    'indextts': ((11,), '3.11', 'IndexTTS requires Python below 3.12; the complete lock needs 3.11+.'),
    'queue': ((11, 12, 13, 14), '3.11', 'The pinned queue dependencies need Python 3.11 or newer.'),
}


def runtime_for(backend):
    return 'mlx' if backend in ('voxcpm2', 'qwen3', 'fish') else 'native' if backend in ('moss', 'higgs') else backend


def compatible(role, info):
    return (info is not None and info.get('implementation') == 'cpython'
            and info.get('system') == 'Darwin' and info.get('machine') == 'arm64'
            and not info.get('free_threaded') and info.get('version', [0])[0] == 3
            and info['version'][1] in RUNTIMES[role][0])


def requirement(role):
    minors, _, reason = RUNTIMES[role]
    versions = ', '.join('3.' + str(minor) for minor in minors)
    return f'{role} needs standard CPython {versions} for Apple Silicon macOS. {reason}'


def probe_python(candidate):
    path = shutil.which(str(candidate))
    if not path:
        return None
    probe = '''import json,platform,sys,sysconfig
print(json.dumps(dict(version=list(sys.version_info[:3]), implementation=sys.implementation.name,
 system=platform.system(), machine=platform.machine(), free_threaded=bool(sysconfig.get_config_var('Py_GIL_DISABLED')),
 base=getattr(sys,'_base_executable',sys.executable))))'''
    try:
        result = subprocess.run([path, '-I', '-c', probe], capture_output=True, text=True, timeout=5)
        if result.returncode:
            return None
        info = json.loads(result.stdout)
        info['path'] = str(Path(path).absolute())
        return info
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def installed_pythons(root):
    # Prefer the user's installed default, then versioned commands and local bases.
    yield sys.executable
    yield 'python3'
    for minor in range(14, 10, -1):
        yield f'python3.{minor}'
        yield f'/opt/homebrew/opt/python@3.{minor}/bin/python3.{minor}'
        yield f'/Library/Frameworks/Python.framework/Versions/3.{minor}/bin/python3'
    yield from sorted(root.glob('.python/*/bin/python3'))
    yield from sorted(root.glob('.venv*/bin/python'))


def managed_python(root, role):
    for name in ('.python', '.bootstrap'):
        if not (root / name).resolve().is_relative_to(root.resolve()):
            raise RuntimeError(f'Keep {name} inside the serving project; automatic setup will not write outside it.')
    uv = subprocess.check_output(['sh', str(root / 'bootstrap_uv.sh')], text=True).strip()
    env = {**os.environ, 'UV_PYTHON_INSTALL_DIR': str(root / '.python'),
           'UV_PYTHON_BIN_DIR': str(root / '.bootstrap/bin'), 'UV_CACHE_DIR': str(root / '.bootstrap/cache'),
           'UV_PYTHON_INSTALL_BIN': '0', 'UV_NO_MODIFY_PATH': '1'}
    version = RUNTIMES[role][1]
    print(f'No compatible installed Python for {role}; installing Python {version} inside {root / ".python"}.', file=sys.stderr)
    subprocess.run([uv, '--no-config', 'python', 'install', version], env=env, check=True)
    python = subprocess.check_output([uv, '--no-config', 'python', 'find', '--managed-python',
                                     '--no-python-downloads', version], env=env, text=True).strip()
    if not Path(python).resolve().is_relative_to((root / '.python').resolve()):
        raise RuntimeError('Managed Python must stay inside the serving project.')
    return python


def ensure_environment(root, role, target, explicit=None):
    root, target = Path(root).absolute(), Path(target).absolute()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('These dependency locks target Apple Silicon macOS.')
    chosen = None
    if explicit:
        chosen = probe_python(explicit)
        if not compatible(role, chosen):
            raise RuntimeError(f'Explicit interpreter {explicit!r} is incompatible. {requirement(role)}')
    python = target / 'bin/python'
    if target.exists() or target.is_symlink():
        existing = probe_python(python)
        if not compatible(role, existing):
            raise RuntimeError(f'Existing environment {target} is incompatible or broken. {requirement(role)} '
                               'It was preserved; choose a new VENV for speech or preserve and rebuild this environment.')
        if chosen and chosen['version'][:2] != existing['version'][:2]:
            raise RuntimeError(f'{target} uses a different Python from the explicit interpreter. '
                               'It was preserved; choose a new VENV or omit the interpreter override.')
        print(f'Reusing Python {".".join(map(str, existing["version"]))} in {target}.', file=sys.stderr)
        return python
    if chosen is None:
        seen = set()
        for candidate in installed_pythons(root):
            info = probe_python(candidate)
            if not info or info['base'] in seen:
                continue
            seen.add(info['base'])
            if compatible(role, info):
                chosen = info
                break
    if chosen is None:
        chosen = probe_python(managed_python(root, role))
        if not compatible(role, chosen):
            raise RuntimeError('Downloaded interpreter failed validation. ' + requirement(role))
    print(f'Using Python {".".join(map(str, chosen["version"]))} for {target}.', file=sys.stderr)
    subprocess.run([chosen['base'], '-m', 'venv', '--copies', str(target)], check=True)
    if not compatible(role, probe_python(python)):
        raise RuntimeError(f'New environment {target} failed validation; it was preserved for inspection.')
    return python


def setup_queue(root):
    python = ensure_environment(root, 'queue', root / '.venv-queue', os.environ.get('QUEUE_BOOTSTRAP_PYTHON'))
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-cache-dir', '--no-deps',
                    '-r', str(root / 'requirements-queue.lock.txt')], check=True)
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['setup-queue'])
    args = parser.parse_args()
    try:
        setup_queue(Path(__file__).resolve().parent)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
