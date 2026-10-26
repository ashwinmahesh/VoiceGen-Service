"""Small terminal-only model chooser; explicit and noninteractive commands never prompt."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from backend_selection import BACKENDS, default_backend
from setup_lifecycle import SetupCancelled,announce_reuse,announce_cancelled,run_setup


def model_name(backend):
    profile=Path(__file__).resolve().parent/'backends'/backend/'service.json'
    return json.loads(profile.read_text())['name']


def show_unavailable(unavailable, stream=sys.stderr):
    if not unavailable:return
    print('\nNot set up or unavailable (not selectable):',file=stream)
    for backend in unavailable:
        print(f'  - {model_name(backend)} [{backend}] — make setup MODEL={backend}',file=stream)
    print('Run the setup command for a model you want, then rerun make serve.\n',file=stream)


def choose_model(default, explicit=None, interactive=False, input_fn=input, stream=sys.stderr, choices=None, serving=False, unavailable=None, setup_complete=()):
    if explicit:return explicit
    choices=tuple(BACKENDS if choices is None else choices)
    if not choices:raise ValueError('No set-up voice models are available. Run make setup first.')
    default=default if default in choices else choices[0]
    if not interactive:return default
    print('Choose the default voice model (all set-up models can run):' if serving else 'Choose a voice model to set up:',file=stream)
    for number,backend in enumerate(choices,1):
        badge=' [SETUP COMPLETE]' if not serving and backend in setup_complete else ''
        print(f'  {number}. {model_name(backend)} [{backend}]'+badge+(' (default)' if backend==default else ''),file=stream)
    if not serving:print('  q. Cancel setup (or press Ctrl+C)',file=stream)
    if serving:show_unavailable(unavailable,stream)
    while True:
        print(f'Model number or ID [Enter = {model_name(default)}]: ',end='',file=stream,flush=True)
        answer=input_fn().strip()
        if not serving and answer.casefold() in ('q','quit','cancel','exit'):
            raise SetupCancelled()
        if not answer:return default
        if answer in choices:return answer
        if answer.isdigit() and 1<=int(answer)<=len(choices):return choices[int(answer)-1]
        print('Choose a listed model ID or number.',file=stream)


def runtime_python(root, backend, original_backend, venv_override=None):
    folder=venv_override or ('.venv' if backend==original_backend else '.venv-mlx' if backend in ('voxcpm2','qwen3','fish') else '.venv-native' if backend in ('moss','higgs') else '.venv-'+backend)
    path=Path(folder).expanduser()
    return (path if path.is_absolute() else Path(root)/path)/'bin/python'


def choose_llm(disabled=False,interactive=False,input_fn=input,stream=sys.stderr):
    if disabled:return False
    if not interactive:return True
    print('Prompt expansion uses a small language model on your Mac to refine voice descriptions and acting directions without changing the words spoken.',file=stream)
    while True:
        print('Enable local prompt expansion? [Y/n]: ',end='',file=stream,flush=True)
        answer=input_fn().strip().casefold()
        if answer in ('','y','yes'):return True
        if answer in ('n','no'):return False
        print('Enter Y or N; Enter enables prompt expansion.',file=stream)


def setup_errors(root, backend, original_backend, venv_override=None):
    """Read verified assets and runtime versions in the correct environment, without model loading."""
    python=runtime_python(root,backend,original_backend,venv_override)
    if not python.is_file():return [f'Python environment missing: {python.parent.parent}']
    environment={**os.environ,'MODEL_BACKEND':backend}
    owner=os.environ.get('VOICEGEN_OVERRIDE_BACKEND',os.environ.get('MODEL_BACKEND'))
    if owner and backend!=owner:
        for key in ('WEIGHTS_DIR','EXTENSIONS_DIR'):environment.pop(key,None)
    probe='''import json,platform,sys,sysconfig
try:
    from config import ROOT,MODEL_DIR,EXTENSION_DIR,PROFILE,validate_mount
    from python_runtime import compatible,requirement,runtime_for
    role=runtime_for(PROFILE['backend'])
    if not compatible(role,dict(version=sys.version_info[:3],implementation=sys.implementation.name,
            system=platform.system(),machine=platform.machine(),free_threaded=bool(sysconfig.get_config_var('Py_GIL_DISABLED')))):
        raise RuntimeError(requirement(role))
    for path in (ROOT,MODEL_DIR,EXTENSION_DIR):validate_mount(path)
    from assets import all_errors
    from runtime_checks import runtime_errors
    print(json.dumps(all_errors()+runtime_errors()))
except Exception as error:
    print(json.dumps([str(error)]))
'''
    try:
        result=subprocess.run([str(python),'-c',probe],cwd=root,env=environment,capture_output=True,text=True,timeout=8)
        if result.returncode:return [(result.stderr.strip() or 'Runtime readiness check failed')[-500:]]
        return json.loads(result.stdout)
    except (OSError,subprocess.TimeoutExpired,ValueError) as error:return [f'Cannot verify setup: {error}']


def ready_models(root, original_backend, venv_override=None):
    ready=[];unavailable={}
    for backend in BACKENDS:
        errors=setup_errors(root,backend,original_backend,venv_override)
        if errors:unavailable[backend]=errors
        else:ready.append(backend)
    return ready,unavailable


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['setup','serve'])
    parser.add_argument('--model',choices=BACKENDS)
    parser.add_argument('--non-interactive',action='store_true')
    parser.add_argument('--keep-venv',action='store_true')
    parser.add_argument('--keep-port',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--no-llm',action='store_true')
    args=parser.parse_args()
    if args.action=='setup':announce_reuse()
    root=Path(__file__).resolve().parent
    original=json.loads((root/'service.json').read_text())['backend']
    default=default_backend()
    if args.action=='serve':
        from queue_config import QUEUE_DB
        import sqlite3
        if QUEUE_DB.is_file():
            try:
                with sqlite3.connect('file:'+str(QUEUE_DB)+'?mode=ro',uri=True) as db:
                    row=db.execute("SELECT value FROM queue_settings WHERE key='default_model'").fetchone()
                    if row:default=json.loads(row[0])
            except sqlite3.OperationalError:pass
    interactive=sys.stdin.isatty() and not args.non_interactive
    override=os.environ.get('VENV') if args.keep_venv else None
    choices=None
    unavailable={}
    setup_complete=[]
    if args.action=='setup' and not args.model and interactive:
        setup_complete,_=ready_models(root,original,override)
    if args.action=='serve' and not args.model and (interactive or not args.dry_run):
        choices,unavailable=ready_models(root,original,override)
        if not choices:
            show_unavailable(unavailable)
            detail=next(iter(unavailable.values()),['No installed assets'])[0]
            raise SystemExit('No set-up voice models are ready. '+detail+'\nRun make setup, or reconnect the configured model drive.')
        if default not in choices:
            print(f'{model_name(default)} is not ready; Enter will select {model_name(choices[0])}.',file=sys.stderr)
    model=choose_model(default,args.model,interactive,choices=choices,serving=args.action=='serve',unavailable=unavailable,setup_complete=setup_complete)
    if args.action=='serve' and not args.dry_run:
        errors=setup_errors(root,model,original,override)
        if errors:raise SystemExit(f'{model_name(model)} is not ready: {errors[0]}\nRun make setup MODEL={model}, or reconnect its configured model drive.')
    command=['make',args.action+'-selected','MODEL='+model]
    if args.action=='serve' and not choose_llm(args.no_llm,interactive):command.append('NO_LLM=1')
    environment=os.environ.copy()
    if not args.keep_venv:environment.pop('VENV',None)
    if not args.keep_port:environment.pop('PORT',None)
    if args.dry_run:print(json.dumps({'model':model,'command':command,'venv':environment.get('VENV'),'port':environment.get('PORT')}));return
    os.chdir(root)
    if args.action=='setup':
        status=run_setup(command,environment,root)
        if status:raise SystemExit(status if status>0 else 128-status)
        return
    os.execvpe('make',command,environment)


def cli():
    try:main()
    except (SetupCancelled,KeyboardInterrupt,EOFError):
        if len(sys.argv)<2 or sys.argv[1]!='setup':raise
        announce_cancelled()
    return 0


if __name__=='__main__':raise SystemExit(cli())
