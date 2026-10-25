from config import PROFILE, MODEL_SPECS, EXTENSION_SPECS, ROOT, OUTPUT_DIR
from schemas import Capabilities, MODEL_OPTIONS
from directions import PRESETS, EMOTIONS


def with_pause_editing(caps):
    caps.limits['pause_adjustment']={'reviewed_spans':True,'automatic_alignment':False,
        'default_minimum_gap_ms':750,'max_spans':30,'max_source_seconds':600,
        'max_output_seconds':900,'max_bytes':128*1024*1024}
    return caps


def capabilities():
    caps=backend_capabilities()
    from license_notices import notice_for
    notice=notice_for(PROFILE['backend'])
    if notice:
        caps.models={key:{**spec,**(notice if key==PROFILE['backend'] else {})} for key,spec in caps.models.items()}
    return caps


def backend_capabilities():
    if PROFILE['backend'] in ('moss','fish','higgs'):
        return with_pause_editing(additional_capabilities())
    if PROFILE['backend'] in ('chatterbox','indextts'):
        return with_pause_editing(torch_capabilities())
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
                'pause_adjustment':{'reviewed_spans':True,'automatic_alignment':False,'default_minimum_gap_ms':750,'max_spans':30,'max_source_seconds':600,'max_output_seconds':900,'max_bytes':128*1024*1024},
                'queue':{'durable':True,'api_generate_immediately_default':False,'ui_generate_immediately_default':True,
                         'ordering':'Immediate FIFO before normal FIFO; active inference is never interrupted',
                         'backend_policy':'Only the startup backend executes; other pending backends are paused',
                         'cancel_running':False},
                'single_worker':True,'token_limit_is_per_segment':True},storage=str(ROOT),output_dir=str(OUTPUT_DIR))


def validate_controls(request):
    caps=capabilities();qwen=PROFILE['backend']=='qwen3';vox=PROFILE['backend']=='voxcpm2'
    for backend in MODEL_OPTIONS:
        if getattr(request,backend) is not None and backend!=PROFILE['backend']:
            raise ValueError(f'{backend} options require that startup backend')
    extra=['exaggeration','cfg_weight','emotion_reference_id','emotion_vector','emotion_alpha','duration_factor','interval_silence']
    allowed={'chatterbox':{'exaggeration','cfg_weight'},'indextts':set(extra)-{'exaggeration','cfg_weight'}}.get(PROFILE['backend'],set())
    for name in extra:
        if getattr(request,name) is not None and name not in allowed:
            raise ValueError(f'{name} is unsupported by {PROFILE["name"]}')
    if PROFILE['backend'] in ('moss','fish','higgs'):
        return validate_additional_controls(request,caps)
    if PROFILE['backend'] in ('chatterbox','indextts'):
        return validate_torch_controls(request,caps)
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


def torch_capabilities():
    chatter=PROFILE['backend']=='chatterbox'
    control=lambda default,lo,hi:dict(default=default,min=lo,max=hi)
    controls={key:None for key in ['steps','guidance','temperature','exaggeration','cfg_weight',
              'duration_factor','interval_silence','emotion_alpha','emotion_vector','emotion_reference_id']}
    controls.update(seed=control(42,0,2**32-1),max_tokens=control(1000,1000,1000) if chatter else control(1000,50,1500),
                    temperature=control(.8,.05,2))
    if chatter:
        controls.update(exaggeration=control(.5,0,1),cfg_weight=control(.5,0,1))
    else:
        controls.update(duration_factor=control(1,.7,1.5),interval_silence=control(400,0,2000),
                        emotion_alpha=control(1,0,1),emotion_reference_id={'type':'reference_id'},
                        emotion_vector={'length':8,'min':0,'max':1,'sum_max':1,
                          'order':['happy','angry','sad','afraid','disgusted','melancholic','surprised','calm']})
    model_controls={key:controls.pop(key) for key in MODEL_OPTIONS[PROFILE['backend']].model_fields}
    for key in ['exaggeration','cfg_weight','duration_factor','interval_silence','emotion_alpha','emotion_vector','emotion_reference_id']:controls.pop(key,None)
    controls[PROFILE['backend']]=model_controls
    return Capabilities(service=PROFILE['id'],name=PROFILE['name'],backend=PROFILE['backend'],
        quantization=PROFILE['quantization'],sample_rate=PROFILE['sample_rate'],models=MODEL_SPECS,
        modes={'design':{'available':False,'description':False,'emotion':False,'accent':False},
               'reference':{'available':True,'description':False,'emotion':False,'accent':False,
                            'transcript':False,'continuation':False,'emotion_reference':not chatter},
               'preset':{'available':False}},controls=controls,languages=['auto','English'] if chatter else ['English','Chinese','Japanese','Spanish','Arabic'],
        limits={'text_characters':1000 if chatter else 3000,'input_tokens':400 if chatter else 2048,
                'upload_bytes':25*1024*1024,'reference_seconds':[1,30],'reference_sample_rate':[8000,96000],
                'reference_channels':2,'single_worker':True,'token_limit_is_per_segment':True,
                'runtime':'PyTorch MPS','experimental':PROFILE.get('experimental',False),'memory_note':PROFILE.get('memory_note'),
                'reference_conditioning_seconds':{'speaker':6,'decoder':10} if chatter else {'speaker':15,'emotion':15},
                'max_tokens_fixed':chatter,'phoneme_annotations':not chatter},storage=str(ROOT),output_dir=str(OUTPUT_DIR))


