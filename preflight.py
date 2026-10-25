"""Setup preflight for the tested Apple Silicon runtime."""
import platform
import sys
from config import prepare_storage, ROOT, PROFILE


def main():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise SystemExit('This lockfile targets Apple Silicon macOS; select a supported runtime for other hardware.')
    mlx=PROFILE['backend'] in ('voxcpm2','qwen3','fish')
    expected=(3,14) if mlx else (3,11)
    if sys.version_info[:2] != expected:
        raise SystemExit(f'This backend lock targets Python {expected[0]}.{expected[1]}; set BOOTSTRAP_PYTHON.')
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
