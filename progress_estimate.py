"""Estimated speech progress shared by the generator and queue APIs; no inference imports.

Speech models stop at their own end marker, so they report no step count and
max_tokens is only a ceiling. When a take starts generating, the generator freezes
an expected duration learned from recent successful takes of the same model. Both
services derive the same percentage from that saved estimate and the clock.
"""
from datetime import datetime, timezone
from itertools import combinations
import math
from statistics import median

HISTORY_TAKES = 20
FIT_TAKES = 3
CEILING = 95
# Rough starting points (generation seconds per character of dialogue) used only until
# this project has a finished take for the model; measured on an Apple Silicon Mac.
DEFAULT_SECONDS_PER_CHARACTER = {'higgs':.23,'voxcpm2':.3,'qwen3':.1,'chatterbox':.35,
                                 'indextts':.5,'moss':.35,'fish':.22}
FALLBACK_SECONDS_PER_CHARACTER = .3
FINISHED = ('done','error','deleted')


def samples(results):
    """(characters, generation_seconds) pairs from newest-first finished results."""
    pairs=[]
    for result in results:
        if not isinstance(result,dict):continue
        text,seconds=result.get('text'),result.get('generation_seconds')
        if (isinstance(text,str) and text.strip() and isinstance(seconds,(int,float)) and not isinstance(seconds,bool)
                and math.isfinite(seconds) and seconds>0):
            pairs.append((len(text),float(seconds)))
    return pairs[:HISTORY_TAKES]


def expected_seconds(characters, exact=(), backend_wide=(), backend=None):
    """Return (seconds, basis, sample count) for a take of this many characters.

    Prefer the exact model variant once it has enough takes. A robust line
    (Theil-Sen: median pairwise slope, then median intercept) keeps per-take
    overhead such as native per-output loading from skewing short or long lines.
    """
    exact,backend_wide=list(exact),list(backend_wide)
    history=exact if len(exact)>=FIT_TAKES else max(exact,backend_wide,key=len)
    if len(history)>=FIT_TAKES and len({count for count,_ in history})>1:
        slope=median((s2-s1)/(c2-c1) for (c1,s1),(c2,s2) in combinations(history,2) if c2!=c1)
        if slope>0:
            overhead=max(0.,median(seconds-slope*count for count,seconds in history))
            return max(1.,overhead+slope*characters),'history',len(history)
    if history:
        return max(1.,median(seconds/count for count,seconds in history)*characters),'history',len(history)
    rate=DEFAULT_SECONDS_PER_CHARACTER.get(backend,FALLBACK_SECONDS_PER_CHARACTER)
    return max(1.,rate*characters),'default',0


def started(characters, exact=(), backend_wide=(), backend=None, now=None):
    """The record saved on a running entry when its speech generation begins."""
    seconds,basis,count=expected_seconds(characters,exact,backend_wide,backend)
    return {'generating_since':(now or datetime.now(timezone.utc)).isoformat(),'expected_seconds':round(seconds,3),
            'basis':basis,'samples':count,'characters':characters}


def take_progress(status, saved, now=None):
    """Public progress for one take; null unless it is running or done."""
    if status=='done':
        return {'percent':100,'phase':'done','estimated':False,'eta_seconds':0,'overdue':False,'basis':None}
    if status!='running':return None
    preparing={'percent':0,'phase':'preparing','estimated':True,'eta_seconds':None,'overdue':False,'basis':None}
    if not isinstance(saved,dict) or not saved.get('expected_seconds'):return preparing
    try:since=datetime.fromisoformat(saved['generating_since'])
    except (KeyError,TypeError,ValueError):return preparing
    expected=float(saved['expected_seconds'])
    elapsed=max(0.,((now or datetime.now(timezone.utc))-since).total_seconds())
    overdue=elapsed>expected
    return {'percent':min(CEILING,int(100*elapsed/expected)),'phase':'generating','estimated':True,
            'eta_seconds':None if overdue else math.ceil(expected-elapsed),'overdue':overdue,'basis':saved.get('basis')}


def batch_progress(rows, now=None):
    """Sequential takes: each finished take counts fully, plus the running take's share."""
    if not rows:return None
    statuses=[row['status'] for row in rows]
    if 'queued' not in statuses and 'running' not in statuses:
        return take_progress('done',None) if 'done' in statuses else None
    finished=sum(status in FINISHED for status in statuses)
    running=next((row for row in rows if row['status']=='running'),None)
    if running is None and not finished:return None
    current=take_progress('running',running.get('progress'),now) if running else None
    eta=None
    if current and current['eta_seconds'] is not None:
        eta=current['eta_seconds']+statuses.count('queued')*math.ceil(running['progress']['expected_seconds'])
    return {'percent':int((finished*100+(current['percent'] if current else 0))/len(rows)),
            'phase':current['phase'] if current else 'waiting','estimated':True,'eta_seconds':eta,
            'overdue':bool(current and current['overdue']),'basis':current['basis'] if current else None}
