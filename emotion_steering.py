"""Experimental MLX port of the MIT qwen3-tts decode-only, norm-preserving steering recipe.

Upstream: gabriele-mastrapasqua/qwen3-tts, qwen_tts_talker.c:803–814 and qwen_tts.c:1881–1887.
Assets/revision/license are pinned in extension_specs.json. Never edits checkpoint weights.
"""
from contextlib import contextmanager
import struct
import numpy as np
from config import EXTENSION_DIR, EXTENSION_SPECS
from directions import EMOTIONS
from assets import verify_spec


def read_vector(path, layer_count=28, hidden_size=2048):
    raw=path.read_bytes()
    if len(raw)<12:raise ValueError('Steering vector header is incomplete')
    magic,layers,hidden=struct.unpack('<Iii',raw[:12])
    if magic!=0x54534C51 or layers!=layer_count+1 or hidden!=hidden_size:
        raise ValueError('Steering vector architecture does not match this model')
    if len(raw)!=12+layers*hidden*4:raise ValueError('Steering vector data is incomplete')
    vector=np.frombuffer(raw,dtype='<f4',offset=12).reshape(layers,hidden).copy()
    if not np.isfinite(vector).all():raise ValueError('Steering vector contains nonfinite values')
    return vector


def steering_weight(sequence_length, cache_offset, decode_index, strength, decay=.985):
    # First generation pass is an unsteered text prefill. Each subsequent one-token
    # decode uses strength * decay**frame, starting at frame zero, matching upstream.
    if sequence_length!=1 or cache_offset<=0:return 0.
    return strength*decay**decode_index


def preserve_norm(x, vector, weight, api):
    before=api.sum(x*x,axis=-1,keepdims=True)
    shifted=x+weight*vector
    after=api.sum(shifted*shifted,axis=-1,keepdims=True)
    scale=api.sqrt(before/api.maximum(after,1e-12))
    return shifted*scale


@contextmanager
def steer_layers(model, vectors, strength, array_api=None, module_base=None):
    if array_api is None:
        import mlx.core as array_api
        import mlx.nn as nn
        module_base=nn.Module
    if module_base is None:module_base=object
    layers=model.talker.model.layers
    if len(layers)!=28 or vectors.shape!=(29,2048):
        raise ValueError('Emotion steering supports the 28-layer, 2048-hidden Qwen 1.7B CustomVoice only')
    originals={index:layers[index] for index in range(21,26)}
    class SteeredLayer(module_base):
        def __init__(self,inner,vector):
            super().__init__();self.inner=inner;self.vector=array_api.array(vector);self.decode_index=0
        def __call__(self,x,position_embeddings,mask=None,cache=None):
            weight=steering_weight(x.shape[1],cache.offset if cache else 0,self.decode_index,strength)
            result=self.inner(x,position_embeddings,mask,cache)
            if weight:
                self.decode_index+=1
                # Arithmetic in float32 avoids bf16 norm/steer rounding; restore backend dtype.
                shifted=preserve_norm(result.astype(array_api.float32),self.vector,weight,array_api)
                return shifted.astype(result.dtype)
            return result
    try:
        for index,inner in originals.items():layers[index]=SteeredLayer(inner,vectors[index])
        yield
    finally:
        for index,inner in originals.items():layers[index]=inner


@contextmanager
def emotion_context(model, request):
    if not request.steering_emotion:
        yield;return
    spec=EXTENSION_SPECS['emotion_steering'];folder=EXTENSION_DIR/spec['subdir']
    errors=verify_spec(spec,folder,'emotion_steering')
    if errors:raise RuntimeError('; '.join(errors)+'. Run make download-extensions.')
    name=EMOTIONS[request.steering_emotion]
    vector=read_vector(folder/f'presets/steer/emotion/ryan_{name}.qlsteer')
    if model.config.tts_model_type!='custom_voice':raise ValueError('Steering requires CustomVoice')
    with steer_layers(model,vector,request.steering_strength):yield
