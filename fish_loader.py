"""Load the pinned full 8-bit Fish conversion without a stale layer exclusion.

MLX Audio 0.6.1's model predicate excludes embeddings and the fast decoder,
but this immutable conversion contains quantized tensors for those layers.
The shared quantizer already selects only modules with checkpoint .scales;
use that authoritative tensor layout and retain strict weight validation.
No installed runtime or model file is modified.
"""
import json
from pathlib import Path


def load_fish_model(folder):
    import mlx.core as mx
    from mlx_audio.tts.models.fish_qwen3_omni.fish_speech import Model
    from mlx_audio.tts.models.fish_qwen3_omni.config import ModelConfig
    from mlx_audio.utils import apply_quantization

    folder=Path(folder)
    config=json.loads((folder/'config.json').read_text())
    config['model_path']=str(folder)
    model=Model(ModelConfig.from_dict(config))
    weights=model.sanitize(mx.load(str(folder/'model.safetensors')))
    apply_quantization(model,config,weights,model_quant_predicate=None)
    model.load_weights(list(weights.items()),strict=True)
    mx.eval(model.parameters())
    model.eval()
    return Model.post_load_hook(model,folder)
