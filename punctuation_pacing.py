"""Explicit phrase synthesis and added silence, never guessed edits inside generated speech."""
import re

BACKENDS=('fish','indextts','chatterbox','qwen3')
DEFAULT_SENTENCE_MS=500
DEFAULT_COMMA_MS=200
MAX_PHRASES=32
ABBREVIATIONS={'mr','mrs','ms','dr','prof','sr','jr','st','vs','etc','e.g','i.e','a.m','p.m'}
OPAQUE=re.compile(r'\[[^\[\]]*\]|<[^<>]*>|(?:https?://|www\.)\S+|\b[\w.+-]+@[\w.-]+\.[a-zA-Z]+')


def phrases(text,sentence_ms,comma_ms):
    """Conservative textual boundaries; retain punctuation, quotes, tags and spoken words."""
    protected=[(m.start(),m.end()) for m in OPAQUE.finditer(text)]
    rows=[];start=0;i=0
    while i<len(text):
        if any(a<=i<b for a,b in protected):i+=1;continue
        char=text[i]
        kind='comma' if char in ',，、' else 'sentence' if char in '.!?。！？…' else None
        delay=comma_ms if kind=='comma' else sentence_ms if kind else 0
        if not delay:i+=1;continue
        if char in ',.' and i and i+1<len(text) and text[i-1].isdigit() and text[i+1].isdigit():i+=1;continue
        if char=='.':
            word=re.search(r'([\w.]+)$',text[:i])
            word=word[1].casefold() if word else ''
            if word in ABBREVIATIONS or re.fullmatch(r'(?:[a-z]\.)*[a-z]',word):i+=1;continue
        end=i+1
        while end<len(text) and text[end] in '.!?。！？…':end+=1
        while end<len(text) and text[end] in '\"\u201d\u2019\')\u300d\u300f':end+=1
        # Latin punctuation inside words/domains is not a phrase boundary.
        if char in ',.!?' and end<len(text) and not text[end].isspace():i=end;continue
        rest=text[end:].strip()
        if rest and re.search(r'\w',OPAQUE.sub('',rest)):
            rows.append({'text':text[start:end].strip(),'after':kind,'pause_ms':delay});start=end
        i=end
    tail=text[start:].strip()
    if tail:rows.append({'text':tail,'after':None,'pause_ms':0})
    if len(rows)>MAX_PHRASES:raise ValueError(f'Longer punctuation pauses allow at most {MAX_PHRASES} phrases; shorten the passage or disable punctuation_pauses')
    return rows


def resolve(request,backend,option_type):
    """Resolve only at admission: old queued settings with absent controls stay whole-line."""
    if backend not in (*BACKENDS,'moss'):return request
    options=getattr(request,backend) or option_type()
    available=backend=='moss' or bool(request.voice_id or request.reference_id or request.preset_voice)
    manual_tags=backend=='fish' and bool(re.search(r'\[[^\[\]]*\]',request.text))
    if options.punctuation_pauses is True and (not available or manual_tags):
        raise ValueError('Punctuation pacing needs a reference/fixed speaker and no manual Fish tags; disable punctuation_pauses for whole-line synthesis')
    enabled=options.punctuation_pauses if options.punctuation_pauses is not None else available and not manual_tags
    values={'punctuation_pauses':enabled,
        'sentence_pause_ms':options.sentence_pause_ms if options.sentence_pause_ms is not None else DEFAULT_SENTENCE_MS,
        'comma_pause_ms':options.comma_pause_ms if options.comma_pause_ms is not None else DEFAULT_COMMA_MS}
    options=options.model_copy(update=values)
    if enabled and backend!='moss':phrases(request.text,options.sentence_pause_ms,options.comma_pause_ms)
    return request.model_copy(update={backend:options})


def moss_instructions(request):
    from directions import instructions
    base=instructions(request,None)
    options=request.moss
    if options is None or options.punctuation_pauses is not True:return base
    sentence=DEFAULT_SENTENCE_MS if options.sentence_pause_ms is None else options.sentence_pause_ms
    comma=DEFAULT_COMMA_MS if options.comma_pause_ms is None else options.comma_pause_ms
    return base+' Delivery pacing: keep the same voice throughout. Allow deliberate pauses of about '+str(sentence/1000)+' seconds between sentences and about '+str(comma/1000)+' seconds after commas. Keep the words themselves fluent and expressive.'


def synthesize(request,backend,generate,on_stage=None):
    """Generate with the same immutable request/reference; insert zeros only between calls."""
    import numpy as np
    options=getattr(request,backend,None)
    enabled=backend in BACKENDS and options is not None and options.punctuation_pauses is True
    rows=phrases(request.text,options.sentence_pause_ms,options.comma_pause_ms) if enabled else [{'text':request.text,'after':None,'pause_ms':0}]
    pieces=[];sample_rate=None;frames=0;details=[]
    for index,row in enumerate(rows):
        if on_stage and len(rows)>1:on_stage(f'Generating phrase {index+1} of {len(rows)}')
        part=request.model_copy(update={'text':row['text']})
        sr,data=generate(part)
        data=np.asarray(data,dtype=np.float32).reshape(-1)
        if sample_rate is not None and sr!=sample_rate:raise RuntimeError('Phrase sample rates differ')
        sample_rate=int(sr)
        if sample_rate<=0 or not data.size or not np.isfinite(data).all() or float(np.max(np.abs(data)))<1e-5:
            raise RuntimeError('A phrase produced empty, nonfinite or silent audio; no complete take was saved')
        added=round(row['pause_ms']*sample_rate/1000)
        pieces.append(data)
        if added:pieces.append(np.zeros(added,dtype=np.float32))
        from performance_controls import rendered_text
        details.append({**row,'model_input_text':rendered_text(part,backend),'start_sample':frames,'audio_samples':len(data),'inserted_samples':added})
        frames+=len(data)+added
        if frames/sample_rate>600:raise RuntimeError('Paced output exceeds 600 seconds; shorten the passage')
    return sample_rate,np.concatenate(pieces),{'strategy':'punctuation_segments' if len(rows)>1 else 'whole_line',
        'enabled':enabled,'extra_sentence_pause_ms':options.sentence_pause_ms if enabled else 0,
        'extra_comma_pause_ms':options.comma_pause_ms if enabled else 0,
        'automatic_audio_alignment':False,'speech_samples_trimmed':False,'phrases':details}
