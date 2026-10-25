"""Backend selection, strict controls, native arguments and storage ownership. No GPU."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import os

from backend_selection import selected_backend, weights_directory, BACKENDS
import capabilities
from schemas import Generation
from config import PROJECT_ROOT, ROOT
from torch_engine import TorchEngine
from native_engine import NativeEngine
from data_owner import own_data_directory


class BackendTests(unittest.TestCase):
    def profile(self,backend):
        import json
        return json.loads((PROJECT_ROOT/'backends'/backend/'service.json').read_text())

    def request(self,**kwargs):
        return Generation(text='Welcome home.',reference_id='a'*32,**kwargs)

    def test_selection_precedence_and_unknown(self):
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
            for name in ['config.py','backend_selection.py','service.json','model_specs.json','extension_specs.json']:
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
                for field,value in [('steps',10),('guidance',2),('clone_mode','continuation'),('steering_strength',3)]:
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
                    self.assertEqual(effective.exaggeration,.5)
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
        with patch('server.all_errors',return_value=[]),patch('server.runtime_errors',return_value=['missing pinned runtime']):
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
