"""Capability-gated preview/apply with immutable spoken text and durable provenance."""
import json
import os
from pathlib import Path
import subprocess
import time
import unicodedata
from uuid import uuid4
from config import ROOT, PROJECT_ROOT, validate_mount
from inference_locks import reservation_descriptors
from performance_controls import HIGGS_CATALOG,rendered_text
from prompt_policy import POLICY_VERSION
from prompt_profiles import BACKENDS,load_profile,mode_spec
from prompt_assets import REPOSITORY,REVISION,check_assets,check_runtime
from voice_catalog import IDENTITY,atomic_json,now


def capability(enabled):
    profiles={backend:load_profile(backend) for backend in BACKENDS}
    return dict(enabled=enabled,model=REPOSITORY,revision=REVISION,policy_version=POLICY_VERSION,
        preview_url='/api/prompts/expand',application='Preview then apply; dialogue and reference transcripts never enter the LLM',
        supported={backend:[spec['label'] for spec in profile['modes'].values() if spec['available']]
                   for backend,profile in profiles.items() if any(spec['available'] for spec in profile['modes'].values())},
        unsupported=['Vox combined/continuation or auto-follow-recording']+
                    [profile['name']+' '+spec['label'] for profile in profiles.values() for spec in profile['modes'].values() if not spec['available']])


def context(request,effective,backend):
    return dict(backend=backend,mode='reference' if request.voice_id or request.reference_id else 'preset' if request.preset_voice else 'design',
                clone_mode=effective.clone_mode,voice_id=request.voice_id,reference_id=request.reference_id,
                preset_voice=request.preset_voice,accent=request.accent,language=request.language,
                delivery_context=request.emotion if not (request.voice_id or request.reference_id or request.preset_voice) else None)


def plan(request,effective,backend,guidance=''):
    if request.prompt_expansion_id:raise ValueError('Restore the original guidance before expanding again.')
    ctx=context(request,effective,backend);mode=ctx['mode']
    profile,spec=mode_spec(backend,mode,effective.clone_mode)
    selected={**spec,**{key:profile[key] for key in ('id','backend','version','source','sha256')},'mode':mode}
    if backend in ('chatterbox','indextts') or (backend=='qwen3' and mode=='reference'):
        raise ValueError('This backend/mode has no supported written delivery instruction path; use its reference or numeric controls.')
    if backend=='voxcpm2' and effective.clone_mode in ('combined','continuation'):
        raise ValueError('Recorded-performance cloning does not accept new guidance. Choose flexible Voice reference explicitly to experiment.')
    if backend in ('fish','higgs'):
        if not guidance.strip():raise ValueError('Enter a written delivery direction for tag suggestions.')
        options=getattr(request,backend)
        original={}
        if backend=='fish':
            if '[' in request.text or (options and options.delivery_tags):raise ValueError('Keep existing Fish tags or clear them before requesting suggestions.')
            original={backend:{'delivery_tags':[]}}
        else:
            if '<|' in request.text or (options and any(getattr(options,key) for key in HIGGS_CATALOG)):
                raise ValueError('Keep existing Higgs tags or clear them before requesting suggestions.')
            original={backend:{key:None for key in HIGGS_CATALOG}}
        prompt=dict(kind=backend,backend=backend,guidance=guidance,profile_spec=selected)
        if backend=='higgs':prompt['allowed_values']=HIGGS_CATALOG
        return ctx,original,prompt
    if backend not in ('voxcpm2','qwen3','moss'):raise ValueError('Prompt expansion is unsupported for this backend.')
    target='description' if mode=='design' else 'emotion'
    original=getattr(request,target)
    if not original.strip():raise ValueError('Enter a voice description or delivery direction before expanding it.')
    return ctx,{target:original},dict(kind='prose',backend=backend,target=target,original=original,guidance=guidance,
                                      accent=request.accent,delivery_context=request.emotion if target=='description' else '',profile_spec=selected)


