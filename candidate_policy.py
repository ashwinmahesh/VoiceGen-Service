"""One focused voice contrast per completion, sharing one loaded text-only LLM."""
import json
from prompt_policy import BASE

VERSION='voice-candidate-diversity-1'


def messages_for(plan):
    system=BASE+' '+plan['profile_spec']['instructions']+''' Design a DIFFERENT SPEAKER INTERPRETATION using the ONE supplied contrast target. Refine that exact target, not another target. Make an audible difference in resonance, vocal weight or articulation, not merely emotion or wording. Keep every explicit voice constraint: age, gender, accent, pitch register, smoothness, restraint, delivery and language. Do not repeat the original. Never output words to speak, biography, sound effects or sampler controls. Return exactly {"addition":"one short audible direction"}. Follow the supplied character/word limits and the guidance language.'''
    examples=[({'voice_guidance':'A calm adult voice.','contrast_target':'Brighter forward resonance.'},
               {'addition':'Use brighter, forward resonance and lighter vocal weight while retaining the calm delivery.'}),
              ({'voice_guidance':'A calm adult voice.','contrast_target':'Rounder chest resonance.'},
               {'addition':'Use rounder chest resonance and fuller vocal weight while retaining the calm delivery.'})]
    if sum('\u4e00'<=c<='\u9fff' for c in plan['base_description'])>len(plan['base_description'])/3:
        system+=' 请用中文输出 addition，只改变未被限定的共鸣和咬字，保留原来的音色约束、口音、音域和情绪。'
        examples=[({'voice_guidance':'平静的成年声音。','contrast_target':'明亮靠前的共鸣'}, {'addition':'在原有音域内使用更明亮靠前的共鸣和较轻的声重，保持平静的表达。'}),
                  ({'voice_guidance':'平静的成年声音。','contrast_target':'圆润厚实的共鸣'}, {'addition':'在原有音域内使用更圆润厚实的共鸣和较饱满的声重，保持平静的表达。'})]
    result=[]
    for target in plan['targets']:
        messages=[{'role':'system','content':system}]
        for source,value in examples:messages.extend([{'role':'user','content':json.dumps(source,ensure_ascii=False)},{'role':'assistant','content':json.dumps(value,ensure_ascii=False)}])
        source={'voice_guidance':plan['base_description'],'preserve_accent':plan['accent'],'preserve_delivery':plan['emotion'],
                'contrast_target':target['direction'],'maximum_addition_characters':plan['max_characters'],'maximum_addition_words':plan['max_words']}
        messages.append({'role':'user','content':json.dumps(source,ensure_ascii=False)});result.append(messages)
    return result
