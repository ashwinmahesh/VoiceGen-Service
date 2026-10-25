"""Small terminal-only model chooser; explicit and noninteractive commands never prompt."""
import argparse
import json
import os
from pathlib import Path
import sys
from backend_selection import BACKENDS


def choose_model(default, explicit=None, interactive=False, input_fn=input, stream=sys.stderr):
    if explicit:return explicit
    if not interactive:return default
    print('Choose a voice model (only the selected model is set up/served):',file=stream)
    for number,backend in enumerate(BACKENDS,1):
        print(f'  {number}. {backend}'+(' (default)' if backend==default else ''),file=stream)
    while True:
        print(f'Model [Enter = {default}]: ',end='',file=stream,flush=True)
        answer=input_fn().strip()
        if not answer:return default
        if answer in BACKENDS:return answer
        if answer.isdigit() and 1<=int(answer)<=len(BACKENDS):return BACKENDS[int(answer)-1]
        print('Choose a listed model ID or number.',file=stream)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['setup','serve'])
    parser.add_argument('--model',choices=BACKENDS)
    parser.add_argument('--non-interactive',action='store_true')
    parser.add_argument('--keep-venv',action='store_true')
    parser.add_argument('--keep-port',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    default=json.loads((root/'service.json').read_text())['backend']
    model=choose_model(default,args.model,sys.stdin.isatty() and not args.non_interactive)
    command=['make',args.action+'-selected','MODEL='+model]
    environment=os.environ.copy()
    if not args.keep_venv:environment.pop('VENV',None)
    if not args.keep_port:environment.pop('PORT',None)
    if args.dry_run:print(json.dumps({'model':model,'command':command,'venv':environment.get('VENV'),'port':environment.get('PORT')}));return
    os.chdir(root)
    os.execvpe('make',command,environment)


if __name__=='__main__':main()
