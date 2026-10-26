"""Setup preflight for the tested Apple Silicon runtime."""
import platform
import sys
from config import prepare_storage, ROOT, PROFILE
from python_runtime import compatible, requirement, runtime_for


def main():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise SystemExit('This lockfile targets Apple Silicon macOS; select a supported runtime for other hardware.')
    mlx=PROFILE['backend'] in ('voxcpm2','qwen3','fish')
    import sysconfig
    if not compatible(runtime_for(PROFILE['backend']),dict(version=sys.version_info[:3],
            implementation=sys.implementation.name,system=platform.system(),machine=platform.machine(),
            free_threaded=bool(sysconfig.get_config_var('Py_GIL_DISABLED')))):
        raise SystemExit(requirement(runtime_for(PROFILE['backend']))+' Run make setup-env.')
    prepare_storage()
    if '--runtime' in sys.argv and mlx:
        import mlx.core as mx
        if not mx.metal.is_available():
            raise SystemExit('MLX Metal accelerator is unavailable')
        print('MLX Metal accelerator is available.')
    elif '--runtime' in sys.argv and PROFILE['backend'] in ('chatterbox','indextts'):
        import torch
        if not torch.backends.mps.is_available():raise SystemExit('PyTorch MPS is unavailable')
        print('PyTorch MPS accelerator is available.')
    print(f'Apple Silicon preflight passed. Data: {ROOT}')


if __name__=='__main__': main()
