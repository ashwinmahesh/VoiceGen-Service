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
    parser.add_argument('--reference',help='Local authorized reference audio to upload')
    parser.add_argument('--transcript',default='')
    parser.add_argument('--text',default='Welcome home. It is good to hear your voice again.')
    parser.add_argument('--description',default='A mature man with a warm, smooth baritone. Calm and welcoming.')
    parser.add_argument('--backend',choices=__import__('backend_selection').BACKENDS)
    args=parser.parse_args()
    url=f'http://127.0.0.1:{args.port}'
    def call(path,body=None):
        request=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,
                                      headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=15) as response:return json.load(response)
    caps=call('/api/capabilities')
    body={'text':args.text,'description':args.description,'emotion':'',
          'language':'auto' if 'auto' in caps['languages'] else 'English',
          'seed':42,'max_tokens':1000,'idempotency_key':'smoke-'+uuid4().hex}
    if args.voice_id:body['voice_id']=args.voice_id
    if args.reference:
        import httpx
        from pathlib import Path
        with Path(args.reference).open('rb') as audio:
            response=httpx.post(url+'/api/references',files={'file':(Path(args.reference).name,audio)},
                                data={'transcript':args.transcript},timeout=30)
        response.raise_for_status();body['reference_id']=response.json()['id']
    elif caps['modes']['design'].get('available',True) is False and not args.voice_id:
        raise SystemExit('This backend requires --reference or --voice-id')
    job=call('/api/generate',body)
    print('Job:',job['id'],flush=True)
    started=time.monotonic()
    while job['status'] in ['queued','running']:
        if time.monotonic()-started>1800:raise SystemExit('Timed out; generation continues. Recover with GET /api/jobs/'+job['id'])
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
