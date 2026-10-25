"""Small real inference. No cloud upload; saves the returned job result in logs."""
import argparse
import json
import time
from uuid import uuid4
import urllib.request
from config import DEFAULT_PORT, ROOT


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=DEFAULT_PORT)
    parser.add_argument('--voice-id')
    args=parser.parse_args()
    url=f'http://127.0.0.1:{args.port}'
    def call(path,body=None):
        request=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,
                                      headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=15) as response:return json.load(response)
    body={'text':'Welcome home. It is good to hear your voice again.',
          'description':'A mature man with a warm, smooth baritone. Calm and welcoming.',
          'emotion':'' if args.voice_id else 'Warm and gently amused.',
          'seed':42,'max_tokens':160,'idempotency_key':'smoke-'+uuid4().hex}
    if args.voice_id:body['voice_id']=args.voice_id
    job=call('/api/generate',body)
    print('Job:',job['id'],flush=True)
    started=time.monotonic()
    while job['status'] in ['queued','running']:
        if time.monotonic()-started>600:raise SystemExit('Timed out; generation continues. Recover with GET /api/jobs/'+job['id'])
        time.sleep(2)
        job=call('/api/jobs/'+job['id'])
    if job['status']!='done':raise SystemExit(job.get('error'))
    import numpy as np
    import soundfile as sf
    data,sr=sf.read(job['result']['path'])
    assert len(data)>sr and np.isfinite(data).all() and np.max(np.abs(data))>1e-5
    (ROOT/'logs'/f'smoke-{job["id"]}.json').write_text(json.dumps(job,indent=2))
    result=job['result'].copy();result.pop('waveform',None)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
