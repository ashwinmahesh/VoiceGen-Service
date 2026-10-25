"""Explicit model-specific text controls; never rewrites the submitted spoken text."""
import re

HIGGS_CATALOG={
    'emotion':['affection','amusement','anger','arousal','awe','bitterness','confusion','contemplation',
        'contentment','determination','disgust','elation','enthusiasm','fear','helplessness','longing',
        'pride','relief','sadness','shame','surprise'],
    'prosody':['speed_very_slow','speed_slow','speed_fast','speed_very_fast','pitch_low','pitch_high','expressive_high','expressive_low'],
    'style':['singing','shouting','whispering'],
}
HIGGS_CATALOG_SOURCE='https://huggingface.co/bosonai/higgs-tts-3-4b/blob/239f63fb7b02b1aa085f98d9efae5e35cc5523e8/PROMPTING.md'


def rendered_text(request,backend):
    options=getattr(request,backend,None)
    if backend=='fish' and options and options.delivery_tags:
        if re.match(r'^\s*\[',request.text):
            raise ValueError('Choose structured Fish delivery tags or manual leading tags, not both')
        manual={tag.strip().casefold() for tag in re.findall(r'\[([^\[\]]+)\]',request.text)}
        if manual.intersection(tag.casefold() for tag in options.delivery_tags):
            raise ValueError('A Fish delivery tag is already present in the submitted text')
        return ''.join('['+tag+'] ' for tag in options.delivery_tags)+request.text
    if backend=='higgs' and options:
        prefix=re.match(r'^\s*((?:<\|[^|>]+\|>\s*)+)',request.text)
        manual=set(re.findall(r'<\|([^:|>]+):[^|>]+\|>',prefix[0])) if prefix else set()
        tags=[]
        for category in HIGGS_CATALOG:
            value=getattr(options,category)
            if value:
                if category in manual:raise ValueError('Choose a structured or manual leading Higgs '+category+' directive, not both')
                tags.append('<|'+category+':'+value+'|>')
        return ''.join(tags)+(' ' if tags else '')+request.text
    return request.text


def applied_performance(request,backend):
    if backend not in ('fish','higgs'):return {}
    options=getattr(request,backend)
    return {**(options.model_dump() if options else {}),'rendered_text':rendered_text(request,backend)}


def qwen_sampling(model,request,reference,transcript):
    resolved=model._resolve_generation_options(request.temperature,request.max_tokens,None,None,None)
    icl=bool(reference and transcript.strip())
    if icl:resolved['repetition_penalty']=max(resolved['repetition_penalty'],1.5)
    return {'conditioning':'ICL' if icl else 'speaker embedding' if reference else 'preset' if request.preset_voice else 'voice design',
        'sampling':resolved,'icl_repetition_penalty_floor':1.5 if icl else None}


def spoken_transcript(record):
    text=record.get('text',record.get('transcript',''))
    if record.get('backend')=='fish':text=re.sub(r'\[[^\[\]]*\]\s*','',text)
    elif record.get('backend')=='higgs':text=re.sub(r'<\|[^|>]+\|>\s*','',text)
    return text.strip()
