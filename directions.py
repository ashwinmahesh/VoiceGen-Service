"""Shared supported modes and literal prompt composition; no hidden prompt expansion."""
from config import PROFILE

PRESETS=[
    {'id':'ryan','name':'Ryan','language':'English','description':'Dynamic male voice'},
    {'id':'aiden','name':'Aiden','language':'English','description':'Sunny American male voice'},
    {'id':'vivian','name':'Vivian','language':'Chinese','description':'Bright young female voice'},
    {'id':'serena','name':'Serena','language':'Chinese','description':'Warm, gentle female voice'},
    {'id':'uncle_fu','name':'Uncle Fu','language':'Chinese','description':'Seasoned male voice, low and mellow'},
    {'id':'dylan','name':'Dylan','language':'Chinese','description':'Beijing dialect male voice'},
    {'id':'eric','name':'Eric','language':'Chinese','description':'Sichuan dialect male voice'},
    {'id':'ono_anna','name':'Ono Anna','language':'Japanese','description':'Playful Japanese female voice'},
    {'id':'sohee','name':'Sohee','language':'Korean','description':'Warm Korean female voice'}]
EMOTIONS={'sad':'sad','joy':'joy','anger':'ang','fear':'fear','disgust':'disgust','surprise':'surprise',
          'contempt':'contempt','awe':'awe','nostalgia':'nostalgia','disapproval':'disapproval',
          'remorse':'remorse','outrage':'outrage','despair':'despair'}


def instructions(request, reference):
    parts=[request.description if reference is None else '',
           f'Accent direction: {request.accent}' if request.accent.strip() else '',request.emotion]
    return '. '.join(part.strip().rstrip('.') for part in parts if part.strip())


def variant_for(request, reference):
    return PROFILE['modes']['preset' if request.preset_voice else 'reference' if reference else 'design']