def validate_torch_controls(request,caps):
    backend=PROFILE['backend']
    options=getattr(request,backend)
    if options is not None:request=request.model_copy(update=options.model_dump())
    controls={**caps.controls,**caps.controls[backend]}
    if not (request.voice_id or request.reference_id):
        raise ValueError(f'{caps.name} requires a saved voice or reference recording; voice design is unsupported')
    if request.preset_voice or request.emotion.strip() or request.accent.strip() or request.ref_text is not None:
        raise ValueError(f'{caps.name} does not accept preset, emotion/accent text, or reference transcript controls. See capabilities.')
    if request.clone_mode!='reference' or request.steering_emotion or request.steering_strength is not None:
        raise ValueError('Continuation and Qwen emotion steering are unsupported by this backend')
    if request.language not in caps.languages:raise ValueError('Unsupported language. See /api/capabilities')
    if len(request.text)>caps.limits['text_characters']:raise ValueError('Text exceeds this backend character limit')
    for name in ['steps','guidance','temperature','max_tokens','exaggeration','cfg_weight','duration_factor','interval_silence','emotion_alpha']:
        value=getattr(request,name)
        spec=controls.get(name)
        if value is not None and spec is None:raise ValueError(f'{name} is unsupported by {caps.name}')
        if value is not None and spec is not None and not spec['min']<=value<=spec['max']:
            raise ValueError(f'{name} must be between {spec["min"]} and {spec["max"]} for {caps.name}')
    if request.emotion_alpha is not None and not (request.emotion_reference_id or request.emotion_vector is not None):
        raise ValueError('emotion_alpha requires an emotion reference or vector')
    defaults={key:spec['default'] for key,spec in controls.items() if spec and 'default' in spec
              and getattr(request,key) is None and key!='emotion_alpha'}
    if request.emotion_reference_id or request.emotion_vector is not None:defaults.setdefault('emotion_alpha',request.emotion_alpha if request.emotion_alpha is not None else 1.)
    effective=request.model_copy(update=defaults)
    return effective.model_copy(update={backend:MODEL_OPTIONS[backend](**{key:getattr(effective,key) for key in MODEL_OPTIONS[backend].model_fields})})


def additional_capabilities():
    backend=PROFILE['backend'];moss=backend=='moss'
    return Capabilities(service=PROFILE['id'],name=PROFILE['name'],backend=backend,
        quantization=PROFILE['quantization'],sample_rate=PROFILE['sample_rate'],models=MODEL_SPECS,
        modes={'design':{'available':moss,'description':moss,'emotion':moss,'accent':moss},
               'reference':{'available':not moss,'description':False,'emotion':False,'accent':False,
                            'transcript':not moss,'transcript_required':not moss,'continuation':False},
               'preset':{'available':False}},
        controls={'steps':None,'guidance':None,'temperature':{'default':1.5 if moss else .7 if backend=='fish' else .8,'min':.05,'max':2},
                  'seed':{'default':42,'min':0,'max':2**32-1},'max_tokens':{'default':1000,'min':50,'max':2000}},
        languages=['English','Chinese'] if moss else ['auto'],
        limits={'text_characters':500 if moss else 3000,'input_tokens':2048,'upload_bytes':25*1024*1024,
                'reference_seconds':[1,30],'reference_sample_rate':[8000,96000],'reference_channels':2,
                'single_worker':True,'token_limit_is_per_segment':True,'runtime':PROFILE['runtime'],
                'inline_controls':'[free-form tag]' if backend=='fish' else '<|category:value|>' if backend=='higgs' else None,
                'max_tokens_unit':'audio frames; upstream may raise to text-derived minimum' if moss else 'audio tokens',
                'model_residency':'per job subprocess' if backend in ('moss','higgs') else 'until unload'},
        storage=str(ROOT),output_dir=str(OUTPUT_DIR))


def validate_additional_controls(request,caps):
    moss=PROFILE['backend']=='moss'
    reference=bool(request.reference_id or request.voice_id)
    if moss and reference:raise ValueError('MOSS VoiceGenerator designs voices from descriptions; reference cloning is unsupported')
    if not moss and not reference:raise ValueError('Select a reference voice for this backend; description-based voice design is unsupported')
    if request.preset_voice or request.clone_mode!='reference' or request.steering_emotion or request.steering_strength is not None:
        raise ValueError('Preset voices, Vox continuation, and Qwen steering are unsupported by this backend')
    if (not moss and (request.emotion.strip() or request.accent.strip())) or (moss and request.ref_text is not None):
        raise ValueError('Use the documented inline text tags for Fish/Higgs directions; MOSS does not use reference transcripts')
    if request.language not in caps.languages:raise ValueError('Unsupported language. See /api/capabilities')
    if len(request.text)>caps.limits['text_characters']:raise ValueError('Text exceeds backend character limit')
    if request.steps is not None or request.guidance is not None:raise ValueError('Steps/guidance are unsupported by this backend')
    if request.temperature is not None and not .05<=request.temperature<=2:raise ValueError('Temperature must be between .05 and 2')
    return request.model_copy(update={'temperature':request.temperature if request.temperature is not None else caps.controls['temperature']['default']})
