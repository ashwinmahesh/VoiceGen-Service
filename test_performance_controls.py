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
import test_contract


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
    def test_higgs_cli_effective_options_no_incomplete_bypass(self):
        r=request(higgs={'emotion':'amusement','top_p':.75,'top_k':20})
        with patch('native_engine.PROFILE',profile('higgs')),patch('native_engine.MODEL_SPECS',{'higgs':{'files':[{'path':'model.gguf'}]}}),patch('native_engine.Path.is_file',return_value=True):
            args=native_engine.NativeEngine().command(r,Path('/tmp/ref.wav'),'Exact words.',Path('/tmp/out.wav'))
        self.assertEqual(args[args.index('--text')+1],'<|emotion:amusement|> '+r.text)
        self.assertEqual(args[args.index('--top-p')+1],'0.75');self.assertIn('--top-k',args)
        self.assertFalse(any('repetition' in x or 'allow_incomplete' in x for x in args))
    def test_qwen_icl_floor_and_clean_reference_transcripts(self):
        model=SimpleNamespace(_resolve_generation_options=lambda *args:{'temperature':.9,'max_tokens':500,'top_k':50,'top_p':1.,'repetition_penalty':1.1})
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
            old_hash=hashlib.sha256(r.model_dump_json(exclude={'idempotency_key','fish','higgs'}).encode()).hexdigest()
            with fixture.manager.store.transaction() as db:
                payload=json.loads(db.execute('SELECT payload FROM entries WHERE key_hash=?',(key,)).fetchone()[0])
                payload['requested'].pop('fish');payload['requested'].pop('higgs')
                db.execute('UPDATE entries SET fingerprint=?,payload=? WHERE key_hash=?',(old_hash,json.dumps(payload),key))
            self.assertEqual(fixture.generate(**body).json()['id'],job['id'])
            pending=fixture.manager.store.read(job['id'])['payload']['requested']
            self.assertNotIn('fish',pending);self.assertNotIn('higgs',pending)
            self.assertEqual(fixture.generate(**{**body,'fish':{'delivery_tags':['low voice']}}).status_code,409)
