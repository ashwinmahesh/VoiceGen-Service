"""Voice expansion contracts, authority boundaries, shared reservations and frozen reuse. No GPU."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import capabilities
import prompt_expansion as expansion
import server
from schemas import Generation,CandidateGeneration
from . import test_contract


class ExpansionTests(unittest.TestCase):
    def setUp(self):
        self.f=test_contract.ContractTests('runTest');self.f.setUp();self.addCleanup(self.f.tearDown)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(server.app.state,'llm_enabled',True))
        self.stack.enter_context(patch('prompt_expansion.check_assets',return_value=Path('/model')))
        self.stack.enter_context(patch('prompt_expansion.check_runtime',return_value=Path('/python')))
        self.calls=[];self.completion='{"addition":"Measured phrasing with restrained emphasis."}'
        def worker(*args,**kwargs):
            data=json.loads(kwargs['input']);self.calls.append(data)
            self.assertNotIn('text',data['plan']);self.assertNotIn('ref_text',data['plan'])
            self.assertIsNone(self.f.engine.model)
            self.assertTrue(kwargs['pass_fds'])
            for fd in kwargs['pass_fds']:os.fstat(fd)
            return SimpleNamespace(returncode=0,stdout=json.dumps({'completion':self.completion}))
        self.worker=self.stack.enter_context(patch('prompt_expansion.subprocess.run',side_effect=worker))
    def preview(self,generation=None,**kwargs):
        return self.f.client.post('/api/prompts/expand',json={'generation':generation or
            {'text':'  DIALOGUE sentinel.\nDo not rewrite this!  ','description':'A calm cultured baritone.'},**kwargs})
    def test_preview_apply_and_candidate_reuse_keep_spoken_text_verbatim(self):
        text='  DIALOGUE sentinel.\nDo not rewrite this!  '
        self.f.engine.model=object()
        response=self.preview();self.assertEqual(response.status_code,200,response.text);record=response.json()
        self.assertNotIn('DIALOGUE sentinel',json.dumps(self.calls))
        self.assertEqual(record['original'],{'description':'A calm cultured baritone.'})
        self.assertEqual(record['llm']['voxcpm2']['id'],'voxcpm2-voice-guidance')
        self.assertEqual(record['llm']['voxcpm2']['mode'],'design')
        self.assertEqual(record['llm']['voxcpm2']['source'],'backends/voxcpm2/prompt_expansion.json')
        self.assertTrue(record['updates']['description'].startswith(record['original']['description']+'\n'))
        self.assertEqual(self.f.client.get('/api/queue').json()['total'],0)
        request={'text':text,**record['updates'],'prompt_expansion_id':record['id'],'candidate_count':2,'candidate_variation':'seed_only','idempotency_key':'expanded-pair'}
        batch=self.f.client.post('/api/candidates',json=request);self.assertEqual(batch.status_code,202,batch.text)
        for job in batch.json()['jobs']:
            done=self.f.finish(job);self.assertEqual(done['status'],'done')
            self.assertEqual(done['result']['text'],text)
            self.assertEqual(done['result']['prompt_expansion'],record)
        self.assertEqual(len(self.calls),1)
        with patch.object(server.app.state,'llm_enabled',False):
            replay=self.f.client.post('/api/generate',json={k:v for k,v in request.items() if k not in ('candidate_count','candidate_variation','idempotency_key')})
            self.assertEqual(replay.status_code,202,replay.text);self.f.finish(replay.json())
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.f.client.post('/api/candidates',json=request).json()['id'],batch.json()['id'])
        self.assertEqual(self.f.client.post('/api/candidates',json={**request,'idempotency_key':'edited','description':'Different'}).status_code,422)
        self.assertEqual(len(self.calls),1)
    def test_reference_transcript_is_not_sent_and_auto_combined_is_not_changed(self):
        transcript='  REFERENCE sentinel.\nExact recorded words.  '
        reference=self.f.client.post('/api/references',files={'file':('voice.wav',test_contract.wav(),'audio/wav')},data={'transcript':transcript}).json()
        base={'text':'DIALOGUE sentinel.','reference_id':reference['id']}
        rejected=self.preview(base);self.assertEqual(rejected.status_code,422);self.assertEqual(self.calls,[])
        request={**base,'clone_mode':'reference','emotion':'Quietly amused.'}
        response=self.preview(request);self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('REFERENCE sentinel',json.dumps(self.calls));self.assertNotIn('DIALOGUE sentinel',json.dumps(self.calls))
        record=response.json();self.assertEqual(set(record['updates']),{'emotion'})
        generated=self.f.client.post('/api/generate',json={**request,**record['updates'],'prompt_expansion_id':record['id']})
        self.assertEqual(generated.status_code,202,generated.text);self.f.finish(generated.json())
        self.assertEqual(self.f.engine.calls[-1][2],transcript)
    def test_unsupported_modes_and_disabled_startup_fail_before_worker(self):
        reference=self.f.upload().json()
        for backend in ('qwen3','chatterbox','indextts'):
            profile=json.loads((server.PROJECT_ROOT/'backends'/backend/'service.json').read_text())
            with patch('server.PROFILE',profile),patch('capabilities.PROFILE',profile),patch.object(self.f.manager,'profile',profile):
                response=self.preview({'text':'Words.','reference_id':reference['id'],'language':'English'})
                self.assertEqual(response.status_code,422,response.text)
        with patch.object(server.app.state,'llm_enabled',False):
            self.assertEqual(self.preview().status_code,422)
        self.assertEqual(self.calls,[])
    def test_strict_schema_and_output_limits_reject_model_authority_overreach(self):
        for completion in ['{"addition":"ok","text":"changed"}','{"addition":"a","addition":"b"}',
                           '```json\n{"addition":"ok"}\n```','{"addition":"<think>bad</think>"}',
                           '{"addition":"[laugh]"}',json.dumps({'addition':'x'*601}),json.dumps({'addition':'a '*61}),'[]','{"addition":1}']:
            self.completion=completion
            response=self.preview();self.assertEqual(response.status_code,422,response.text)
        self.assertFalse(list((self.f.root/'prompt-expansions').glob('*.json')))
    def test_frozen_context_and_empty_suggestion(self):
        base={'text':'Exact words.','description':'A calm voice.','emotion':'Quietly amused.','accent':'Romanian-accented English.'}
        response=self.preview(base);self.assertEqual(response.status_code,200,response.text);record=response.json()
        self.assertEqual(self.calls[-1]['plan']['delivery_context'],base['emotion'])
        for change in [{'emotion':'Shouting.'},{'accent':'Neutral accent.'}]:
            rejected=self.f.client.post('/api/generate',json={**base,**record['updates'],**change,'prompt_expansion_id':record['id']})
            self.assertEqual(rejected.status_code,422,rejected.text)
        self.completion='{"addition":""}'
        self.assertEqual(self.preview(base).json()['updates'],{'description':base['description']})
    def test_higgs_rejects_category_mismatch_and_allows_no_suggestion(self):
        prompt={'kind':'higgs'}
        with self.assertRaises(ValueError):expansion.updates_for(prompt,{},'{"control":"style","value":"amusement"}')
        self.assertEqual(expansion.updates_for(prompt,{},'{"control":"none","value":null}'),
                         {'higgs':{'emotion':None,'prosody':None,'style':None}})
    def test_busy_shared_lock_and_runtime_failures_preserve_original(self):
        import fcntl,subprocess
        with self.f.manager.lock_file.open('a+') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.preview().status_code,409)
        self.assertEqual(self.calls,[])
        with patch('prompt_expansion.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',180)):
            self.assertEqual(self.preview().status_code,422)
        self.assertFalse(self.f.manager.expanding)
        self.assertIsNone(self.f.manager.active)
        self.assertEqual(self.f.client.get('/api/queue').json()['total'],0)
    def test_structured_fish_higgs_suggestions_use_existing_controls(self):
        reference=self.f.upload().json()
        for backend,completion in [('fish','{"delivery_tags":["quietly amused"]}'),
                                   ('higgs','{"control":"emotion","value":"amusement"}')]:
            profile=json.loads((server.PROJECT_ROOT/'backends'/backend/'service.json').read_text())
            self.completion=completion
            with patch('server.PROFILE',profile),patch('capabilities.PROFILE',profile),patch.object(self.f.manager,'profile',profile):
                request={'text':'Exact dialogue.','reference_id':reference['id'],'ref_text':'Exact recorded words.'}
                response=self.preview(request,guidance='Quietly amused.');self.assertEqual(response.status_code,200,response.text)
                record=response.json();self.assertEqual(set(record['updates']),{backend})
                effective=expansion.apply(Generation(**request),record['updates'])
                self.assertEqual(effective.text,request['text']);self.assertEqual(effective.ref_text,request['ref_text'])
                self.assertEqual(self.preview({**request,backend:record['updates'][backend]},guidance='More acting.').status_code,422)
    def test_previous_single_and_candidate_fingerprints_ignore_new_omitted_field(self):
        from jobs import JobManager
        for request in [Generation(text='Hello.',description='A voice.'),CandidateGeneration(text='Hello.',description='A voice.')]:
            dumped=request.model_dump_json(exclude={'model','idempotency_key','prompt_expansion_id','qwen3','moss'})
            if isinstance(request,CandidateGeneration):
                dumped=request.model_dump_json(exclude={'model','idempotency_key','prompt_expansion_id','candidate_variation','candidate_use_llm'})
                actual=JobManager.candidate_fingerprint(request)
            else:actual=JobManager.fingerprint(request)
            self.assertEqual(actual,hashlib.sha256(dumped.encode()).hexdigest())
    def test_startup_capability_and_make_flags(self):
        self.assertTrue(self.f.client.get('/api/capabilities').json()['limits']['prompt_expansion']['enabled'])
        source=(server.PROJECT_ROOT/'Makefile').read_text()
        self.assertIn('setup-llm',source);self.assertIn('$(LLM_FLAG)',source)
    def test_asset_readiness_rejects_truncated_weights_without_loading(self):
        import prompt_assets as assets
        folder=self.f.root/'llm';folder.mkdir()
        for name in assets.FILES:(folder/name).write_text('{}')
        (folder/'download.json').write_text(json.dumps(dict(repository=assets.REPOSITORY,revision=assets.REVISION,weights_sha256=assets.WEIGHTS_SHA256)))
        with patch.object(assets,'weights_directory',return_value=folder),patch.object(assets,'WEIGHTS_BYTES',2):
            self.assertEqual(assets.check_assets(),folder)
            (folder/'model.safetensors').write_bytes(b'x')
            with self.assertRaisesRegex(RuntimeError,'weight size'):assets.check_assets()
    def test_first_download_then_repeated_setup_reuses_verified_llm(self):
        import prompt_assets as assets
        from unittest.mock import Mock
        folder=self.f.root/'new-llm';weight_bytes=b'fixture weights'
        def snapshot(**kwargs):
            self.assertEqual(kwargs['repo_id'],assets.REPOSITORY)
            self.assertEqual(kwargs['revision'],assets.REVISION)
            for name in kwargs['allow_patterns']:(folder/name).write_bytes(weight_bytes if name=='model.safetensors' else b'{}')
        download=Mock(side_effect=snapshot)
        with patch.object(assets,'weights_directory',return_value=folder),patch.object(assets,'WEIGHTS_BYTES',len(weight_bytes)),\
             patch.object(assets,'WEIGHTS_SHA256',hashlib.sha256(weight_bytes).hexdigest()),\
             patch.dict('sys.modules',{'huggingface_hub':SimpleNamespace(snapshot_download=download)}),patch.dict(os.environ):
            assets.download()
            timestamp=(folder/'model.safetensors').stat().st_mtime_ns
            assets.download()
            self.assertEqual(download.call_count,1)
            self.assertEqual((folder/'model.safetensors').stat().st_mtime_ns,timestamp)
            self.assertEqual(assets.check_assets(full=True),folder)
    def test_backend_mode_profiles_select_native_rules_and_publish_namespaced_specs(self):
        from prompt_policy import messages_for
        from prompt_profiles import load_profile,BACKENDS
        profiles={backend:load_profile(backend) for backend in BACKENDS}
        self.assertEqual(len({profile['id'] for profile in profiles.values()}),7)
        caps=self.f.client.get('/api/capabilities').json()
        self.assertEqual(caps['models']['voxcpm2']['prompt_expansion'],profiles['voxcpm2'])
        cases=[('voxcpm2','design',{}),('voxcpm2','reference',{'reference_id':'a'*32}),
               ('qwen3','design',{}),('qwen3','preset',{'preset_voice':'ryan'}),('moss','design',{}),
               ('fish','reference',{'reference_id':'a'*32}),('higgs','reference',{'reference_id':'a'*32})]
        for backend,mode,values in cases:
            with self.subTest(backend=backend,mode=mode):
                request=Generation(text='DIALOGUE sentinel',description='A calm voice.',emotion='Quiet amusement.',
                                   ref_text='REFERENCE sentinel',clone_mode='reference',**values)
                _,_,prompt=expansion.plan(request,request,backend,'Restrained amusement.')
                selected=prompt['profile_spec'];expected=profiles[backend]['modes'][mode]
                self.assertEqual(selected['instructions'],expected['instructions'])
                self.assertEqual(selected['sha256'],profiles[backend]['sha256'])
                messages=messages_for(prompt)
                self.assertIn(expected['instructions'],messages[0]['content'])
                self.assertNotIn('DIALOGUE sentinel',json.dumps(messages));self.assertNotIn('REFERENCE sentinel',json.dumps(messages))
        from prompt_profiles import mode_spec
        for backend,mode,clone in [('voxcpm2','reference','combined'),('voxcpm2','reference','continuation'),
                                  ('qwen3','reference','reference'),('chatterbox','reference','reference'),('indextts','reference','reference')]:
            with self.assertRaises(ValueError):mode_spec(backend,mode,clone)
    def test_profile_limits_and_native_capability_cannot_be_overridden(self):
        import prompt_profiles
        profile=prompt_profiles.load_profile('voxcpm2');profile={k:v for k,v in profile.items() if k not in ('source','sha256')}
        for invalid in ['limit','target','native-path']:
            value=json.loads(json.dumps(profile))
            if invalid=='limit':value['modes']['design']['limits']['addition_characters']=601
            elif invalid=='target':value['modes']['design']['target']='text'
            else:value.update(id='chatterbox-voice-guidance',backend='chatterbox')
            with patch.object(Path,'read_bytes',return_value=json.dumps(value).encode()):
                with self.assertRaises(ValueError):prompt_profiles.load_profile('chatterbox' if invalid=='native-path' else 'voxcpm2')
        prompt={'kind':'prose','target':'description','profile_spec':{'limits':{'addition_characters':10,'addition_words':2}}}
        with self.assertRaises(ValueError):expansion.updates_for(prompt,{'description':'Voice.'},'{"addition":"Three new words"}')
