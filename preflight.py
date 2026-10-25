"""Setup preflight for the tested Apple Silicon runtime."""
import platform
import sys
from config import prepare_storage, ROOT


def main():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise SystemExit('This lockfile targets Apple Silicon macOS; select a supported runtime for other hardware.')
    if sys.version_info[:2] != (3,14):
        raise SystemExit('This project lockfile was tested with Python 3.14; set BOOTSTRAP_PYTHON to that interpreter.')
    prepare_storage()
    if '--runtime' in sys.argv:
        import mlx.core as mx
        if not mx.metal.is_available():
            raise SystemExit('MLX Metal accelerator is unavailable')
        print('MLX Metal accelerator is available.')
    print(f'Apple Silicon preflight passed. Data: {ROOT}')


if __name__=='__main__': main()