def strict_json(text):
    def object_pairs(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate output key')
            result[key]=value
        return result
    if len(text)>4096:raise ValueError('Expansion too large')
    value=json.loads(text,object_pairs_hook=object_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Non-finite value')))
    if not isinstance(value,dict):raise ValueError('Expected one JSON object')
    return value


def updates_for(prompt,original,completion):
    value=strict_json(completion)
    limits=prompt.get('profile_spec',{}).get('limits',{})
    if prompt['kind']=='prose':
        if set(value)!={'addition'} or not isinstance(value['addition'],str):raise ValueError('Invalid prose output schema')
        addition=value['addition']
        if len(addition)>limits.get('addition_characters',600) or len(addition.split())>limits.get('addition_words',60) or any(unicodedata.category(c).startswith('C') or c in '<>[]{}' for c in addition):
            raise ValueError('Invalid prose addition')
        target=prompt['target'];expanded=original[target]+('\n'+addition.strip() if addition.strip() else '')
        if addition.strip() and addition.strip() in original[target]:raise ValueError('The model repeated original guidance instead of adding detail; keep the original or retry.')
        if len(expanded)>(1500 if target=='description' else 1000):raise ValueError('Expanded guidance exceeds the voice field limit; shorten it or keep the original.')
        return {target:expanded}
    if prompt['kind']=='fish':
        if set(value)!={'delivery_tags'} or not isinstance(value['delivery_tags'],list) or len(value['delivery_tags'])>limits.get('delivery_tags',2):
            raise ValueError('Invalid Fish suggestion schema')
        from schemas import FishOptions
        return {'fish':{'delivery_tags':FishOptions(delivery_tags=value['delivery_tags']).delivery_tags}}
    if set(value)!={'control','value'}:raise ValueError('Suggest one catalog control and value')
    control,selected=value['control'],value['value']
    if control=='none' and selected is None:return {'higgs':{key:None for key in HIGGS_CATALOG}}
    if control not in HIGGS_CATALOG or selected not in HIGGS_CATALOG[control]:raise ValueError('Unknown Higgs control')
    return {'higgs':{key:selected if key==control else None for key in HIGGS_CATALOG}}


def apply(request,updates):
    values={}
    for key,value in updates.items():
        if key in ('fish','higgs'):
            from schemas import MODEL_OPTIONS
            old=getattr(request,key)
            values[key]=MODEL_OPTIONS[key](**{**(old.model_dump() if old else {}),**value})
        elif key in ('description','emotion'):values[key]=value
        else:raise ValueError('Expansion attempted an unsupported field')
    result=request.model_copy(update=values)
    assert result.text==request.text and result.ref_text==request.ref_text
    return result


def expand(request,effective,backend,guidance='',root=ROOT,transcript=''):
    ctx,original,prompt=plan(request,effective,backend,guidance)
    folder=check_assets();python=check_runtime();started=time.monotonic()
    environment={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false'}
    try:
        response=subprocess.run([str(python),str(PROJECT_ROOT/'prompt_worker.py')],
            input=json.dumps({'weights':str(folder),'plan':prompt}),text=True,capture_output=True,
            env=environment,pass_fds=reservation_descriptors(),timeout=180)
    except subprocess.TimeoutExpired:raise ValueError('Expansion exceeded 180 seconds; no speech was generated.') from None
    if response.returncode:raise ValueError('Local guidance expansion failed; keep the original or retry.')
    try:
        data=json.loads(response.stdout);updates=updates_for(prompt,original,data['completion'])
    except (ValueError,KeyError,TypeError) as error:raise ValueError('Invalid expansion: '+str(error)) from None
    candidate=apply(request,updates)
    from capabilities import validate_controls,resolve_clone_mode
    resolved=validate_controls(candidate)
    resolved=resolve_clone_mode(resolved,backend,bool(ctx['voice_id'] or ctx['reference_id']),transcript)
    if resolved.clone_mode!=effective.clone_mode:raise ValueError('Expansion cannot change reference conditioning mode')
    rendered_text(resolved,backend)
    identity=uuid4().hex
    record=dict(id=identity,created_at=now(),context=ctx,original=original,updates=updates,request_guidance=guidance,
        llm=dict(model=REPOSITORY,revision=REVISION,policy_version=POLICY_VERSION,runtime='mlx-lm 0.32.2',
                 profile=backend+':'+prompt.get('target',prompt['kind']),policy_source='prompt_policy.py',
                 seconds=round(time.monotonic()-started,3),temperature=0,thinking=False,
                 peak_mlx_memory_gb=data.get('peak_mlx_memory_gb'),peak_process_rss_gb=data.get('peak_process_rss_gb')))
    record['llm'][backend]=prompt['profile_spec']
    target=validate_mount(Path(root)/'prompt-expansions'/f'{identity}.json');target.parent.mkdir(parents=True,exist_ok=True)
    atomic_json(target,record)
    return record


def applied_record(request,effective,backend,root=ROOT):
    identity=request.prompt_expansion_id
    if not identity:return None
    if not IDENTITY.fullmatch(identity):raise ValueError('Invalid prompt expansion ID')
    path=validate_mount(Path(root)/'prompt-expansions'/f'{identity}.json')
    if not path.is_file() or path.stat().st_size>32768:raise ValueError('Saved prompt expansion is unavailable; preview again or remove its ID')
    record=json.loads(path.read_text())
    if record['id']!=identity or record['context']!=context(request,effective,backend):raise ValueError('Expansion belongs to another voice/mode/accent; preview again')
    for key,value in record['updates'].items():
        current=getattr(request,key)
        if isinstance(value,dict):
            if current is None or any(getattr(current,k)!=v for k,v in value.items()):raise ValueError('Expanded controls changed; remove the expansion ID or preview again')
        elif current!=value:raise ValueError('Expanded guidance changed; remove the expansion ID or preview again')
    return record
