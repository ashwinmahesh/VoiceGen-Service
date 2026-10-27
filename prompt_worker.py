"""Short-lived, offline MLX-LM worker; stdout is a bounded JSON protocol."""
import contextlib
import json
import resource
import sys
from prompt_policy import messages_for,higgs_completion
from lifecycle_events import measure, observe, pipe_emitter
from prompt_assets import REPOSITORY


def run(request):
    from mlx_lm import load
    backend=request.get('backend','unknown')
    with measure('load','llm_weights',backend,REPOSITORY,'prompt_expansion'):
        model,tokenizer=load(request['weights'])
    try:return generate_loaded(request,model,tokenizer)
    finally:
        try:
            with measure('unload','llm_weights',backend,REPOSITORY,'prompt_expansion'):
                model=tokenizer=None
                import gc
                import mlx.core as mx
                gc.collect();mx.clear_cache()
        except Exception:
            pass  # This short-lived worker still exits; telemetry must not mask its result/error.


def generate_loaded(request,model,tokenizer):
    from mlx_lm import stream_generate
    from mlx_lm.sample_utils import make_sampler
    candidates=request.get('task')=='candidate_variants'
    if candidates:
        batches=request['plan']['messages']
        if not isinstance(batches,list) or not 1<=len(batches)<=4:raise ValueError('Invalid contrast count')
    else:batches=[messages_for(request['plan'])]
    completions=[];peak=0
    for messages in batches:
        formatted=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=False)
        text='';last=None
        for response in stream_generate(model,tokenizer,prompt=formatted,max_tokens=320,sampler=make_sampler(temp=0)):
            text+=response.text;last=response
            if len(text)>4096:raise ValueError('Output too large')
        if last is None or last.finish_reason=='length':raise ValueError('Output did not finish')
        if not candidates and request['plan']['kind']=='higgs':text=higgs_completion(text,request['plan']['allowed_values'])
        completions.append(text);peak=max(peak,last.peak_memory)
    return {('completions' if candidates else 'completion'):completions if candidates else completions[0],
            'peak_mlx_memory_gb':round(peak,3),
            'peak_process_rss_gb':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1e9 if sys.platform=='darwin' else 1e6),3)}



if __name__=='__main__':
    try:
        request=json.loads(sys.stdin.read(131073))
        with contextlib.redirect_stdout(sys.stderr),observe(pipe_emitter()):result=run(request)
        print(json.dumps(result,allow_nan=False))
    except Exception:
        print(json.dumps({'error':'Local voice guidance expansion failed or reached its limit; no speech was generated.'}))
        sys.exit(1)
