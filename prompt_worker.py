"""Short-lived, offline MLX-LM worker; stdout is a bounded JSON protocol."""
import contextlib
import json
import resource
import sys
from prompt_policy import messages_for


def run(request):
    from mlx_lm import load,stream_generate
    from mlx_lm.sample_utils import make_sampler
    model,tokenizer=load(request['weights'])
    messages=messages_for(request['plan'])
    formatted=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=False)
    text='';last=None
    for response in stream_generate(model,tokenizer,prompt=formatted,max_tokens=320,sampler=make_sampler(temp=0)):
        text+=response.text;last=response
        if len(text)>4096:raise ValueError('Output too large')
    if last is None or last.finish_reason=='length':raise ValueError('Output did not finish')
    return {'completion':text,'peak_mlx_memory_gb':round(last.peak_memory,3),
            'peak_process_rss_gb':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1e9 if sys.platform=='darwin' else 1e6),3)}


if __name__=='__main__':
    try:
        request=json.loads(sys.stdin.read(32769))
        with contextlib.redirect_stdout(sys.stderr):result=run(request)
        print(json.dumps(result,allow_nan=False))
    except Exception:
        print(json.dumps({'error':'Local voice guidance expansion failed or reached its limit; no speech was generated.'}))
        sys.exit(1)
