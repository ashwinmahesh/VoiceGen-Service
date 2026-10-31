"""Model-specific rendering, strict options, actual mappings and private artifact metadata; no GPU."""
import hashlib,json,sys,types
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import capabilities,engine,native_engine,server
from config import PROJECT_ROOT
from schemas import Generation,generation_metadata
from performance_controls import rendered_text,applied_performance,qwen_sampling,spoken_transcript,HIGGS_CATALOG
from . import test_contract


def profile(backend):return json.loads((PROJECT_ROOT/'backends'/backend/'service.json').read_text())
def request(**extra):return Generation(text='My guests. You came.',reference_id='a'*32,**extra)


class PerformanceTests(unittest.TestCase):
    def test_fish_exact_rendering_manual_local_tags_and_duplicates(self):
        raw='My guests. You came.';r=request(fish={'delivery_tags':['low voice','quietly amused']})
        self.assertEqual(rendered_text(r,'fish'),'[low voice] [quietly amused] '+raw)
        self.assertEqual(r.text,raw)
        self.assertEqual(rendered_text(r.model_copy(update={'fish':None}),'fish'),raw)
        local=r.model_copy(update={'text':'My guests. [pause] You came.'})
        self.assertEqual(rendered_text(local,'fish'),'[low voice] [quietly amused] My guests. [pause] You came.')
        for text in ['[whisper] My guests.','My guests. [low voice] You came.']:
            with self.assertRaises(ValueError):rendered_text(r.model_copy(update={'text':text}),'fish')
        for tags in [['a']*5,['a','A'],['[x]'],['x\ny'],['<|foo|>'],['x'*61],['\x00'],['']]:
            with self.assertRaises(ValueError):request(fish={'delivery_tags':tags})
    def test_higgs_official_enums_prefix_conflicts_and_local_controls(self):
        r=request(higgs={'emotion':'amusement','prosody':'expressive_low','style':'whispering'})
        self.assertEqual(rendered_text(r,'higgs'),'<|emotion:amusement|><|prosody:expressive_low|><|style:whispering|> '+r.text)
        self.assertEqual(len(HIGGS_CATALOG['emotion']),21);self.assertEqual(len(HIGGS_CATALOG['prosody']),8)
        for key,value in [('emotion','contempt'),('prosody','pause'),('style','Romanian')]:
            with self.assertRaises(ValueError):request(higgs={key:value})
        for text in ['<|emotion:anger|> My guests.',' <|emotion:amusement|> My guests.']:
            with self.assertRaises(ValueError):rendered_text(r.model_copy(update={'text':text}),'higgs')
        self.assertIn('<|prosody:pause|>',rendered_text(r.model_copy(update={'text':'My guests.<|prosody:pause|> You came.'}),'higgs'))
        for backend in ['fish','higgs']:
            for opts in [{'top_p':float('nan')},{'top_k':101},{'repetition_penalty':1.2},{'speed':.9},{'unknown':1}]:
                with self.assertRaises(ValueError):request(**{backend:opts})
            with patch('capabilities.PROFILE',profile('voxcpm2')):
                with self.assertRaises(ValueError):capabilities.validate_controls(request(**{backend:{}}))
    def test_fish_native_sampling_and_44100_reference_mapping(self):
        adapter=engine.Engine();adapter.model=Mock(sample_rate=44100)
        util=types.ModuleType('mlx_audio.utils');util.load_audio=Mock(return_value='wave-at-44100')
        with patch('engine.PROFILE',profile('fish')),patch.dict(sys.modules,{'mlx_audio.utils':util}):
            adapter.backend_call(request(fish={'delivery_tags':['low voice'],'top_p':.6,'top_k':25}),Path('/tmp/reference.wav'),'Exact reference words.')
        kwargs=adapter.model.generate.call_args.kwargs
        self.assertEqual(kwargs['text'],'[low voice] My guests. You came.')
        self.assertEqual(kwargs['top_p'],.6);self.assertEqual(kwargs['top_k'],25)
        self.assertEqual(kwargs['ref_text'],'Exact reference words.');self.assertEqual(kwargs['ref_audio'],'wave-at-44100')
        util.load_audio.assert_called_once_with('/tmp/reference.wav',sample_rate=44100)
        self.assertNotIn('repetition_penalty',kwargs);self.assertNotIn('speed',kwargs)
    def test_rendered_text_counts_toward_budget(self):
        adapter=engine.Engine();adapter.model=Mock();adapter.model.tokenizer.encode.side_effect=list
        r=Generation(text='x'*2000,reference_id='a'*32,fish={'delivery_tags':['y'*60]})
        with patch('engine.PROFILE',profile('fish')),patch('capabilities.PROFILE',profile('fish')):
            with self.assertRaises(ValueError):adapter.check_input_budget(r,Path('/tmp/reference.wav'),'abc')
            self.assertLess(len(adapter.check_input_budget(r.model_copy(update={'fish':None}),Path('/tmp/reference.wav'),'abc')),2048)
    def test_higgs_server_effective_options_no_incomplete_bypass(self):
        r=request(higgs={'emotion':'amusement','top_p':.75,'top_k':20})
        with patch('native_engine.PROFILE',profile('higgs')):
            body=native_engine.NativeEngine().request_body(r,Path('/tmp/ref.wav'),'Exact words.')
        self.assertEqual(body['input'],'<|emotion:amusement|> '+r.text)
        self.assertEqual((body['options']['top_p'],body['options']['top_k']),(.75,20))
        self.assertFalse(any('repetition' in key or 'allow_incomplete' in key for key in [*body,*body['options']]))
    def test_qwen_icl_floor_and_clean_reference_transcripts(self):
        model=SimpleNamespace(speech_tokenizer=SimpleNamespace(has_encoder=True),_resolve_generation_options=lambda *args:{'temperature':.9,'max_tokens':500,'top_k':50,'top_p':1.,'repetition_penalty':1.1})
        self.assertEqual(qwen_sampling(model,request(),Path('/tmp/ref'),'Exact words.')['sampling']['repetition_penalty'],1.5)
        self.assertEqual(qwen_sampling(model,request(),Path('/tmp/ref'),'')['sampling']['repetition_penalty'],1.1)
        self.assertEqual(spoken_transcript({'backend':'fish','text':'[low voice] My guests. [pause] You came.'}),'My guests. You came.')
        self.assertEqual(spoken_transcript({'backend':'higgs','text':'<|emotion:amusement|> My guests.'}),'My guests.')
    def test_chatterbox_consumption_and_namespace_only_legacy_aliases(self):
        with patch('capabilities.PROFILE',profile('chatterbox')):
            seconds=capabilities.capabilities().limits['reference_conditioning_seconds']
            self.assertEqual(seconds,{'speech_tokens':6,'decoder':10,'voice_encoder':'full waveform'})
        r=request(higgs={'emotion':'amusement'});self.assertEqual(r.emotion,'')
        self.assertEqual(generation_metadata(r)['higgs']['emotion'],'amusement')
        self.assertEqual(generation_metadata(r)['emotion'],'')


class PerformanceArtifactTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_contract.ContractTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.reference=self.fixture.upload().json()
    def test_auto_transcript_does_not_mislabel_a_clipped_or_truncated_recording(self):
        fixture=self.fixture
        admitted=fixture.generate(reference_id=self.reference['id'],ref_text='Recorded words.')
        self.assertEqual(admitted.status_code,202,admitted.text)
        take=fixture.finish(admitted.json())['result']
        path=Path(take['path']);sidecar=path.with_suffix('.json')
        original=json.loads(sidecar.read_text())
        path.write_bytes(test_contract.wav(seconds=31))
        # Stale duration metadata must not label a 30-second excerpt as the whole line.
        saved=fixture.client.post('/api/voices',json={'output_id':take['id'],'name':'Clipped recording'})
        self.assertEqual(saved.status_code,201,saved.text)
        self.assertEqual(saved.json()['transcript'],'')
        path.write_bytes(test_contract.wav(seconds=2))
        original['possibly_truncated']=True;sidecar.write_text(json.dumps(original))
        saved=fixture.client.post('/api/voices',json={'output_id':take['id'],'name':'Incomplete recording'})
        self.assertEqual(saved.status_code,201,saved.text)
        self.assertEqual(saved.json()['transcript'],'')
    def test_rendered_effective_metadata_requested_text_reuse_and_legacy_hash(self):
        fixture=self.fixture;original=fixture.engine.generate
        def generate(effective,*args,**kwargs):
            result=original(effective,*args,**kwargs);result.update({server.PROFILE['backend']:applied_performance(effective,server.PROFILE['backend'])});return result
        fixture.engine.generate=generate
        for backend,options in [('fish',{'delivery_tags':['low voice']}),('higgs',{'emotion':'amusement'})]:
            with patch('server.PROFILE',profile(backend)),patch('capabilities.PROFILE',profile(backend)),patch.object(fixture.manager,'profile',profile(backend)):
                body=dict(reference_id=self.reference['id'],ref_text='Exact reference words.',**{backend:options},idempotency_key=backend+'-quality')
                job=fixture.generate(**body).json();result=fixture.finish(job)['result']
                self.assertEqual(result['requested']['text'],'Welcome home.');self.assertEqual(result['effective']['text'],'Welcome home.')
                self.assertIn('rendered_text',result['effective'][backend]);self.assertEqual(result['effective']['emotion'],'')
                self.assertEqual(fixture.generate(**body).json()['id'],job['id'])
                changed={'delivery_tags':['quietly amused']} if backend=='fish' else {'emotion':'affection'}
                self.assertEqual(fixture.generate(**{**body,backend:changed}).status_code,409)
                self.assertEqual(fixture.engine.calls[-1][2],'Exact reference words.')
                # Automatic saved transcripts contain spoken words, not control prefixes.
                saved=fixture.client.post('/api/voices',json={'output_id':result['id'],'name':'Performance fixture'}).json()
                self.assertEqual(saved['transcript'],'Welcome home.')
        with patch('server.PROFILE',profile('fish')),patch('capabilities.PROFILE',profile('fish')),patch.object(fixture.manager,'profile',profile('fish')):
            body=dict(reference_id=self.reference['id'],ref_text='Exact words.',idempotency_key='pre-quality')
            job=fixture.generate(**body).json();fixture.finish(job)
            r=Generation(text='Welcome home.',description='A calm baritone.',**body)
            key=hashlib.sha256(b'pre-quality').hexdigest()
            old_hash=hashlib.sha256(r.model_dump_json(exclude={'model','idempotency_key','fish','higgs','qwen3','moss','prompt_expansion_id'}).encode()).hexdigest()
            with fixture.manager.store.transaction() as db:
                payload=json.loads(db.execute('SELECT payload FROM entries WHERE key_hash=?',(key,)).fetchone()[0])
                payload['requested'].pop('fish');payload['requested'].pop('higgs')
                db.execute('UPDATE entries SET fingerprint=?,payload=? WHERE key_hash=?',(old_hash,json.dumps(payload),key))
            self.assertEqual(fixture.generate(**body).json()['id'],job['id'])
            pending=fixture.manager.store.read(job['id'])['payload']['requested']
            self.assertNotIn('fish',pending);self.assertNotIn('higgs',pending)
            self.assertEqual(fixture.generate(**{**body,'fish':{'delivery_tags':['low voice']}}).status_code,409)

class AutoCloningTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_contract.ContractTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        for name in ['server.PROFILE','capabilities.PROFILE']:
            self.fixture.stack.enter_context(patch(name,profile('voxcpm2')))
        self.fixture.stack.enter_context(patch.object(self.fixture.manager,'profile',profile('voxcpm2')))
        self.reference=self.fixture.upload().json()
    def test_admission_resolves_auto_and_preserves_explicit_and_design(self):
        f=self.fixture
        for extra,wanted in [({'ref_text':'Exact words.'},'combined'),({},'reference'),
                ({'ref_text':'Exact words.','emotion':'calm'},'reference'),
                ({'ref_text':'Exact words.','accent':'Eastern European'},'reference'),
                ({'clone_mode':'reference'},'reference'),
                ({'clone_mode':'continuation','ref_text':'Exact words.'},'continuation'),
                ({'clone_mode':'combined','ref_text':'Exact words.'},'combined')]:
            response=f.generate(reference_id=self.reference['id'],**extra)
            self.assertEqual(response.status_code,202,response.text)
            result=f.finish(response.json())['result']
            self.assertEqual(result['requested']['clone_mode'],extra.get('clone_mode','auto'))
            self.assertEqual(result['effective']['clone_mode'],wanted)
        result=f.finish(f.generate().json())['result'];self.assertEqual(result['effective']['clone_mode'],'reference')
        from capabilities import resolve_clone_mode
        for backend in ['qwen3','chatterbox','indextts','fish','higgs','moss']:
            self.assertEqual(resolve_clone_mode(request(),backend,Path('/reference'),'Exact words.').clone_mode,'reference')
    def test_new_auto_and_explicit_reference_keys_are_distinct(self):
        f=self.fixture;body={'reference_id':self.reference['id'],'ref_text':'Exact words.','idempotency_key':'auto-new'}
        first=f.generate(**body);f.finish(first.json())
        self.assertEqual(f.generate(**{**body,'clone_mode':'reference'}).status_code,409)
        body.update(idempotency_key='explicit-new',clone_mode='reference',ref_text=None)
        first=f.generate(**body);f.finish(first.json());body.pop('clone_mode')
        self.assertEqual(f.generate(**body).status_code,409)
    def test_old_sqlite_retry_and_frozen_reference_are_preserved(self):
        f=self.fixture;body={'reference_id':self.reference['id'],'ref_text':'Exact words.','idempotency_key':'auto-old'}
        first=f.generate(**body);result=f.finish(first.json())
        old=Generation(text='Welcome home.',description='A calm baritone.',**body).model_copy(update={'clone_mode':'reference'})
        key=hashlib.sha256(b'auto-old').hexdigest()
        with f.manager.store.transaction() as db:
            payload=json.loads(db.execute('SELECT payload FROM entries WHERE key_hash=?',(key,)).fetchone()[0])
            payload.pop('fingerprint_version');payload['requested']['clone_mode']='reference';payload['effective']['clone_mode']='reference'
            payload['requested'].pop('fish');payload['requested'].pop('higgs')
            db.execute('UPDATE entries SET fingerprint=?,payload=? WHERE key_hash=?',(f.manager.fingerprint(old,{'fish','higgs'}),json.dumps(payload),key))
        self.assertEqual(f.generate(**body).json()['id'],first.json()['id'])
        stored=f.manager.store.read(first.json()['id'])['payload']
        self.assertEqual(stored['effective']['clone_mode'],'reference');self.assertNotIn('fish',stored['requested'])
        self.assertEqual(f.generate(**{**body,'clone_mode':'combined'}).status_code,409)
        self.assertEqual(f.generate(**{**body,'fish':{'delivery_tags':['low voice']}}).status_code,409)
    def test_default_values_and_explicit_overrides(self):
        for backend,temperature in [('chatterbox',.8),('indextts',.8),('fish',.7),('higgs',.8),('moss',1.5),('qwen3',.9),('voxcpm2',None)]:
            with patch('capabilities.PROFILE',profile(backend)):
                r=request(language='English' if backend in ('chatterbox','indextts') else 'auto') if backend!='moss' else Generation(text='Hello.',description='A calm baritone.',language='English')
                effective=capabilities.validate_controls(r)
                self.assertEqual(effective.temperature,temperature)
                if backend=='chatterbox':
                    self.assertEqual(effective.chatterbox.exaggeration,.3)
                    explicit=capabilities.validate_controls(request(chatterbox={'exaggeration':.5,'cfg_weight':.3},temperature=.7,language='English'))
                    self.assertEqual(explicit.chatterbox.exaggeration,.5);self.assertEqual(explicit.temperature,.7)
                if backend in ('fish','higgs'):
                    self.assertEqual(getattr(effective,backend).top_k,30)

    def test_pending_auto_is_concrete_and_catalog_edits_do_not_reresolve(self):
        import threading
        f=self.fixture;f.engine.gate=threading.Event()
        active=f.generate().json();self.assertTrue(f.engine.started.wait(2))
        pending=f.generate(reference_id=self.reference['id'],ref_text='Exact admitted words.').json()
        self.assertEqual(pending['effective']['clone_mode'],'combined')
        sidecar=f.root/'references'/f"{self.reference['id']}.json"
        stored=json.loads(sidecar.read_text());stored['transcript']='Different catalog words.';sidecar.write_text(json.dumps(stored))
        f.engine.gate.set();f.finish(active);result=f.finish(pending)['result']
        self.assertEqual(result['effective']['clone_mode'],'combined')
        self.assertEqual(result['reference']['transcript'],'Exact admitted words.')

    def test_legacy_json_reference_hash_accepts_old_omitted_mode_retry(self):
        from voice_catalog import atomic_json,now
        f=self.fixture;identity='d'*32;key=hashlib.sha256(b'json-auto-old').hexdigest()
        old=Generation(text='Welcome home.',description='A calm baritone.',clone_mode='reference')
        atomic_json(f.root/'jobs'/f'{identity}.json',{'id':identity,'status':'error','stage':'Legacy interrupted','created_at':now(),'updated_at':now()})
        atomic_json(f.root/'idempotency'/f'{key}.json',{'job':identity,'backend':f.manager.profile['backend'],'fingerprint':f.manager.fingerprint(old,{'fish','higgs'})})
        self.assertEqual(f.generate(idempotency_key='json-auto-old').json()['id'],identity)
        self.assertEqual(f.generate(idempotency_key='json-auto-old',clone_mode='combined').status_code,409)

    def test_qwen_transcribed_reference_cannot_silently_lose_encoder(self):
        adapter=engine.Engine();adapter.model=SimpleNamespace(speech_tokenizer=SimpleNamespace(has_encoder=False))
        with patch('engine.PROFILE',profile('qwen3')):
            with self.assertRaisesRegex(ValueError,'encoder'):adapter.backend_call(request(),Path('/reference'),'Exact words.')

class FrozenCharacterDefaultTests(unittest.TestCase):
    def test_old_chatterbox_default_is_not_recomputed_on_dispatch(self):
        from .test_queue import QueueFixture
        fixture=QueueFixture(methodName='runTest');fixture.setUp();self.addCleanup(fixture.tearDown)
        from .test_contract import wav
        reference=fixture.root/'references'/('a'*32+'.wav');reference.write_bytes(wav())
        old=fixture.manager(backend='chatterbox')
        requested=Generation(text='A full character line.',reference_id='a'*32,clone_mode='reference',language='English')
        old_effective=requested.model_copy(update={'temperature':.8,'chatterbox':__import__('schemas').ChatterboxOptions(exaggeration=.5,cfg_weight=.5)})
        job=old.submit(requested,old_effective,reference,'',fixture.root/'outputs');old.close()
        with patch('capabilities.PROFILE',profile('chatterbox')):
            self.assertEqual(capabilities.validate_controls(requested).chatterbox.exaggeration,.3)
        fresh=fixture.manager(backend='chatterbox');fresh.recover()
        self.assertEqual(fixture.done(fresh,job['id'])['status'],'done')
        self.assertEqual(fresh.engine.calls[0][0].exaggeration,.5)
