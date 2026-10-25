from config import PROFILE, MODEL_SPECS, EXTENSION_SPECS, ROOT, OUTPUT_DIR
from schemas import Capabilities
from directions import PRESETS, EMOTIONS


def capabilities():
    qwen=PROFILE['backend']=='qwen3';vox=PROFILE['backend']=='voxcpm2'
    custom=qwen and 'custom' in MODEL_SPECS
    return Capabilities(service=PROFILE['id'],name=PROFILE['name'],backend=PROFILE['backend'],
        quantization=PROFILE['quantization'],sample_rate=PROFILE['sample_rate'],models=MODEL_SPECS,
        extensions={key:{'source':spec['source'],'revision':spec['revision'],'license':spec['license'],
                         'experimental':True,'modes':['preset'],'emotions':list(EMOTIONS),
                         'strength':{'default':8,'min':0,'max':12},'layers':[21,25],'decay':.985}
                    for key,spec in EXTENSION_SPECS.items()},presets=PRESETS if custom else [],
        modes={'design':{'description':True,'emotion':True,'accent':True},
               'reference':{'description':False,'emotion':vox,'accent':vox,'transcript':True,
                            'continuation':vox,'combined':vox,'clone_modes':['reference','continuation','combined'] if vox else ['reference'],'continuation_direction':False},
               'preset':{'available':custom,'description':False,'emotion':custom,'accent':custom}},
        controls={'steps':{'default':10,'min':1,'max':30} if vox else None,
                  'guidance':{'default':2.,'min':1,'max':5} if vox else None,
                  'temperature':{'default':.9,'min':0,'max':2} if qwen else None,
                  'seed':{'default':42,'min':0,'max':2**32-1},
                  'max_tokens':{'default':1000,'min':50,'max':2000}},
        languages=['auto','English','Chinese','Japanese','Korean','German','French','Russian','Portuguese','Spanish','Italian'] if qwen else ['auto'],
        limits={'text_characters':3000,'input_tokens':2048 if qwen else 4096,'upload_bytes':25*1024*1024,
                'reference_seconds':[1,30],'reference_sample_rate':[8000,96000],'reference_channels':2,
                'single_worker':True,'token_limit_is_per_segment':True},storage=str(ROOT),output_dir=str(OUTPUT_DIR))


def validate_controls(request):
    caps=capabilities();qwen=PROFILE['backend']=='qwen3';vox=PROFILE['backend']=='voxcpm2'
    for name in ['steps','guidance','temperature']:
        if getattr(request,name) is not None and caps.controls[name] is None:
            raise ValueError(f'{name} is unsupported by {PROFILE["name"]}')
    if request.language not in caps.languages:raise ValueError('Unsupported language. See /api/capabilities')
    reference=bool(request.voice_id or request.reference_id)
    if request.preset_voice and request.preset_voice not in {item['id'] for item in caps.presets}:
        raise ValueError('Unsupported preset voice. See /api/capabilities')
    if qwen and reference and (request.emotion.strip() or request.accent.strip()):
        raise ValueError('Qwen Base does not support emotion or accent instructions with a reference voice')
    if request.clone_mode in ['continuation','combined'] and (not vox or not reference):
        raise ValueError('Continuation/combined cloning is supported only for VoxCPM2 reference voices')
    if request.clone_mode in ['continuation','combined'] and (request.emotion.strip() or request.accent.strip()):
        raise ValueError('Continuation/combined cloning follows the recorded delivery; choose reference mode for new emotion/accent directions')
    if request.ref_text and not (reference and (qwen or request.clone_mode in ['continuation','combined'])):
        raise ValueError('ref_text requires Qwen reference cloning or VoxCPM2 continuation/combined cloning')
    if request.steering_emotion:
        if not request.preset_voice or not caps.extensions or request.steering_emotion not in EMOTIONS:
            raise ValueError('Emotion steering requires an installed Qwen CustomVoice preset and a listed emotion')
    elif request.steering_strength is not None:
        raise ValueError('Choose a steering_emotion before setting steering_strength')
    defaults={name:spec['default'] for name,spec in caps.controls.items() if name in ['steps','guidance','temperature']
              and spec is not None and getattr(request,name) is None}
    if request.steering_emotion:
        if request.temperature is None:defaults['temperature']=1.1
        if request.steering_strength is None:defaults['steering_strength']=8.
    return request.model_copy(update=defaults)
