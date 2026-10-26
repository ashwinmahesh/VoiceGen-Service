"""Intentional design contrasts; frozen once per durable candidate group."""
from difflib import SequenceMatcher
import hashlib
import json
import os
import subprocess
import time
import unicodedata
from config import PROJECT_ROOT
from candidate_policy import VERSION
from prompt_assets import REPOSITORY,REVISION,check_assets,check_runtime
from prompt_expansion import strict_json
from prompt_profiles import mode_spec
from inference_locks import reservation_descriptors

TARGETS=[
 ('Brighter resonance','Brighter forward resonance and lighter vocal weight within the stated register.',
  'Use noticeably brighter, forward resonance and lighter vocal weight within the stated register, retaining every specified voice trait, accent and delivery.'),
 ('Darker resonance','Rounder chest resonance and fuller vocal weight within the stated register.',
  'Use noticeably rounder chest resonance and fuller vocal weight within the stated register, retaining every specified voice trait, accent and delivery.'),
 ('Focused articulation','Compact resonance and tightly focused articulation, without changing the requested texture.',
  'Use compact, concentrated resonance and tightly focused articulation, retaining the requested vocal texture, register, accent and delivery.'),
 ('Open resonance','Broad open resonance and smoothly connected vowel shapes, within all stated traits.',
  'Use broad, open vowel resonance and smoothly connected vowel shapes, retaining every stated voice trait, register, accent and delivery.')]
CHINESE=[
 '在既定音域内使用更明亮、靠前的共鸣和较轻的声重，同时保留所有指定的音色、口音和情绪。',
 '在既定音域内使用更圆润、厚实的胸腔共鸣和较饱满的声重，同时保留所有指定的音色、口音和情绪。',
 '使用更紧凑集中的共鸣和清晰聚焦的咬字，同时保留指定的音色质感、音域、口音和情绪。',
 '使用更开阔的元音共鸣和流畅相连的元音形态，同时保留所有指定的声音特征、音域、口音和情绪。']


def definition(request,effective,backend,llm_enabled=False):
    llm_enabled=llm_enabled and request.candidate_use_llm
    design=not (request.voice_id or request.reference_id or request.preset_voice)
    if request.candidate_variation=='distinct' and (not design or backend not in ('voxcpm2','qwen3','moss')):
        raise ValueError('Distinct speaker designs require Vox, Qwen VoiceDesign or MOSS design mode. Reference/preset voices retain their selected identity.')
    if request.candidate_variation=='seed_only' or not design or request.candidate_count==1:return None
    profile,spec=mode_spec(backend,'design',effective.clone_mode)
    anchor_room=max(len(value[2]) for value in TARGETS[:request.candidate_count-1])+2 if llm_enabled else 1
    maximum=min(300,1500-len(effective.description)-anchor_room)
    if maximum<80:raise ValueError('Shorten the description to leave room for distinct voice directions, or choose candidate_variation:"seed_only".')
    selected={**spec,**{key:profile[key] for key in ('id','backend','version','source','sha256')},'mode':'design'}
    result=dict(version=VERSION,method='llm' if llm_enabled else 'built_in',ready=False,
                base_description=effective.description,accent=effective.accent,emotion=effective.emotion,
                profile_spec=selected,max_characters=min(maximum,spec['limits']['addition_characters']),
                max_words=min(45,spec['limits']['addition_words']),
                targets=[dict(label=label,direction=direction) for label,direction,_ in TARGETS[:request.candidate_count-1]])
    if llm_enabled:
        from candidate_policy import messages_for
        result['messages']=messages_for(result)
    return result


def validate_additions(plan,values):
    if not isinstance(values,list) or len(values)!=len(plan['targets']):raise ValueError('Diversity planner returned the wrong number of voice directions')
    cleaned=[]
    for value in values:
        if not isinstance(value,str) or not value.strip() or len(value)>plan['max_characters'] or len(value.split())>plan['max_words'] or any(unicodedata.category(c).startswith('C') or c in '<>[]{}' for c in value):
            raise ValueError('Invalid diverse voice direction')
        value=value.strip()
        if value.casefold() in plan['base_description'].casefold():raise ValueError('Diversity planner repeated the original description')
        if any(SequenceMatcher(None,value.casefold(),old.casefold()).ratio()>.98 for old in cleaned):raise ValueError('Diversity planner returned near-duplicate directions')
        cleaned.append(value)
    return cleaned


def build(plan,values,llm=None):
    values=validate_additions(plan,values)
    variants=[]
    for index,addition in enumerate(['',*values]):
        anchor=TARGETS[index-1][2] if index and plan['method']=='llm' else ''
        chinese=sum('\u4e00'<=c<='\u9fff' for c in plan['base_description'])>len(plan['base_description'])/3
        if anchor and chinese:anchor=CHINESE[index-1]
        effective_addition='\n'.join(part for part in (addition,anchor) if part)
        variants.append(dict(index=index+1,label='Original design' if index==0 else plan['targets'][index-1]['label'],
            base_description=plan['base_description'],addition=effective_addition,refinement=addition,contrast_anchor=anchor,
            description=plan['base_description']+('\n'+effective_addition if effective_addition else ''),
            method=plan['method'],version=plan['version'],profile_spec=plan['profile_spec'],llm=llm))
    return variants


def built_in(plan):
    chinese=sum('\u4e00'<=c<='\u9fff' for c in plan['base_description'])>len(plan['base_description'])/3
    additions=[CHINESE[i] if chinese else TARGETS[i][2] for i in range(len(plan['targets']))]
    if any(len(value)>plan['max_characters'] for value in additions):
        raise ValueError('The description leaves too little room for distinct directions; shorten it or choose candidate_variation:"seed_only".')
    return build(plan,additions)


def generate(plan):
    folder=check_assets();python=check_runtime();started=time.monotonic()
    try:
        response=subprocess.run([str(python),str(PROJECT_ROOT/'prompt_worker.py')],
            input=json.dumps({'weights':str(folder),'task':'candidate_variants','plan':{'messages':plan['messages']}}),text=True,capture_output=True,
            env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false'},
            pass_fds=reservation_descriptors(),timeout=180)
    except subprocess.TimeoutExpired:raise ValueError('Distinct voice planning exceeded 180 seconds; no speech was generated.') from None
    if response.returncode:raise ValueError('Distinct voice planning failed; retry or choose seed_only. No silent fallback was used.')
    data=json.loads(response.stdout)
    if not isinstance(data.get('completions'),list) or len(data['completions'])!=len(plan['targets']):raise ValueError('Invalid diversity output count')
    values=[]
    for completion in data['completions']:
        value=strict_json(completion)
        if set(value)!={'addition'}:raise ValueError('Invalid diversity output schema')
        values.append(value['addition'])
    return build(plan,values,dict(model=REPOSITORY,revision=REVISION,runtime='mlx-lm 0.32.2',
        policy_version=plan['version'],prompt_sha256=hashlib.sha256(json.dumps(plan['messages'],sort_keys=True).encode()).hexdigest(),
        seconds=round(time.monotonic()-started,3),temperature=0,thinking=False,
        peak_mlx_memory_gb=data.get('peak_mlx_memory_gb'),peak_process_rss_gb=data.get('peak_process_rss_gb')))
