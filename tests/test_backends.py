"""Backend selection, strict controls, native arguments and storage ownership. No GPU."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import os

from backend_selection import selected_backend, weights_directory, BACKENDS, default_backend
import capabilities
from schemas import Generation
from config import PROJECT_ROOT, ROOT
from torch_engine import TorchEngine
from native_engine import NativeEngine
from data_owner import own_data_directory


class BackendTests(unittest.TestCase):
    def test_llm_startup_default_interactive_choice_and_explicit_opt_out(self):
        import io
        from startup_select import choose_llm
        self.assertTrue(choose_llm())
        explanation=io.StringIO()
        self.assertTrue(choose_llm(interactive=True,input_fn=lambda:'',stream=explanation))
        self.assertIn('on your Mac',explanation.getvalue())
        self.assertIn('without changing the words spoken',explanation.getvalue())
        self.assertFalse(choose_llm(interactive=True,input_fn=lambda:'N',stream=io.StringIO()))
        self.assertFalse(choose_llm(disabled=True,interactive=True,input_fn=lambda:self.fail('Explicit opt-out must skip input')))
        answers=iter(['wrong','y'])
        self.assertTrue(choose_llm(interactive=True,input_fn=lambda:next(answers),stream=io.StringIO()))
        import subprocess,sys
        base=[sys.executable,str(PROJECT_ROOT/'startup_select.py'),'serve','--model','higgs','--non-interactive','--dry-run']
        import json
        self.assertNotIn('NO_LLM=1',json.loads(subprocess.check_output(base,text=True))['command'])
        self.assertIn('NO_LLM=1',json.loads(subprocess.check_output(base+['--no-llm'],text=True))['command'])

    def profile(self,backend):
        import json
        return json.loads((PROJECT_ROOT/'backends'/backend/'service.json').read_text())

    def test_static_model_registry_matches_backend_profiles(self):
        import json
        models=json.loads((PROJECT_ROOT/'model_registry.json').read_text())
        self.assertEqual(len({model['id'] for model in models}),len(models))
        self.assertIn(default_backend(),{model['id'] for model in models})
        self.assertEqual({model['id'] for model in models},
                         {path.parent.name for path in (PROJECT_ROOT/'backends').glob('*/service.json')})
        for model in models:
            profile=self.profile(model['id'])
            self.assertEqual((model['id'],model['name']),(profile['backend'],profile['name']))

    def request(self,**kwargs):
        return Generation(text='Welcome home.',reference_id='a'*32,**kwargs)

    def test_pause_workflow_capability_survives_all_backends(self):
        for backend in BACKENDS:
            with patch('capabilities.PROFILE',self.profile(backend)):
                caps=capabilities.capabilities()
                self.assertTrue(caps.limits['pause_adjustment']['reviewed_spans'])
                self.assertFalse(caps.limits['pause_adjustment']['automatic_alignment'])
                self.assertEqual(caps.modes['reference'].get('combined',False),backend=='voxcpm2')
                self.assertEqual({key:caps.limits['candidates'][key] for key in ('min','max','default')},
                                 {'min':1,'max':5,'default':2})

    def test_interactive_model_selection_defaults_overrides_and_pty(self):
        import io,json,subprocess,sys,pty
        from startup_select import choose_model
        self.assertEqual(choose_model('qwen3',interactive=False),'qwen3')
        self.assertEqual(choose_model('voxcpm2','fish',True,input_fn=lambda: self.fail('unexpected prompt')),'fish')
        answers=iter(['typo','3'])
        self.assertEqual(choose_model('voxcpm2',interactive=True,input_fn=lambda:next(answers),stream=io.StringIO()),'chatterbox')
        import config
        from startup_select import model_name
        for action,answer,expected in [('setup','',default_backend()),('setup','7','higgs')]:
            with self.subTest(action=action,answer=answer):
                master,slave=pty.openpty()
                try:
                    process=subprocess.Popen([sys.executable,str(PROJECT_ROOT/'startup_select.py'),action,'--dry-run'],
                        stdin=slave,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                    os.close(slave);slave=None;os.write(master,(answer+'\n').encode())
                    out,err=process.communicate(timeout=10)
                    self.assertEqual(process.returncode,0);picked=json.loads(out)
                    self.assertEqual(picked['model'],expected)
                    self.assertEqual(picked['command'],['make',action+'-selected','MODEL='+expected])
                    self.assertIn('Enter = '+model_name(default_backend()),err)
                    for backend in BACKENDS:self.assertIn(model_name(backend)+' ['+backend+']',err)
                finally:
                    os.close(master)
                    if slave is not None:os.close(slave)
        env={**os.environ,'VENV':'/temporary/auto-venv','PORT':'8111'}
        base=[sys.executable,str(PROJECT_ROOT/'startup_select.py'),'serve','--model','fish','--non-interactive','--dry-run']
        picked=json.loads(subprocess.check_output(base,env=env,text=True));self.assertIsNone(picked['venv']);self.assertIsNone(picked['port'])
        picked=json.loads(subprocess.check_output(base+['--keep-venv','--keep-port'],env=env,text=True));self.assertEqual(picked['venv'],env['VENV']);self.assertEqual(picked['port'],'8111')

    def test_serve_menu_contains_only_ready_models_with_available_default(self):
        import io
        from startup_select import choose_model
        output=io.StringIO()
        self.assertEqual(choose_model('voxcpm2',interactive=True,input_fn=lambda:'',stream=output,
                                      choices=['voxcpm2','higgs'],serving=True,unavailable={'qwen3':['missing assets']}),'voxcpm2')
        self.assertIn('VoxCPM2 [voxcpm2] (default)',output.getvalue())
        self.assertIn('Higgs TTS 3 [higgs]',output.getvalue())
        self.assertIn('  2. Higgs TTS 3 [higgs]',output.getvalue())
        self.assertIn('  - Qwen3-TTS [qwen3] — make setup MODEL=qwen3',output.getvalue())
        self.assertNotIn('  3.',output.getvalue())
        self.assertIn('not selectable',output.getvalue())
        answers=iter(['qwen3','2'])
        self.assertEqual(choose_model('voxcpm2',interactive=True,input_fn=lambda:next(answers),stream=io.StringIO(),
                                      choices=['voxcpm2','higgs'],serving=True),'higgs')
        self.assertEqual(choose_model('voxcpm2',interactive=True,input_fn=lambda:'',stream=io.StringIO(),
                                      choices=['higgs'],serving=True),'higgs')
        with self.assertRaises(ValueError):choose_model('voxcpm2',choices=[])

    def test_setup_menu_marks_currently_ready_models_and_keeps_every_choice(self):
        import io
        from startup_select import choose_model,model_name
        for complete in [[],['voxcpm2','higgs'],list(BACKENDS)]:
            output=io.StringIO()
            self.assertEqual(choose_model('higgs',interactive=True,input_fn=lambda:'',stream=output,
                                          setup_complete=complete),'higgs')
            lines=output.getvalue().splitlines()
            for number,backend in enumerate(BACKENDS,1):
                line=next(line for line in lines if line.startswith(f'  {number}. '))
                self.assertIn(model_name(backend)+' ['+backend+']',line)
                self.assertEqual('[SETUP COMPLETE]' in line,backend in complete)
                self.assertEqual('(default)' in line,backend=='higgs')
        self.assertEqual(choose_model('higgs',interactive=True,input_fn=lambda:'3',stream=io.StringIO(),
                                     setup_complete=['higgs']),'chatterbox')

    def test_setup_status_scans_only_for_interactive_menu_and_respects_venv_override(self):
        import io,json,sys,startup_select
        from contextlib import redirect_stdout
        for args,interactive,scans in [(['setup','--dry-run'],True,True),
                                       (['setup','--model','higgs','--dry-run'],True,False),
                                       (['setup','--non-interactive','--dry-run'],True,False),
                                       (['setup','--dry-run'],False,False)]:
            output=io.StringIO()
            with self.subTest(args=args,interactive=interactive),patch.object(sys,'argv',['startup_select.py',*args]), \
                 patch('startup_select.sys.stdin.isatty',return_value=interactive), \
                 patch('startup_select.ready_models',return_value=(['higgs'],{'voxcpm2':['missing volume']})) as ready, \
                 patch('startup_select.choose_model',return_value='higgs') as choose,redirect_stdout(output):
                startup_select.main()
                self.assertEqual(ready.called,scans)
                self.assertEqual(choose.call_args.kwargs['setup_complete'],['higgs'] if scans else [])
        with patch.object(sys,'argv',['startup_select.py','setup','--keep-venv','--dry-run']), \
             patch('startup_select.sys.stdin.isatty',return_value=True), \
             patch.dict(os.environ,{'VENV':'/chosen/environment'}), \
             patch('startup_select.ready_models',return_value=([],{})) as ready, \
             patch('startup_select.choose_model',return_value='higgs'),redirect_stdout(io.StringIO()):
            startup_select.main()
            original=json.loads((PROJECT_ROOT/'service.json').read_text())['backend']
            ready.assert_called_once_with(PROJECT_ROOT,original,'/chosen/environment')

    def test_ready_discovery_respects_runtime_and_asset_failures_without_loading(self):
        import startup_select
        def errors(root,backend,original,override):
            return [] if backend in ('voxcpm2','higgs') else ['missing runtime or verified assets']
        with patch('startup_select.setup_errors',side_effect=errors):
            ready,unavailable=startup_select.ready_models(PROJECT_ROOT,'voxcpm2')
        self.assertEqual(ready,['voxcpm2','higgs']);self.assertEqual(len(unavailable),5)
        self.assertEqual(startup_select.runtime_python(PROJECT_ROOT,'qwen3','voxcpm2'),PROJECT_ROOT/'.venv-mlx/bin/python')
        self.assertEqual(startup_select.runtime_python(PROJECT_ROOT,'higgs','voxcpm2'),PROJECT_ROOT/'.venv-native/bin/python')
        self.assertEqual(startup_select.runtime_python(PROJECT_ROOT,'voxcpm2','voxcpm2'),PROJECT_ROOT/'.venv/bin/python')
        with patch('startup_select.subprocess.run') as run:
            self.assertTrue(startup_select.setup_errors(PROJECT_ROOT,'higgs','voxcpm2','/missing-voice-menu-fixture'))
            run.assert_not_called()

    def test_readiness_rejects_unsupported_python_before_loading_assets_or_inference(self):
        import shutil,sys,startup_select
        parent=PROJECT_ROOT/'state/validation';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='setup-status-',dir=parent) as folder:
            root=Path(folder)
            backend='higgs' if sys.version_info.minor>=13 else 'voxcpm2' if sys.version_info.minor<=11 else 'indextts'
            python=startup_select.runtime_python(root,backend,'voxcpm2')
            python.parent.mkdir(parents=True);python.symlink_to(sys.executable)
            shutil.copyfile(PROJECT_ROOT/'python_runtime.py',root/'python_runtime.py')
            (root/'config.py').write_text('from pathlib import Path\nROOT=MODEL_DIR=EXTENSION_DIR=Path(__file__).parent\n'
                +f'PROFILE={{"backend":{backend!r}}}\n'+'def validate_mount(path):return path\n')
            for module in ['assets.py','runtime_checks.py']:
                (root/module).write_text('raise AssertionError("Readiness should stop before asset/inference imports")\n')
            errors=startup_select.setup_errors(root,backend,'voxcpm2')
            self.assertEqual(len(errors),1)
            self.assertIn('needs standard CPython',errors[0])

    def test_serve_selection_fails_cleanly_before_launch_when_setup_is_missing(self):
        import io,sys,startup_select
        with patch.object(sys,'argv',['startup_select.py','serve','--non-interactive']),patch('startup_select.setup_errors',return_value=['incomplete model']),patch('startup_select.os.execvpe') as launch:
            with self.assertRaisesRegex(SystemExit,'make setup MODEL='):
                startup_select.main()
            launch.assert_not_called()
        with patch.object(sys,'argv',['startup_select.py','serve']),patch('startup_select.sys.stdin.isatty',return_value=True),patch('startup_select.ready_models',return_value=([],{'voxcpm2':['volume is not mounted']})),patch('startup_select.os.execvpe') as launch:
            with self.assertRaisesRegex(SystemExit,'No set-up voice models are ready'):
                startup_select.main()
            launch.assert_not_called()

    def test_typed_model_options_inactive_unknown_and_legacy_aliases(self):
        from schemas import generation_metadata
        for backend,values in [('chatterbox',{'exaggeration':.7}),('indextts',{'duration_factor':1.2})]:
            with patch('capabilities.PROFILE',self.profile(backend)):
                request=self.request(**{backend:values},language='English')
                effective=capabilities.validate_controls(request)
                record=generation_metadata(effective)
                self.assertEqual(record[backend][next(iter(values))],next(iter(values.values())))
                self.assertNotIn('exaggeration',record);self.assertNotIn('duration_factor',record)
                self.assertIn(backend,capabilities.capabilities().controls)
                with self.assertRaises(ValueError):self.request(**{backend:{'unknown':1}})
                with self.assertRaises(ValueError):self.request(**{backend:values,**values})
                with self.assertRaises(ValueError):capabilities.validate_controls(self.request(**{'indextts' if backend=='chatterbox' else 'chatterbox':{}},language='English'))
        with self.assertRaises(ValueError):self.request(indextts={'emotion_vector':[float('nan')]*8})
        with self.assertRaises(ValueError):self.request(indextts={'emotion_vector':[.2]*8})
        with patch('capabilities.PROFILE',self.profile('voxcpm2')):
            with self.assertRaises(ValueError):capabilities.validate_controls(self.request(chatterbox={}))

    def test_truthful_nonblocking_model_license_notices(self):
        from license_notices import notice_for
        for backend in ['voxcpm2','qwen3','chatterbox','moss']:self.assertFalse(notice_for(backend))
        self.assertIn('commercial',notice_for('fish')['notice'])
        self.assertIn('Creator Use Grant',notice_for('higgs')['notice'])
        self.assertIn('100 million',notice_for('indextts')['notice'])
        for backend in ['fish','higgs','indextts']:
            import json
            specs=json.loads((PROJECT_ROOT/'backends'/backend/'model_specs.json').read_text())
            with patch('capabilities.PROFILE',self.profile(backend)),patch('capabilities.MODEL_SPECS',specs):
                self.assertIn('license_url',capabilities.capabilities().models[backend])
                self.assertEqual(capabilities.capabilities().models[backend]['commercial_use_notice'],notice_for(backend)['commercial_use_notice'])

    def test_license_warning_at_every_startup_is_conditional_and_nonblocking(self):
        import asyncio,logging,server
        from contextlib import nullcontext
        from license_notices import notice_for
        async def start_twice():
            for _ in range(2):
                async with server.lifespan(server.app):pass
        for backend in BACKENDS:
            with self.subTest(backend=backend),patch('server.PROFILE',self.profile(backend)),patch('server.prepare_storage'),patch('server.manager',Mock()) as manager,patch('data_owner.own_data_directory',side_effect=lambda *args:nullcontext(object())):
                notice=notice_for(backend)
                if notice:
                    with self.assertLogs(level=logging.WARNING) as output:asyncio.run(start_twice())
                    self.assertEqual(len(output.output),2)
                    for message in output.output:
                        self.assertIn('MODEL LICENSE WARNING',message)
                        self.assertIn(notice['license_url'],message)
                        self.assertIn(notice['commercial_use_notice'],' '.join(message.splitlines()[2:-2]))
                else:
                    with self.assertNoLogs(level=logging.WARNING):asyncio.run(start_twice())
                self.assertEqual(manager.recover.call_count,2)
                self.assertEqual(manager.close.call_count,2)

    def test_managed_start_surfaces_license_warning_in_launching_terminal(self):
        import io,manage,sys
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'logs').mkdir()
            state={'service':'fish','project':str(PROJECT_ROOT),'storage':str(root)}
            process=Mock(pid=12345);process.poll.return_value=None
            with patch.object(sys,'argv',['manage.py','start','--port','8113']),patch('manage.PROFILE',self.profile('fish')),patch('manage.ROOT',root),patch('manage.QUEUE_DB',root/'state/queue.sqlite3'),patch('manage.prepare_storage'),patch('prompt_assets.check_assets'),patch('prompt_assets.check_runtime'),patch('manage.health',side_effect=[None,state]),patch('manage.socket.socket'),patch('manage.subprocess.Popen',return_value=process) as launch,redirect_stdout(io.StringIO()):
                with self.assertLogs(level='WARNING') as output:manage.main()
                self.assertIn('MODEL LICENSE WARNING',output.output[0])
                self.assertIn('Fish S2 Pro',output.output[0])
                self.assertEqual((root/'state/server-8113.pid').read_text(),'12345')
                launch.assert_called_once()

    def test_selection_precedence_and_unknown(self):
        with patch.dict(os.environ):
            os.environ.pop('MODEL_BACKEND',None)
            self.assertEqual(selected_backend([]),'higgs')
        with patch.dict(os.environ,{'MODEL_BACKEND':'fish'}):
            self.assertEqual(selected_backend([]),'fish')
            self.assertEqual(selected_backend(['--backend','higgs']),'higgs')
        with patch.dict(os.environ,{'MODEL_BACKEND':'typo'}):
            with self.assertRaises(ValueError):selected_backend([])
        for backend in BACKENDS:self.assertEqual(selected_backend(['--backend',backend]),backend)

    def test_explicit_original_backend_reuses_existing_weights(self):
        root=Path('/absolute/existing/data')
        for original in ['voxcpm2','qwen3']:
            self.assertEqual(weights_directory(root,None,original),root/'weights')
            self.assertEqual(weights_directory(root,original,original),root/'weights')
            self.assertEqual(weights_directory(root,'fish',original),root/'weights/fish')
            self.assertEqual(weights_directory(root,original,original,'/absolute/shared/weights'),Path('/absolute/shared/weights'))

    def test_config_saved_asset_mapping_override_and_missing_volume(self):
        import json
        import shutil
        import subprocess
        import sys
        with tempfile.TemporaryDirectory(dir=ROOT/'cache/tmp') as tmp:
            project=Path(tmp)/'project';project.mkdir()
            for name in ['config.py','backend_selection.py','service.json','model_specs.json','extension_specs.json','model_registry.json','service_defaults.json']:
                shutil.copyfile(PROJECT_ROOT/name,project/name)
            shutil.copytree(PROJECT_ROOT/'backends/qwen3',project/'backends/qwen3')
            data=Path(tmp)/'data'
            settings={'data_dir':str(data),'backend_weights':{'qwen3':str(Path(tmp)/'shared-weights')},
                      'backend_extensions':{'qwen3':str(Path(tmp)/'shared-extensions')}}
            (project/'storage.json').write_text(json.dumps(settings))
            env={key:value for key,value in os.environ.items() if key not in ('DATA_DIR','WEIGHTS_DIR','EXTENSIONS_DIR','MODEL_BACKEND')}
            env['MODEL_BACKEND']='qwen3'
            code='import config,json; print(json.dumps([str(config.MODEL_DIR),str(config.EXTENSION_DIR)]))'
            def paths():return json.loads(subprocess.check_output([sys.executable,'-c',code],cwd=project,env=env,text=True))
            self.assertEqual(paths(),[settings['backend_weights']['qwen3'],settings['backend_extensions']['qwen3']])
            env['WEIGHTS_DIR']=str(Path(tmp)/'explicit');self.assertEqual(paths()[0],env['WEIGHTS_DIR'])
            del env['WEIGHTS_DIR']
            settings['backend_weights']['qwen3']='/Volumes/unavailable-voice-backend-test/weights'
            (project/'storage.json').write_text(json.dumps(settings))
            result=subprocess.run([sys.executable,'-c','import config; config.prepare_storage()'],cwd=project,env=env,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0);self.assertIn('not mounted',result.stderr)
            self.assertFalse(data.exists())

    def test_reject_controls_per_backend_and_apply_defaults(self):
        for backend in ['chatterbox','indextts','moss','fish','higgs']:
            with patch('capabilities.PROFILE',self.profile(backend)):
                for field,value in [('steps',10),('guidance',2),('clone_mode','continuation'),('clone_mode','combined'),('steering_strength',3)]:
                    with self.assertRaises(ValueError):capabilities.validate_controls(self.request(**{field:value}))
                if backend=='moss':
                    request=Generation(text='Welcome home.',description='A calm baritone.',language='English')
                    with self.assertRaises(ValueError):capabilities.validate_controls(self.request(language='English'))
                else:
                    request=self.request(language='English' if backend=='indextts' else 'auto')
                    with self.assertRaises(ValueError):capabilities.validate_controls(self.request(emotion='Angry'))
                    with self.assertRaises(ValueError):capabilities.validate_controls(self.request(accent='Romanian'))
                effective=capabilities.validate_controls(request)
                self.assertIsNotNone(effective.temperature)
                caps=capabilities.capabilities()
                self.assertEqual(caps.modes['design']['available'],backend=='moss')
                if backend=='chatterbox':
                    self.assertEqual(effective.exaggeration,.3)
                    with self.assertRaises(ValueError):capabilities.validate_controls(self.request(max_tokens=160))
                if backend=='indextts':
                    self.assertEqual(effective.interval_silence,400)
                    with self.assertRaises(ValueError):capabilities.validate_controls(self.request(language='English',emotion_alpha=.5))

    def test_mlx_rejects_new_torch_controls(self):
        for backend in ['voxcpm2','qwen3']:
            with patch('capabilities.PROFILE',self.profile(backend)):
                with self.assertRaises(ValueError):capabilities.validate_controls(self.request(exaggeration=.5))
                with self.assertRaises(ValueError):capabilities.validate_controls(self.request(emotion_vector=[0]*8))

    def test_emotion_vector_and_conflicts(self):
        for vector in [[0]*7,[float('nan')]*8,[.2]*8,[-.1]+[0]*7]:
            with self.assertRaises(ValueError):self.request(emotion_vector=vector)
        with self.assertRaises(ValueError):self.request(emotion_vector=[0]*8,emotion_reference_id='b'*32)

    def test_api_invalid_vectors_return_json_safe_validation_errors(self):
        import json
        from fastapi.testclient import TestClient
        import server
        client=TestClient(server.app)
        for vector in [[.2]*8,[float('nan')]*8,[-.1]+[0]*7]:
            response=client.post('/api/generate',content=json.dumps({'text':'Welcome.',
                'reference_id':'a'*32,'emotion_vector':vector}),headers={'Content-Type':'application/json'})
            self.assertEqual(response.status_code,422,response.text)
            self.assertIn('detail',response.json())

    def test_runtime_mismatch_is_not_advertised_as_ready(self):
        from fastapi.testclient import TestClient
        import server
        with patch('server.manager.store.listing',return_value={'counts':{}}),patch('server.all_errors',return_value=[]),patch('server.runtime_errors',return_value=['missing pinned runtime']):
            response=TestClient(server.app).get('/health').json()
            self.assertEqual(response['status'],'error')
            self.assertTrue(response['downloaded'])
            self.assertEqual(response['runtime_errors'],['missing pinned runtime'])

    def test_index_native_control_forwarding(self):
        adapter=TorchEngine();adapter.model=Mock()
        import numpy as np
        adapter.model.infer.return_value=(22050,np.array([[100,-100]],dtype=np.int16))
        with patch('torch_engine.PROFILE',self.profile('indextts')):
            sr,data=adapter.backend_call(self.request(language='English',temperature=.8,duration_factor=1.2,
                interval_silence=650,emotion_vector=[0,0,0,0,0,0,0,1],emotion_alpha=.7),Path('/tmp/voice.wav'))
        kwargs=adapter.model.infer.call_args.kwargs
        self.assertEqual(kwargs['duration_factor'],1.2);self.assertEqual(kwargs['interval_silence'],650)
        self.assertFalse(kwargs['text_normalization']);self.assertEqual(kwargs['lang'],'en')
        self.assertTrue(np.max(np.abs(data))<1);self.assertEqual(sr,22050)

    def test_native_command_preserves_text_and_uses_metal(self):
        import json
        for backend in ['moss','higgs']:
            specs=json.loads((PROJECT_ROOT/'backends'/backend/'model_specs.json').read_text())
            with patch('native_engine.PROFILE',self.profile(backend)),patch('native_engine.MODEL_SPECS',specs),patch('native_engine.Path.is_file',return_value=True):
                request=self.request(language='English',temperature=1.,seed=2**32-1)
                args=NativeEngine().command(request,Path('/tmp/ref.wav'),'Exact words.',Path('/tmp/out.wav'))
                self.assertEqual(args[args.index('--text')+1],request.text)
                self.assertEqual(args[args.index('--backend')+1],'metal')
                if backend=='higgs':self.assertEqual(args[args.index('--reference-text')+1],'Exact words.')

    def test_data_dir_exclusion_before_recovery(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'cache/tmp') as tmp:
            root=Path(tmp);(root/'jobs').mkdir()
            with own_data_directory(root):
                with self.assertRaisesRegex(RuntimeError,'owns this DATA_DIR'):
                    with own_data_directory(root):self.fail('second owner')
            with own_data_directory(root):pass


if __name__=='__main__':unittest.main()
