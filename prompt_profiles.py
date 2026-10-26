"""Versioned backend/mode expansion specifications; stdlib-only for the isolated LLM."""
import hashlib
import json
from pathlib import Path

BACKENDS=('voxcpm2','qwen3','chatterbox','indextts','moss','fish','higgs')
NATIVE_MODES={'voxcpm2':{'design','reference'},'qwen3':{'design','preset'},'moss':{'design'},
              'fish':{'reference'},'higgs':{'reference'},'chatterbox':set(),'indextts':set()}
ROOT=Path(__file__).resolve().parent


def load_profile(backend):
    if backend not in BACKENDS:raise ValueError('Unknown voice expansion backend')
    source=f'backends/{backend}/prompt_expansion.json'
    raw=(ROOT/source).read_bytes();profile=json.loads(raw)
    if not isinstance(profile,dict) or set(profile)!={'id','backend','version','name','summary','sources','modes'}:
        raise ValueError('Invalid expansion profile fields: '+source)
    if profile['id']!=backend+'-voice-guidance' or profile['backend']!=backend or type(profile['version']) is not int or profile['version']<1:
        raise ValueError('Invalid expansion profile identity: '+source)
    if not all(isinstance(profile[k],str) and profile[k].strip() for k in ('name','summary')):
        raise ValueError('Expansion profile needs a name and summary')
    if not isinstance(profile['sources'],list) or not profile['sources'] or any(not isinstance(url,str) or not url.startswith('https://') for url in profile['sources']):
        raise ValueError('Expansion profile needs HTTPS source links')
    modes=profile['modes']
    if not isinstance(modes,dict) or not modes or set(modes)-{'design','reference','preset'}:raise ValueError('Invalid expansion profile modes')
    kind='fish' if backend=='fish' else 'higgs' if backend=='higgs' else 'prose'
    for mode,spec in modes.items():
        if not isinstance(spec,dict) or type(spec.get('available')) is not bool or not isinstance(spec.get('label'),str):raise ValueError('Invalid expansion mode availability/label')
        if not spec['available']:
            if set(spec)!={'available','label','reason'} or not isinstance(spec['reason'],str) or not spec['reason'].strip():raise ValueError('Unavailable expansion mode needs a reason')
            continue
        keys={'available','label','instructions','kind','target','limits'}
        if set(spec)-{'clone_modes'}!=keys or spec['kind']!=kind or not isinstance(spec['instructions'],str) or not spec['instructions'].strip():raise ValueError('Invalid expansion mode specification')
        target=('description' if mode=='design' else 'emotion') if kind=='prose' else 'fish.delivery_tags' if kind=='fish' else 'higgs'
        if spec['target']!=target:raise ValueError('Expansion profile cannot change its native target')
        limits=spec['limits'];ceilings={'addition_characters':600,'addition_words':60} if kind=='prose' else {'delivery_tags':2} if kind=='fish' else {'controls':1}
        if not isinstance(limits,dict) or set(limits)!=set(ceilings) or any(type(limits[k]) is not int or not 1<=limits[k]<=cap for k,cap in ceilings.items()):raise ValueError('Expansion profile exceeds application limits')
        if 'clone_modes' in spec and (mode!='reference' or spec['clone_modes']!=['reference']):raise ValueError('Only flexible reference supports prose expansion')
        if backend=='voxcpm2' and mode=='reference' and spec.get('clone_modes')!=['reference']:raise ValueError('Vox reference expansion requires explicit flexible conditioning')
        if mode not in NATIVE_MODES[backend]:
            raise ValueError('Profile cannot enable an unsupported native instruction path')
    return {**profile,'source':source,'sha256':hashlib.sha256(raw).hexdigest()}


def mode_spec(backend,mode,clone_mode='reference'):
    profile=load_profile(backend);spec=profile['modes'].get(mode)
    if not spec or not spec['available']:raise ValueError((spec or {}).get('reason','No prompt expansion profile for this voice mode'))
    if 'clone_modes' in spec and clone_mode not in spec['clone_modes']:
        raise ValueError('Recorded-performance cloning does not accept new guidance. Choose flexible Voice reference explicitly to experiment.')
    return profile,spec
