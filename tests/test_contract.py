"""No downloads or MLX inference. Exercise persistence, resource lifetime, and both adapters."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
import server
import storage
from jobs import JobManager
from schemas import Generation
from config import PROJECT_ROOT, ROOT


def wav(rate=16000,channels=1,seconds=2):
    data=np.sin(np.arange(rate*seconds)*2*np.pi*220/rate).astype('float32')*.2
    if channels>1:data=np.tile(data[:,None],(1,channels))
    stream=BytesIO();sf.write(stream,data,rate,format='WAV');return stream.getvalue()


class FakeEngine:
    def __init__(self):
        self.model=None;self.variant=None;self.calls=[];self.gate=None;self.started=threading.Event();self.fail=False
    def generate(self,request,reference,destination,on_stage,transcript=''):
        self.calls.append((request,reference,transcript));self.started.set();on_stage('Generating speech')
        if self.gate:self.gate.wait(5)
        if self.fail:raise RuntimeError('fake backend failure')
        destination.write_bytes(wav())
        self.model=object();self.variant='fake'
        return {'duration':2.,'sample_rate':16000,'generation_seconds':.1,'waveform':[.2]*10}
    def unload(self):self.model=None;self.variant=None


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'cache/tmp')
        self.root=Path(self.tmp.name)
        for name in ['outputs','voices','references/originals','jobs','artifact-index','idempotency','cache/tmp']:
            (self.root/name).mkdir(parents=True,exist_ok=True)
        self.engine=FakeEngine();self.manager=JobManager(self.engine,self.root,self.root/'gpu.lock')
        self.stack=ExitStack()
        self.stack.enter_context(patch.object(server.app.state,'llm_enabled',False))
        for target,value in [('server.ROOT',self.root),('storage.ROOT',self.root),('storage.OUTPUT_DIR',self.root/'outputs'),
                             ('server.engine',self.engine),('server.manager',self.manager),('server.all_errors',lambda:[]),('server.runtime_errors',lambda:[]),
                             ('server.prepare_storage',lambda:None)]:
            self.stack.enter_context(patch(target,value))
        self.client=TestClient(server.app);self.client.__enter__()
    def tearDown(self):
        if self.engine.gate:self.engine.gate.set()
        self.client.__exit__(None,None,None);self.stack.close();self.tmp.cleanup()
    def generate(self,**extra):
        return self.client.post('/api/generate',json={'text':'Welcome home.','description':'A calm baritone.',**extra})
    def finish(self,job):
        for _ in range(200):
            row=self.client.get('/api/jobs/'+job['id']).json()
            if row['status'] in ['done','error']:return row
            time.sleep(.01)
        self.fail('Job did not finish')
    def upload(self,**kw):
        return self.client.post('/api/references',files={'file':('voice.wav',wav(**kw),'audio/wav')})
    def test_candidate_defaults_distinct_seeds_and_independent_voice_saves(self):
        response=self.client.post('/api/candidates',json={'text':'Welcome home.','description':'A calm baritone.'})
        self.assertEqual(response.status_code,202,response.text)
        batch=response.json();self.assertEqual(batch['candidate_count'],2)
        for job in batch['jobs']:self.assertEqual(self.finish(job)['status'],'done')
        batch=self.client.get('/api/candidates/'+batch['id']).json()
        self.assertEqual((batch['status'],batch['done_count']),('done',2))
        self.assertEqual([row['result']['seed'] for row in batch['jobs']],[42,43])
        self.assertEqual(len({row['result']['path'] for row in batch['jobs']}),2)
        for index,job in enumerate(batch['jobs'],1):
            self.assertEqual(job['candidate_index'],index)
            self.assertEqual(job['result']['candidate_batch_id'],batch['id'])
            self.assertEqual(job['result']['candidate_count'],2)
            self.assertEqual(self.client.get(job['result']['audio_url']).status_code,200)
        chosen=batch['jobs'][1]['result']
        voice=self.client.post('/api/voices',json={'output_id':chosen['id'],'name':'Chosen second option'}).json()
        self.assertEqual(voice['source_id'],chosen['id'])
        self.assertEqual(self.client.get('/api/history').json()['total'],2)
    def test_candidate_count_validation_one_and_five_and_seed_wrap(self):
        base={'text':'Welcome home.','description':'A calm baritone.'}
        for count in [0,6,-1,1.5,True,'2',None]:
            response=self.client.post('/api/candidates',json={**base,'candidate_count':count})
            self.assertEqual(response.status_code,422,response.text)
        for count in (1,5):
            response=self.client.post('/api/candidates',json={**base,'candidate_count':count,'seed':2**32-1})
            self.assertEqual(response.status_code,202,response.text)
            jobs=response.json()['jobs'];self.assertEqual(len(jobs),count)
            self.assertEqual([row['requested']['seed'] for row in jobs],[(2**32-1+i)%2**32 for i in range(count)])
            for job in jobs:self.assertEqual(self.finish(job)['status'],'done')
        self.assertEqual(self.client.get('/api/candidates/not-an-id').status_code,404)
        self.assertEqual(self.client.get('/api/candidates/'+'0'*32).status_code,404)
        self.assertEqual(self.generate(candidate_count=2).status_code,422)
    def test_candidate_idempotency_recovers_original_batch_and_rejects_changes(self):
        request={'text':'Welcome home.','description':'A calm baritone.','candidate_count':3,'idempotency_key':'same-options'}
        first=self.client.post('/api/candidates',json=request).json()
        again=self.client.post('/api/candidates',json=request).json()
        self.assertEqual(first['id'],again['id'])
        self.assertEqual([row['id'] for row in first['jobs']],[row['id'] for row in again['jobs']])
        for change in [{'candidate_count':2},{'seed':99},{'text':'Other words.'}]:
            self.assertEqual(self.client.post('/api/candidates',json={**request,**change}).status_code,409)
        for job in first['jobs']:self.finish(job)
        self.assertEqual(self.client.get('/api/queue',params={'status':'all'}).json()['total'],3)
    def test_candidates_reuse_the_same_transcribed_reference(self):
        reference=self.upload().json()
        request={'text':'Welcome home.','reference_id':reference['id'],'ref_text':'Exact reference words.',
                 'candidate_count':2,'generate_immediately':True}
        response=self.client.post('/api/candidates',json=request)
        self.assertEqual(response.status_code,202,response.text)
        for job in response.json()['jobs']:
            self.assertTrue(job['generate_immediately']);self.assertEqual(job['effective']['clone_mode'],'combined' if server.PROFILE['backend']=='voxcpm2' else 'reference')
            self.assertEqual(self.finish(job)['status'],'done')
        self.assertEqual([row[2] for row in self.engine.calls],['Exact reference words.']*2)
        self.assertEqual(len({str(row[1]) for row in self.engine.calls}),1)
        caps=self.client.get('/api/capabilities').json()['limits']['candidates']
        self.assertEqual((caps['min'],caps['max'],caps['default']),(1,5,2))
    def test_failed_candidate_does_not_block_others_and_can_be_retried(self):
        generate=self.engine.generate
        def fail_one(request,*args,**kwargs):
            if request.seed==43:raise RuntimeError('One option failed')
            return generate(request,*args,**kwargs)
        with patch.object(self.engine,'generate',side_effect=fail_one):
            response=self.client.post('/api/candidates',json={'text':'Welcome home.','description':'A calm baritone.','candidate_count':3})
            self.assertEqual(response.status_code,202,response.text)
            for job in response.json()['jobs']:self.finish(job)
        batch=self.client.get('/api/candidates/'+response.json()['id']).json()
        self.assertEqual((batch['status'],batch['done_count']),('partial',2))
        self.assertEqual([row['status'] for row in batch['jobs']],['done','error','done'])
        retried=self.client.post('/api/queue/'+batch['jobs'][1]['id']+'/retry',json={})
        self.assertEqual(retried.status_code,202);self.assertEqual(self.finish(retried.json())['status'],'done')
        self.assertEqual(self.client.get('/api/candidates/'+batch['id']).json()['status'],'done')
    def test_direct_voice_upload_enters_collection_preserves_original_and_is_reusable(self):
        original=wav(rate=48000,channels=2)
        response=self.client.post('/api/voices/upload',files={'file':('performance.wav',original,'audio/wav')},
            data={'name':'  Uploaded aristocrat  ','description':'A cultured, calm baritone.',
                  'transcript':'My guests. You came.','tags':[' Romanian ','Villain','romanian']})
        self.assertEqual(response.status_code,201,response.text)
        voice=response.json();self.assertEqual(voice['name'],'Uploaded aristocrat')
        self.assertEqual(voice['tags'],['romanian','villain']);self.assertEqual(voice['transcript'],'My guests. You came.')
        found=self.client.get('/api/voices',params={'q':'uploaded cultured','tag':'ROMANIAN'}).json()
        self.assertEqual([row['id'] for row in found['data']],[voice['id']])
        self.assertEqual(self.client.get('/api/voices/'+voice['id']).json(),voice)
        self.assertEqual(self.client.get('/api/references/'+voice['source_id']+'/original').content,original)
        with sf.SoundFile(self.root/'voices'/f'{voice["id"]}.wav') as audio:
            self.assertEqual((audio.samplerate,audio.channels,len(audio)),(16000,1,32000))
        generated=self.generate(voice_id=voice['id']).json()
        self.assertEqual(self.finish(generated)['status'],'done')
        self.assertEqual(str(self.engine.calls[-1][1]),str(self.root/'voices'/f'{voice["id"]}.wav'))
    def test_direct_voice_upload_rejects_bad_metadata_audio_and_size_without_catalog_entries(self):
        for details in [{'name':' '},{'name':'Named','tags':['x'*41]}, {'name':'Named','tags':['tag']*21}]:
            response=self.client.post('/api/voices/upload',files={'file':('voice.wav',wav(),'audio/wav')},data=details)
            self.assertEqual(response.status_code,422,response.text)
        for raw in [b'not audio',wav(seconds=0)]:
            response=self.client.post('/api/voices/upload',files={'file':('bad.wav',raw,'audio/wav')},data={'name':'Named'})
            self.assertEqual(response.status_code,422,response.text)
        response=self.client.post('/api/voices/upload',content=b'x',headers={'content-length':str(27*1024*1024)})
        self.assertEqual(response.status_code,413)
        self.assertFalse(list((self.root/'voices').iterdir()))
        self.assertFalse(list((self.root/'references').glob('*.wav')))
    def test_direct_voice_upload_rolls_back_new_reference_if_catalog_save_fails(self):
        preserved=self.upload().json();before=set((self.root/'references').rglob('*'))
        with patch('server.save_voice',side_effect=OSError('simulated catalog write failure')):
            response=self.client.post('/api/voices/upload',files={'file':('new.wav',wav(),'audio/wav')},data={'name':'Named'})
        self.assertEqual(response.status_code,422)
        self.assertEqual(set((self.root/'references').rglob('*')),before)
        self.assertEqual(self.client.get(preserved['audio_url']).status_code,200)
        self.assertEqual(self.client.get('/api/voices').json()['total'],0)
    def test_unknown_and_invalid_fields(self):
        for body in [{'speed':2},{'text':' '},{'steps':99},{'voice_id':'../bad'},{'max_tokens':1}]:
            self.assertEqual(self.generate(**body).status_code,422)
    def test_capabilities_and_health(self):
        self.assertIn('reference',self.client.get('/api/capabilities').json()['modes'])
        self.assertEqual(self.client.get('/health').json()['status'],'unloaded')
    def test_incomplete_assets_and_truthful_health(self):
        with patch('server.all_errors',lambda:['missing shard']):
            self.assertEqual(self.client.get('/health').json()['status'],'error')
            self.assertEqual(self.generate().status_code,503)
    def test_custom_output_and_restart_index(self):
        folder=self.root/'custom'
        response=self.generate(output_dir=str(folder));self.assertEqual(response.status_code,202,response.text)
        row=self.finish(response.json())['result'];self.assertTrue(Path(row['path']).is_file())
        self.assertEqual(Path(row['path']).parent,folder.resolve())
        fresh=JobManager(self.engine,self.root,self.root/'other.lock')
        self.assertEqual(fresh.read(row['id'])['status'],'done');fresh.executor.shutdown()
        self.assertEqual(self.client.get(row['audio_url']).status_code,200)
        saved=self.client.get('/api/artifacts/'+row['id']).json()
        self.assertEqual(saved['requested']['output_dir'],str(folder))
        self.assertEqual(saved['effective']['steps'],10 if server.PROFILE['backend']=='voxcpm2' else None)
        self.assertEqual(self.client.get('/api/metadata/'+row['id']).status_code,200)
    def test_relative_output_and_unmounted_volume(self):
        self.assertEqual(self.generate(output_dir='relative').status_code,422)
        self.assertEqual(self.generate(output_dir='/Volumes/absent-voice-test/out').status_code,422)
        self.assertEqual(self.generate(output_dir='/Volumes/ASH-SSD/../absent-voice-test/out').status_code,422)
        self.assertFalse(Path('/Volumes/absent-voice-test').exists())
    def test_idempotent_retry_and_changed_payload(self):
        response=self.generate(idempotency_key='test-key');row=self.finish(response.json())
        retry=self.generate(idempotency_key='test-key')
        self.assertEqual(retry.json()['id'],row['id']);self.assertEqual(len(self.engine.calls),1)
        self.assertEqual(self.generate(idempotency_key='test-key',seed=44).status_code,409)
    def test_legacy_idempotency_fingerprint_survives_additive_api_fields(self):
        from voice_catalog import atomic_json,now
        identity='e'*32
        request=Generation(text='Welcome home.',description='A calm baritone.',idempotency_key='old-api')
        legacy={'prompt_expansion_id','preset_voice','accent','clone_mode','steering_emotion','steering_strength','qwen3','moss'}
        import hashlib
        fingerprint=hashlib.sha256(request.model_dump_json(exclude={'idempotency_key','generate_immediately',*legacy}).encode()).hexdigest()
        key=hashlib.sha256(b'old-api').hexdigest()
        atomic_json(self.root/'jobs'/f'{identity}.json',{'id':identity,'status':'error','stage':'Old completion','created_at':now(),'updated_at':now()})
        atomic_json(self.root/'idempotency'/f'{key}.json',{'job':identity,'fingerprint':fingerprint})
        response=self.generate(idempotency_key='old-api')
        self.assertEqual(response.status_code,202);self.assertEqual(response.json()['id'],identity)
        self.assertEqual(self.generate(idempotency_key='old-api',accent='British English').status_code,409)
    def test_new_reference_controls_reject_missing_transcript_or_unsupported_mode(self):
        reference=self.upload().json()
        if server.PROFILE['backend']=='voxcpm2':
            response=self.generate(reference_id=reference['id'],clone_mode='continuation')
            self.assertEqual(response.status_code,422);self.assertEqual(len(self.engine.calls),0)
            job=self.generate(reference_id=reference['id'],clone_mode='continuation',ref_text='Exact words.').json()
            self.assertEqual(self.finish(job)['status'],'done');self.assertEqual(self.engine.calls[-1][2],'Exact words.')
        else:
            self.assertEqual(self.generate(reference_id=reference['id'],accent='Romanian English').status_code,422)
            self.assertEqual(self.generate(reference_id=reference['id'],clone_mode='continuation').status_code,422)
    def test_namespaced_backend_options_metadata_and_alias_compatibility(self):
        reference=self.upload().json()
        import jobs,capabilities
        for backend,options in [('chatterbox',{'exaggeration':.7}),('indextts',{'duration_factor':1.2,'emotion_vector':[.1]*8,'emotion_alpha':.5})]:
            profile=json.loads((PROJECT_ROOT/'backends'/backend/'service.json').read_text())
            with patch('server.PROFILE',profile),patch.object(self.manager,'profile',profile),patch('capabilities.PROFILE',profile):
                job=self.generate(reference_id=reference['id'],language='English',**{backend:options}).json()
                result=self.finish(job)['result']
                self.assertNotIn('exaggeration',result['requested']);self.assertNotIn('duration_factor',result['effective'])
                self.assertEqual(result['requested'][backend][next(iter(options))],next(iter(options.values())))
                self.assertEqual(result['effective'][backend][next(iter(options))],next(iter(options.values())))
                alias_job=self.generate(reference_id=reference['id'],language='English',**options).json()
                alias=self.finish(alias_job)['result']
                self.assertEqual(alias['requested'][backend],options)

    def test_combined_retry_from_prior_api_fields_survives_backend_merge(self):
        if server.PROFILE['backend']!='voxcpm2':return
        request=Generation(text='Welcome home.',description='A calm baritone.',voice_id='a'*32,
            clone_mode='combined',idempotency_key='prior-combined')
        added={'prompt_expansion_id','exaggeration','cfg_weight','emotion_reference_id','emotion_vector','emotion_alpha','duration_factor','interval_silence','chatterbox','indextts','fish','higgs','qwen3','moss'}
        fingerprint=hashlib.sha256(request.model_dump_json(exclude={'idempotency_key',*added}).encode()).hexdigest()
        identity='c'*32
        from voice_catalog import atomic_json,now
        atomic_json(self.root/'jobs'/f'{identity}.json',{'id':identity,'status':'error','stage':'Prior completion','created_at':now(),'updated_at':now()})
        key=hashlib.sha256(b'prior-combined').hexdigest()
        atomic_json(self.root/'idempotency'/f'{key}.json',{'job':identity,'fingerprint':fingerprint})
        response=self.client.post('/api/generate',json=request.model_dump())
        self.assertEqual(response.status_code,202);self.assertEqual(response.json()['id'],identity)

    def test_combined_validation_metadata_and_reuse(self):
        reference=self.upload().json()
        for fields in [{}, {'ref_text':'   '}]:
            self.assertEqual(self.generate(reference_id=reference['id'],clone_mode='combined',**fields).status_code,422)
        request=dict(reference_id=reference['id'],clone_mode='combined',ref_text='Exact words.',idempotency_key='combined-test')
        if server.PROFILE['backend']!='voxcpm2':
            self.assertEqual(self.generate(**request).status_code,422);return
        job=self.generate(**request).json();result=self.finish(job)['result']
        self.assertEqual(result['effective']['ref_text'],'Exact words.')
        self.assertEqual(result['requested']['clone_mode'],'combined')
        self.assertEqual(result['effective']['description'],'')
        self.assertEqual(result['reference']['transcript'],'Exact words.')
        self.assertEqual(self.generate(**request).json()['id'],job['id'])
        self.assertEqual(self.generate(**{**request,'clone_mode':'continuation'}).status_code,409)
    def test_busy_and_disconnected_client_keeps_capacity(self):
        self.engine.gate=threading.Event()
        row=self.generate().json();self.assertTrue(self.engine.started.wait(1))
        # The POST has returned, so no HTTP handler owns this work or its capacity.
        pending=self.generate();self.assertEqual(pending.status_code,202);self.assertEqual(pending.json()['status'],'queued')
        self.assertEqual(self.client.post('/api/model/unload').status_code,409)
        self.assertTrue(self.client.get('/health').json()['busy'])
        self.engine.gate.set();self.assertEqual(self.finish(row)['status'],'done')
    def test_cross_service_gpu_reservation(self):
        handle=self.manager.reserve()
        peer=JobManager(FakeEngine(),self.root,self.root/'gpu.lock')
        try:
            with self.assertRaises(ValueError):peer.reserve()
        finally:handle.close();peer.executor.shutdown()
    def test_backend_failure_survives_restart(self):
        self.engine.fail=True
        row=self.finish(self.generate().json());self.assertEqual(row['status'],'error')
        self.assertIn('fake backend failure',self.client.get('/health').json()['last_error'])
        self.assertEqual(self.client.get('/health').json()['status'],'error')
        self.assertFalse(list((self.root/'outputs').glob('*.wav')))
    def test_restart_marks_interrupted_jobs(self):
        from voice_catalog import atomic_json,now
        identity='a'*32
        atomic_json(self.root/'jobs'/f'{identity}.json',{'id':identity,'status':'running','stage':'x','created_at':now(),'updated_at':now()})
        self.manager.close()
        fresh=JobManager(self.engine,self.root,self.root/'gpu.lock')
        try:
            fresh.recover();self.assertEqual(fresh.read(identity)['status'],'error')
        finally:fresh.close()
    def test_original_preserved_and_working_audio_bounded(self):
        raw=wav(48000,2)
        response=self.client.post('/api/references',files={'file':('stereo.wav',raw)},data={'transcript':'Hello there.'})
        self.assertEqual(response.status_code,201,response.text)
        row=response.json();self.assertEqual(self.client.get(row['original_url']).content,raw)
        self.assertEqual(row['original']['channels'],2);self.assertEqual(row['effective']['sample_rate'],16000)
        self.assertEqual(row['transcript'],'Hello there.')
        working=sf.info(BytesIO(self.client.get(row['audio_url']).content));self.assertEqual(working.channels,1)
    def test_decoded_audio_limits(self):
        for kw in [{'rate':4000},{'channels':3},{'seconds':31}]:self.assertEqual(self.upload(**kw).status_code,422)
    def test_bounded_bodies_and_origin(self):
        self.assertEqual(self.client.post('/api/generate',content=b'x'*131073).status_code,413)
        self.assertEqual(self.client.post('/api/references',content=b'',headers={'Content-Length':str(27*1024*1024)}).status_code,413)
        self.assertEqual(self.client.post('/api/model/unload',headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/health',headers={'Host':'evil.example'}).status_code,400)
    def test_saved_voice_metadata_search_edit_and_reuse(self):
        ref=self.upload().json()
        row=self.client.post('/api/voices',json={'reference_id':ref['id'],'name':'Calm Count','description':'Cultured baritone',
                      'tags':[' Romanian ','ROMANIAN','villain'],'transcript':'Welcome home.'}).json()
        self.assertEqual(row['tags'],['romanian','villain'])
        before=(self.root/'voices'/f'{row["id"]}.wav').read_bytes()
        edited=self.client.patch('/api/voices/'+row['id'],json={'name':'The Count','tags':[],'description':''})
        self.assertEqual(edited.status_code,200);self.assertEqual(edited.json()['transcript'],'Welcome home.')
        self.assertEqual(before,(self.root/'voices'/f'{row["id"]}.wav').read_bytes())
        self.assertEqual(self.client.get('/api/voices?q=count').json()['total'],1)
        self.assertEqual(self.client.get('/api/voices?tag=romanian').json()['total'],0)
        job=self.generate(voice_id=row['id']).json();self.assertEqual(self.finish(job)['status'],'done')
        self.assertEqual(self.engine.calls[-1][2],'Welcome home.')
    def test_history_pagination_and_missing_media(self):
        for text in ['First hello','Second hello','Third goodbye']:self.finish(self.generate(text=text).json())
        page=self.client.get('/api/history?q=hello&limit=1').json()
        self.assertEqual(page['total'],2);self.assertTrue(page['has_more']);self.assertEqual(len(page['data']),1)
        Path(page['data'][0]['path']).unlink()
        self.assertEqual(self.client.get('/api/history?q=hello').json()['total'],1)
    def test_audio_traversal(self):
        self.assertEqual(self.client.get('/api/audio/outputs/not-a-uuid').status_code,404)
        self.assertEqual(self.client.get('/api/audio/cache/'+'a'*32).status_code,404)


    def test_resources_record_success_failure_and_retry_without_real_inference(self):
        self.engine.fail=True
        failed=self.finish(self.generate().json())
        self.assertEqual(failed['status'],'error')
        # End-of-attempt monitoring completes immediately after the queue transition.
        for _ in range(100):
            first=self.client.get('/api/observability/jobs/'+failed['id']).json()
            if first.get('selected_run',{}).get('status')=='error':break
            time.sleep(.01)
        self.assertEqual(first['selected_run']['status'],'error')
        self.assertGreater(first['summary']['samples'],0)
        self.engine.fail=False
        retry=self.client.post('/api/jobs/'+failed['id']+'/retry').json()
        self.assertEqual(self.finish(retry)['status'],'done')
        for _ in range(100):
            history=self.client.get('/api/observability/jobs/'+failed['id']).json()
            if history.get('selected_run',{}).get('status')=='done':break
            time.sleep(.01)
        self.assertEqual(history['selected_run']['attempt'],2)
        self.assertEqual(len(history['runs']),2)
        self.assertTrue(all(row['job_id']==failed['id'] for row in history['runs']))


class AdapterTests(unittest.TestCase):
    def request(self,**kw):return Generation(text='Hello.',description='A rich baritone.',emotion='Calm.',**kw)
    def test_backend_mapping_vox(self):
        import engine
        adapter=engine.Engine();adapter.model=Mock()
        request=self.request(steps=12,guidance=2.5)
        with patch('engine.PROFILE',{'backend':'voxcpm2'}):adapter.backend_call(request,None,'')
        kwargs=adapter.model.generate.call_args.kwargs
        self.assertEqual(kwargs['inference_timesteps'],12);self.assertEqual(kwargs['cfg_value'],2.5)
        self.assertIn('baritone',kwargs['instruct']);self.assertIn('Calm',kwargs['instruct'])
    def test_backend_mapping_qwen_design_and_base(self):
        import engine
        adapter=engine.Engine();adapter.model=Mock()
        request=self.request(temperature=.7,language='English')
        with patch('engine.PROFILE',{'backend':'qwen3'}):
            adapter.backend_call(request,None,'')
            adapter.backend_call(request,Path('/tmp/reference.wav'),'Exact words.')
        design=adapter.model.generate_voice_design.call_args.kwargs
        base=adapter.model.generate.call_args.kwargs
        self.assertEqual(design['language'],'English');self.assertEqual(design['temperature'],.7)
        self.assertEqual(base['ref_text'],'Exact words.');self.assertNotIn('instruct',base)
        self.assertEqual(base['split_pattern'],'')
    def test_unsupported_controls_both_profiles(self):
        import capabilities
        for backend,field in [('qwen3','steps'),('voxcpm2','temperature')]:
            profile={'backend':backend,'id':'test','name':'test','sample_rate':24000,'quantization':'test'}
            with patch('capabilities.PROFILE',profile):
                with self.assertRaises(ValueError):capabilities.validate_controls(self.request(**{field:1}))
                if backend=='qwen3':
                    with self.assertRaises(ValueError):capabilities.validate_controls(self.request(voice_id='a'*32))
    def test_variant_switch_unloads_previous(self):
        import engine
        adapter=engine.Engine(loader=lambda path:Mock(tokenizer=Mock(),speech_tokenizer=Mock()))
        adapter.model=object();adapter.variant='old';adapter.unload=Mock(side_effect=lambda:setattr(adapter,'model',None))
        with patch('engine.verify_assets',lambda key:[]):adapter.load(next(iter(engine.MODEL_SPECS)))
        adapter.unload.assert_called_once();self.assertIsNotNone(adapter.model)


class AssetTests(unittest.TestCase):
    def test_retry_and_checksum_completion(self):
        import assets,download
        with tempfile.TemporaryDirectory(dir=ROOT/'cache/tmp') as tmp:
            root=Path(tmp);blob=b'{"model_type":"fake"}'
            spec={'test':{'repo':'fake/repo','revision':'abc','subdir':'','files':[{'path':'config.json','size':len(blob),
                'sha256':hashlib.sha256(blob).hexdigest()}]}}
            with patch('assets.MODEL_DIR',root),patch('assets.MODEL_SPECS',spec),patch('download.MODEL_DIR',root),patch('download.MODEL_SPECS',spec),patch('download.prepare_storage'):
                with self.assertRaises(RuntimeError):download.download_models(lambda **kw:None)
                self.assertFalse((root/'verified.json').exists())
                def fake(**kw):
                    self.assertEqual(kw['revision'],'abc');(root/'config.json').write_bytes(blob)
                download.download_models(fake)
                self.assertEqual(assets.verify_assets('test'),[])
                (root/'config.json').write_bytes(b'x'*len(blob))
                self.assertTrue(assets.verify_assets('test',full=True))
    def test_lifecycle_pid_mismatch_never_verified(self):
        import manage
        with patch('manage.listeners',lambda port:['123']):self.assertFalse(manage.verified_pid(456,8003))
        self.assertFalse(manage.matching({'service':'unrelated','project':str(PROJECT_ROOT),'storage':str(PROJECT_ROOT)}))
    def test_lifecycle_accepts_mac_framework_python_only_with_matching_command_and_cwd(self):
        import manage
        executable=str((Path(manage.sys.base_prefix)/'Resources/Python.app/Contents/MacOS/Python').resolve())
        command=executable+' -u '+str(PROJECT_ROOT/'server.py')+' --port 8003'
        results=[Mock(stdout=command),Mock(stdout=f'p123\nfcwd\nn{PROJECT_ROOT}\n')]
        with patch('manage.listeners',lambda port:['123']),patch('manage.subprocess.run',side_effect=results):
            self.assertTrue(manage.verified_pid(123,8003))
        results=[Mock(stdout='/usr/bin/echo '+str(PROJECT_ROOT/'server.py')),Mock(stdout=f'n{PROJECT_ROOT}\n')]
        with patch('manage.listeners',lambda port:['123']),patch('manage.subprocess.run',side_effect=results):
            self.assertFalse(manage.verified_pid(123,8003))
    def test_appledouble_cleanup_preserves_other_files(self):
        from clean_env import clean_appledouble
        with tempfile.TemporaryDirectory(dir=ROOT/'cache/tmp') as tmp:
            root=Path(tmp)
            (root/'._fake').write_bytes(b'not metadata')
            (root/'._model.safetensors').write_bytes(b'\x00\x05\x16\x07'+b'hello')
            (root/'model.safetensors').write_bytes(b'weights')
            self.assertEqual(clean_appledouble(root),1)
            self.assertTrue((root/'._fake').is_file());self.assertEqual((root/'model.safetensors').read_bytes(),b'weights')
    def test_source_only_ignore_rules(self):
        rules=(PROJECT_ROOT/'.gitignore').read_text().splitlines()
        for item in ['weights/','voices/','references/','outputs/','artifact-index/','jobs/','.venv/','cache/','idempotency/']:
            self.assertIn(item,rules)


if __name__=='__main__':unittest.main()
