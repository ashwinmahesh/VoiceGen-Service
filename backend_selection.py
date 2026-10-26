"""Resolve startup selection before importing any model runtime."""
import argparse
import json
import os
from pathlib import Path

BACKENDS = tuple(model['id'] for model in json.loads((Path(__file__).resolve().parent/'model_registry.json').read_text()))
AUDIOCPP_REVISION = '082177ecac45f1965929ded33cd81a85c13542f1'


def voice_design_models():
    """Static supported models, in menu order; no runtime or asset probing."""
    root=Path(__file__).resolve().parent
    return [model for model in json.loads((root/'model_registry.json').read_text())
            if 'design' in json.loads((root/'backends'/model['id']/'service.json').read_text()).get('modes',{})]


def default_backend():
    value=json.loads((Path(__file__).resolve().parent/'service_defaults.json').read_text())['model']
    if value not in BACKENDS:raise ValueError(f'Unsupported default model {value!r}')
    return value


def weights_directory(root, backend, original_backend, override=None):
    if override:
        path=Path(override).expanduser()
        if not path.is_absolute():raise ValueError('WEIGHTS_DIR/backend_weights must be an absolute directory')
        return path
    base=Path(root)/'weights'
    return base if backend is None or backend==original_backend else base/backend


def selected_backend(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--backend', choices=BACKENDS)
    args, _ = parser.parse_known_args(argv)
    value = args.backend or os.environ.get('MODEL_BACKEND') or default_backend()
    if value is not None and value not in BACKENDS:
        raise ValueError(f'Unknown MODEL_BACKEND {value!r}; choose {", ".join(BACKENDS)}')
    return value
