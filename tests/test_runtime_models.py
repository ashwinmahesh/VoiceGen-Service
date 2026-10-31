"""Shared ready-model admission, ordering, contexts, and real subprocess routing.

All model work is a benign fake adapter; no weights or production queues.
"""
from contextlib import ExitStack
import fcntl
import io
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from unittest.mock import patch

from fastapi.testclient import TestClient
from config import PROJECT_ROOT,PROFILE,MODEL_SPECS,model_context
from durable_queue import QueueStore
from model_readiness import ReadyModels
from runtime_engine import RuntimeEngine
from backend_protocol import send,receive


FAKE_WORKER='''import os,sys,socket,time,wave,struct,math
from pathlib import Path
from backend_protocol import send,receive
sock=socket.socket(fileno=int(sys.argv[sys.argv.index('--fd')+1]))
send(sock,{'ready':True})
calls=0
while True:
 try:request,fds=receive(sock)
 except EOFError:break
 try:
  if request['command']=='close':send(sock,{'ok':True});break
  calls+=1
  text=request['request']['text']
  if text=='Crash.':os._exit(9)
  send(sock,{'event':'Loading model'})
  path=Path(request['destination']);path.parent.mkdir(parents=True,exist_ok=True)
  if text=='Held.':
   path.with_suffix('.started').touch()
   while not (path.parent/'release').exists():time.sleep(.02)
  send(sock,{'event':'Generating speech'})
  with wave.open(str(path),'wb') as out:
   out.setparams((1,2,16000,0,'NONE','not compressed'))
   out.writeframes(b''.join(struct.pack('<h',int(2000*math.sin(i*.1))) for i in range(16000)))
  send(sock,{'loaded':True,'variant':os.environ['MODEL_BACKEND'],'release_when_paused':os.environ['MODEL_BACKEND']=='higgs','result':{'duration':1.,'sample_rate':16000,'generation_seconds':.01,'waveform':[.1],
    'worker_pid':os.getpid(),'worker_calls':calls,'received_leases':len(fds),'backend':os.environ['MODEL_BACKEND']}})
 finally:
  for fd in fds:os.close(fd)
sock.close()
'''


def wait_for(check,timeout=10):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        result=check()
        if result:return result
        time.sleep(.02)
    raise AssertionError('Timed out waiting for runtime-model fixture')


def recording():
    out=io.BytesIO()
    with wave.open(out,'wb') as audio:
        audio.setparams((1,2,16000,0,'NONE','not compressed'))
        audio.writeframes(struct.pack('<hh',1000,-1000)*16000)
    return out.getvalue()


class ModelContexts(unittest.TestCase):
    def test_concurrent_metadata_requests_do_not_change_each_other_or_default(self):
        from capabilities import capabilities
        original=PROFILE['backend'];barrier=threading.Barrier(2);results={}
        def inspect(model):
            with model_context(model):
                barrier.wait();caps=capabilities()
                results[model]=(PROFILE['backend'],caps.backend,dict(MODEL_SPECS),caps.controls['temperature'])
        threads=[threading.Thread(target=inspect,args=(model,)) for model in ('higgs','voxcpm2')]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(results['higgs'][0:2],('higgs','higgs'))
        self.assertEqual(results['voxcpm2'][0:2],('voxcpm2','voxcpm2'))
        self.assertEqual(set(results['higgs'][2]),{'higgs'})
        self.assertEqual(set(results['voxcpm2'][2]),{'voxcpm2'})
        self.assertEqual(PROFILE['backend'],original)


class SharedQueue(unittest.TestCase):
    def setUp(self):
        parent=PROJECT_ROOT/'state/validation';parent.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=parent,prefix='global-models-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=QueueStore(self.root,self.root/'queue.sqlite3');self.store.initialize()

    def test_global_order_positions_pause_run_now_and_blocked_skip(self):
        first=self.store.admit_opaque('higgs',{})
        second=self.store.admit_opaque('voxcpm2',{})
        third=self.store.admit_opaque('higgs',{})
        self.assertEqual(self.store.listing(backend='higgs')['data'][1]['position'],3)
        self.store.move(third['id'],before_id=first['id'])
        self.assertEqual(self.store.listing()['data'][0]['id'],third['id'])
        self.store.pause(True);self.assertIsNone(self.store.claim_global(['higgs','voxcpm2']))
        self.store.run_now(second['id'])
        self.assertEqual(self.store.claim_global(['voxcpm2'])['id'],second['id'])
        self.store.change(second['id'],status='done')
        self.store.pause(False);self.store.mark_blocked({'higgs':'Missing fixture assets'})
        self.assertIsNone(self.store.claim_global(['voxcpm2']))
        self.assertEqual(self.store.read(first['id'])['status'],'queued')
        self.assertIn('Missing',self.store.read(first['id'])['blocked_reason'])
        self.store.mark_blocked({'higgs':None})
        self.assertEqual(self.store.claim_global(['higgs'])['id'],third['id'])

    def claim_all(self,store,ready=('higgs','voxcpm2')):
        claimed=[]
        while row:=store.claim_global(list(ready)):
            claimed.append(row['id']);store.change(row['id'],status='done')
        return claimed

    def test_candidate_batch_finishes_before_other_normal_work(self):
        payload={'destination':str(self.root),'requested':{},'effective':{}}
        batch=self.store.admit_candidates('voxcpm2','voxcpm2','design',False,[payload,payload],None,'fingerprint')
        first=self.store.claim_global(['higgs','voxcpm2']);self.store.change(first['id'],status='done')
        other=self.store.admit_opaque('higgs',{});self.store.move(other['id'],before_id=batch['jobs'][1]['id'])
        self.assertEqual(self.claim_all(self.store),[batch['jobs'][1]['id'],other['id']])

    def test_immediate_work_runs_after_the_current_take_then_the_batch_resumes_even_after_restart(self):
        payload={'destination':str(self.root),'requested':{},'effective':{}}
        batch=self.store.admit_candidates('voxcpm2','voxcpm2','design',False,[payload,payload,payload],None,'fingerprint')
        later=self.store.admit_opaque('higgs',{});other=self.store.admit_opaque('higgs',{})
        first=self.store.claim_global(['higgs','voxcpm2'])
        self.assertEqual(first['id'],batch['jobs'][0]['id'])
        self.store.run_now(other['id']);self.store.change(first['id'],status='done')
        urgent=self.store.admit_candidates('higgs','higgs','reference',True,[payload,payload],None,'urgent')
        fresh=QueueStore(self.root,self.store.path);fresh.initialize()
        self.assertEqual(self.claim_all(fresh),[other['id'],*(job['id'] for job in urgent['jobs']),
                                                 batch['jobs'][1]['id'],batch['jobs'][2]['id'],later['id']])

    def test_pausing_mid_batch_returns_its_remaining_options_to_the_top(self):
        payload={'destination':str(self.root),'requested':{},'effective':{}}
        batch=self.store.admit_candidates('voxcpm2','voxcpm2','design',False,[payload,payload,payload],None,'fingerprint')
        other=self.store.admit_opaque('higgs',{})
        first=self.store.claim_global(['higgs','voxcpm2'])
        self.store.move(other['id'],before_id=batch['jobs'][1]['id'])
        self.store.pause(True);self.store.change(first['id'],status='done')
        self.assertIsNone(self.store.claim_global(['higgs','voxcpm2']))
        self.assertIsNone(self.store.setting('active_batch'))
        self.assertEqual([row['id'] for row in self.store.listing()['data']],[batch['jobs'][1]['id'],batch['jobs'][2]['id'],other['id']])
        self.store.pause(False)
        self.assertEqual(self.claim_all(self.store),[batch['jobs'][1]['id'],batch['jobs'][2]['id'],other['id']])

    def test_pausing_while_immediate_work_interrupts_a_batch_still_returns_the_batch_to_the_top(self):
        payload={'destination':str(self.root),'requested':{},'effective':{}}
        batch=self.store.admit_candidates('voxcpm2','voxcpm2','design',False,[payload,payload],None,'fingerprint')
        other=self.store.admit_opaque('higgs',{})
        first=self.store.claim_global(['higgs','voxcpm2']);self.store.change(first['id'],status='done')
        urgent=self.store.admit_opaque('higgs',{},immediate=True)
        self.store.move(other['id'],before_id=batch['jobs'][1]['id'])
        self.assertEqual(self.store.claim_global(['higgs','voxcpm2'])['id'],urgent['id'])
        self.store.pause(True);self.store.change(urgent['id'],status='done')
        self.assertIsNone(self.store.claim_global(['higgs','voxcpm2']))
        self.assertEqual([row['id'] for row in self.store.listing()['data']],[batch['jobs'][1]['id'],other['id']])

    def test_unplanned_batch_without_llm_does_not_block_eligible_models(self):
        payload={'destination':str(self.root),'requested':{},'effective':{},'candidate_plan':{'method':'llm','ready':False}}
        batch=self.store.admit_candidates('voxcpm2','voxcpm2','design',False,[payload,payload],None,'plan')
        self.store.set_setting('active_batch',batch['id'])
        other=self.store.admit_opaque('higgs',{})
        claimed=self.store.claim_global(['higgs','voxcpm2'],allow_llm=False)
        self.assertEqual(claimed['id'],other['id'])
        self.assertEqual(self.store.read(batch['jobs'][0]['id'])['status'],'queued')

    def test_lease_descriptor_survives_sender_close_until_receiver_closes(self):
        path=self.root/'gpu.lock';handle=path.open('a+');fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        left,right=socket.socketpair()
        try:
            send(left,{'lease':True},[handle.fileno()]);value,fds=receive(right)
            handle.close();self.assertTrue(value['lease'])
            with path.open('a+') as peer:
                with self.assertRaises(BlockingIOError):fcntl.flock(peer,fcntl.LOCK_EX|fcntl.LOCK_NB)
                for fd in fds:os.close(fd)
                fcntl.flock(peer,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:handle.close();left.close();right.close()


class RuntimeAPITests(unittest.TestCase):
    def setUp(self):
        import server,storage,queue_server
        from jobs import JobManager
        self.server,self.queue_server=server,queue_server
        parent=PROJECT_ROOT/'state/validation';parent.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=parent,prefix='runtime-api-');self.root=Path(self.tmp.name)
        for name in ('outputs','voices','references/originals','jobs','artifact-index','idempotency','cache/tmp'):(self.root/name).mkdir(parents=True)
        self.script=self.root/'worker.py';self.script.write_text('import sys;sys.path.insert(0,'+repr(str(PROJECT_ROOT))+')\n'+FAKE_WORKER)
        self.ready={'higgs','voxcpm2'}
        self.catalog=ReadyModels(probe=lambda root,model,original,override:[] if model in self.ready else ['Missing fixture model'])
        self.catalog.python=lambda model:Path(sys.executable)
        self.engine=RuntimeEngine(self.catalog,self.script)
        self.manager=JobManager(self.engine,self.root,self.root/'gpu.lock',db_path=self.root/'queue.sqlite3')
        self.stack=ExitStack()
        for target,value in [('server.ROOT',self.root),('server.prepare_storage',lambda:None),('server.engine',self.engine),
                             ('server.manager',self.manager),('storage.ROOT',self.root),('storage.OUTPUT_DIR',self.root/'outputs'),
                             ('queue_server.store',self.manager.store),('queue_server.catalog',self.catalog)]:
            self.stack.enter_context(patch(target,value))
        self.stack.enter_context(patch.object(server.app.state,'llm_enabled',False))
        self.stack.enter_context(patch.object(server.app.state,'requested_default','voxcpm2'))
        self.client=TestClient(server.app);self.client.__enter__()
        self.queue=TestClient(queue_server.app)
        response=self.client.post('/api/references',files={'file':('fixture.wav',recording(),'audio/wav')},data={'transcript':'Hello.'})
        self.assertEqual(response.status_code,201,response.text);self.reference=response.json()['id']

    def tearDown(self):
        (self.root/'outputs/release').touch()
        self.client.__exit__(None,None,None);self.stack.close();self.tmp.cleanup()

    def submit(self,model=None,text='Hello.',**extra):
        body={'text':text,'description':'A cultured baritone.',**extra}
        if model:body['model']=model
        if model=='higgs':body.update(reference_id=self.reference,description='',ref_text='Hello.')
        response=self.client.post('/api/generate',json=body)
        self.assertEqual(response.status_code,202,response.text);return response.json()

    def done(self,job):
        return wait_for(lambda:(row if (row:=self.client.get('/api/jobs/'+job['id']).json())['status'] in ('done','error') else None))

    def test_pausing_releases_only_a_model_resident_for_queue_work(self):
        self.assertEqual(self.done(self.submit('voxcpm2'))['status'],'done')
        self.client.post('/api/queue/pause');time.sleep(.3)
        self.assertEqual(self.engine.active_model,'voxcpm2')
        self.client.post('/api/queue/resume')
        self.assertEqual(self.done(self.submit('higgs'))['status'],'done')
        self.assertEqual(self.engine.active_model,'higgs')
        self.assertTrue(self.engine.release_when_paused)
        self.client.post('/api/queue/pause')
        wait_for(lambda:self.engine.active_model is None)
        self.assertFalse(self.engine.release_when_paused)

    def test_real_subprocesses_reuse_same_model_and_reap_before_switching(self):
        one=self.done(self.submit('voxcpm2'));two=self.done(self.submit('voxcpm2'))
        self.assertEqual(one['status'],'done',one)
        self.assertEqual(two['result']['worker_pid'],one['result']['worker_pid'])
        self.assertEqual(two['result']['worker_calls'],2)
        self.assertGreaterEqual(two['result']['received_leases'],3)
        native=self.done(self.submit('higgs'));self.assertEqual(native['status'],'done',native)
        self.assertNotEqual(native['result']['worker_pid'],two['result']['worker_pid'])
        with self.assertRaises(ProcessLookupError):os.kill(two['result']['worker_pid'],0)
        again=self.done(self.submit('voxcpm2'));self.assertEqual(again['status'],'done',again)
        with self.assertRaises(ProcessLookupError):os.kill(native['result']['worker_pid'],0)
        state=self.client.get('/health').json()
        self.assertEqual(state['service'],'voicegen-service');self.assertTrue(state['runtime_model_switching'])
        self.assertEqual(state['default_model'],'voxcpm2');self.assertEqual(state['active_model'],'voxcpm2')

    def test_discovery_ready_only_and_unready_admission_rejects_both_apis(self):
        for client in (self.client,self.queue):
            models=client.get('/v1/models').json()
            self.assertEqual({row['id'] for row in models['data']},self.ready)
            self.assertEqual(models['default_model'],'voxcpm2')
        direct=self.client.post('/api/generate',json={'model':'fish','text':'Hi.','description':'A voice.'})
        queued=self.queue.post('/api/queue',json={'model':'fish','payload':{'text':'Hi.'}})
        for response in (direct,queued):
            self.assertEqual(response.status_code,422,response.text);self.assertIn('make setup MODEL=fish',response.json()['detail'])
        self.assertEqual(self.manager.store.listing()['total'],0)

    def test_generated_voice_transcript_is_saved_editable_and_reused_by_higgs(self):
        take=self.done(self.submit('voxcpm2',text='Welcome home. Sit, please.'))['result']
        saved=self.client.post('/api/voices',json={'output_id':take['id'],'name':'Reusable narrator'})
        self.assertEqual(saved.status_code,201,saved.text)
        voice=saved.json();self.assertEqual(voice['transcript'],'Welcome home. Sit, please.')
        transcript='Welcome home. Please sit.'
        edited=self.client.patch('/api/voices/'+voice['id'],json={'transcript':transcript})
        self.assertEqual(edited.status_code,200,edited.text)
        self.assertEqual(self.client.get('/api/voices/'+voice['id']).json()['transcript'],transcript)
        job=self.client.post('/api/generate',json={'model':'higgs','voice_id':voice['id'],'text':'A different line.'})
        self.assertEqual(job.status_code,202,job.text)
        result=self.done(job.json())
        self.assertEqual(result['status'],'done',result)
        self.assertEqual(result['result']['reference']['transcript'],transcript)

    def test_queue_openapi_covers_shared_management_and_typed_responses(self):
        for client,entry_model in ((self.client,'Job'),(self.queue,'Entry')):
            schema=client.get('/openapi.json').json();paths=schema['paths'];models=schema['components']['schemas']
            for path,methods in {
                '/api/queue':{'get','delete'},'/api/queue/{identity}/move':{'post'},
                '/api/queue/{identity}/run-now':{'post'},'/api/queue/pause':{'post'},'/api/queue/resume':{'post'},
            }.items():self.assertTrue(methods<=set(paths[path]),(path,methods))
            fields={row['name'] for row in paths['/api/queue']['get']['parameters']}
            self.assertEqual(fields,{'status','model','limit','offset'})
            for path in ('/api/queue/pause','/api/queue/resume'):
                response=paths[path]['post']['responses']['200']['content']['application/json']['schema']
                self.assertEqual(response['$ref'],'#/components/schemas/QueueState')
            response=paths['/v1/models']['get']['responses']['200']['content']['application/json']['schema']
            self.assertEqual(response['$ref'],'#/components/schemas/ModelList')
            self.assertIn('modes',models['ModelDescriptor']['required'])
            self.assertTrue({'position','blocked_reason'}<=set(models[entry_model]['properties']))
        # Pausing through either origin changes the same direct-SQL state.
        self.assertEqual(self.queue.post('/api/queue/pause').json(),{'paused':True})
        self.assertTrue(self.client.get('/health').json()['queue_paused'])
        self.assertEqual(self.client.post('/api/queue/resume').json(),{'paused':False})
        self.assertFalse(self.queue.get('/health').json()['paused'])
        from queue_schemas import ModelList
        docs=(PROJECT_ROOT/'API.md').read_text().split('**`GET /v1/models`**',1)[1]
        example=json.loads(docs.split('```json',1)[1].split('```',1)[0])
        ModelList.model_validate(example)

    def test_retry_unavailable_model_is_actionable_and_preserves_failed_entry(self):
        failed=self.done(self.submit('voxcpm2','Crash.'))
        self.assertEqual(failed['status'],'error',failed)
        before=self.manager.store.read(failed['id'])
        self.ready.discard('voxcpm2');self.catalog.cache.clear()
        response=self.client.post('/api/jobs/'+failed['id']+'/retry')
        self.assertEqual(response.status_code,422,response.text)
        self.assertIn('make setup MODEL=voxcpm2',response.json()['detail'])
        after=self.manager.store.read(failed['id'])
        for field in ('status','attempts','payload','fingerprint','backend'):
            self.assertEqual(before[field],after[field],field)

    def test_shared_saved_default_idempotency_and_explicit_model(self):
        first=self.submit(idempotency_key='default-key')
        self.manager.store.set_setting('default_model','higgs')
        duplicate=self.submit(idempotency_key='default-key')
        self.assertEqual(first['id'],duplicate['id'])
        self.assertEqual(self.queue.get('/health').json()['default_model'],'higgs')
        missing=self.client.post('/api/generate',json={'text':'Hi.','description':'A voice.'})
        self.assertEqual(missing.status_code,422)
        self.manager.store.pause(True)
        queued=self.queue.post('/api/queue',json={'payload':{'text':'Hi.','reference_id':self.reference},'idempotency_key':'queued-default'}).json()
        self.assertEqual(queued['model'],'higgs')
        self.manager.store.set_setting('default_model','voxcpm2')
        retry=self.queue.post('/api/queue',json={'payload':{'text':'Hi.','reference_id':self.reference},'idempotency_key':'queued-default'})
        self.assertEqual(retry.status_code,202,retry.text);self.assertEqual(retry.json()['id'],queued['id'])

    def test_missing_model_stays_blocked_while_other_work_finishes_then_recovers(self):
        self.manager.store.pause(True)
        blocked=self.submit('higgs');eligible=self.submit('voxcpm2')
        self.ready.remove('higgs');self.catalog.cache.clear();self.manager.store.pause(False);self.manager._wake.set()
        finished=self.done(eligible);self.assertEqual(finished['status'],'done',finished)
        waiting=self.client.get('/api/jobs/'+blocked['id']).json()
        self.assertEqual(waiting['status'],'queued');self.assertIn('Missing',waiting['blocked_reason'])
        self.ready.add('higgs');self.catalog.cache.clear();self.manager._wake.set()
        self.assertEqual(self.done(blocked)['status'],'done')

    def test_backend_crash_fails_one_job_and_next_model_still_runs(self):
        bad=self.done(self.submit('voxcpm2','Crash.'));self.assertEqual(bad['status'],'error',bad)
        good=self.done(self.submit('higgs'));self.assertEqual(good['status'],'done',good)
        def free():
            with (self.root/'gpu.lock').open('a+') as handle:
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:return False
                return True
        wait_for(free)

    def test_run_now_runs_after_the_current_take_then_the_batch_resumes(self):
        response=self.client.post('/api/candidates',json={'model':'voxcpm2','text':'Held.','description':'A cultured baritone.',
            'candidate_count':3,'candidate_variation':'seed_only','candidate_use_llm':False})
        self.assertEqual(response.status_code,202,response.text);batch=response.json()
        wait_for(lambda:(self.root/'outputs'/ (batch['jobs'][0]['id']+'.started')).exists())
        other=self.submit('higgs')
        self.assertEqual(self.client.post('/api/queue/'+other['id']+'/run-now').status_code,200)
        (self.root/'outputs/release').touch()
        rows=[self.done(job) for job in batch['jobs']];urgent=self.done(other)
        self.assertTrue(all(row['status']=='done' for row in [*rows,urgent]),rows)
        self.assertLess(rows[0]['completed_at'],urgent['started_at'])
        self.assertLess(urgent['completed_at'],rows[1]['started_at'])
        self.assertLess(rows[1]['completed_at'],rows[2]['started_at'])

    def test_api_stays_responsive_and_validates_model_during_generation(self):
        active=self.submit('voxcpm2','Held.')
        wait_for(lambda:(self.root/'outputs'/(active['id']+'.started')).exists())
        started=time.monotonic()
        for model in ('higgs','voxcpm2','higgs','voxcpm2'):self.submit(model)
        self.assertLess(time.monotonic()-started,2)
        self.assertEqual(self.client.get('/api/capabilities?model=higgs').json()['backend'],'higgs')
        response=self.client.post('/api/generate',json={'model':'higgs','text':'Hello.','description':'A baritone.'})
        self.assertEqual(response.status_code,422,response.text)
        self.assertIn('VoxCPM2',response.json()['detail'])
        self.assertIn('MOSS',response.json()['detail']) # supported alternatives remain useful setup hints
        self.assertEqual(self.client.get('/health').json()['active_job'],active['id'])

    def test_health_remains_bounded_during_sqlite_write_contention(self):
        import sqlite3
        with sqlite3.connect(self.manager.store.path) as db:
            db.execute('BEGIN EXCLUSIVE')
            started=time.monotonic();response=self.client.get('/health')
            self.assertLess(time.monotonic()-started,1.5)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['queue_storage']['status'],'unavailable')
            db.rollback()
        self.assertEqual(self.client.get('/health').json()['queue_storage']['status'],'ready')

    def test_model_disappearing_at_worker_start_stays_queued(self):
        from model_readiness import ModelUnavailable
        require=self.catalog.require
        def unavailable(model,**kwargs):
            if threading.current_thread().name.startswith('voice-inference'):
                self.ready.discard(model)
                raise ModelUnavailable('Storage disconnected. Run make setup MODEL='+model)
            return require(model,**kwargs)
        with patch.object(self.catalog,'require',side_effect=unavailable):
            job=self.submit('voxcpm2')
            wait_for(lambda:self.manager.store.read(job['id']).get('blocked_reason'))
            row=self.manager.store.read(job['id'])
            self.assertEqual(row['status'],'queued');self.assertEqual(row['attempts'],0)
        self.ready.add('voxcpm2');self.catalog.cache.clear();self.manager._wake.set()
        self.assertEqual(self.done(job)['status'],'done')

    def test_ready_design_choices_are_separate_from_static_supported_alternatives(self):
        caps=self.client.get('/api/capabilities?model=higgs').json()['modes']['design']
        self.assertFalse(caps['available'])
        self.assertEqual([row['id'] for row in caps['ready_models']],['voxcpm2'])
        self.assertEqual([row['id'] for row in caps['supported_models']],['voxcpm2','qwen3','moss'])
        self.ready.discard('voxcpm2');self.catalog.cache.clear()
        caps=self.client.get('/api/capabilities?model=higgs').json()['modes']['design']
        self.assertEqual(caps['ready_models'],[])
        self.assertEqual(caps['unavailable_reason'],'Switch to a model which supports voice generation: VoxCPM2, Qwen3-TTS, MOSS VoiceGenerator')
        models=self.client.get('/v1/models').json()['data']
        self.assertEqual([row['id'] for row in models],['higgs'])
        self.assertNotIn('design',models[0]['modes'])


class WarmBackendOwnershipTests(unittest.TestCase):
    def test_warm_backend_retains_consumer_lease_but_releases_gpu(self):
        from inference_locks import inherit_reservations
        from schemas import Generation
        parent=PROJECT_ROOT/'state/validation';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            root=Path(folder);script=root/'worker.py'
            script.write_text('import sys;sys.path.insert(0,'+repr(str(PROJECT_ROOT))+')\n'+FAKE_WORKER)
            catalog=ReadyModels(probe=lambda *args:[]);catalog.python=lambda model:Path(sys.executable)
            engine=RuntimeEngine(catalog,script)
            owner=(root/'owner.lock').open('a+');gpu=(root/'gpu.lock').open('a+')
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(gpu,fcntl.LOCK_EX|fcntl.LOCK_NB)
            engine.consumer_owner=owner
            try:
                with model_context('voxcpm2'),inherit_reservations(owner,gpu):
                    engine.generate(Generation(text='Hello.',description='A calm voice.'),None,root/'take.wav')
                owner.close();gpu.close()
                with (root/'owner.lock').open('a+') as check:
                    with self.assertRaises(BlockingIOError):fcntl.flock(check,fcntl.LOCK_EX|fcntl.LOCK_NB)
                def gpu_free():
                    with (root/'gpu.lock').open('a+') as check:
                        try:fcntl.flock(check,fcntl.LOCK_EX|fcntl.LOCK_NB);return True
                        except BlockingIOError:return False
                wait_for(gpu_free)
                engine.unload()
                with (root/'owner.lock').open('a+') as check:fcntl.flock(check,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:
                engine.unload();owner.close();gpu.close()

    def test_unverifiable_process_cleanup_prevents_another_backend_start(self):
        engine=RuntimeEngine(ReadyModels(probe=lambda *args:[]))
        engine.process=process=type('Process',(),{'pid':987654,'poll':lambda self:0,'wait':lambda self,**kwargs:0})()
        with patch.object(engine,'_reap_group',side_effect=RuntimeError('Group state cannot be verified')):
            with self.assertRaisesRegex(RuntimeError,'cleanup could not be verified'):engine.unload()
        with patch('runtime_engine.subprocess.Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'cleanup could not be verified'):engine._start('higgs',None)
            launch.assert_not_called()
        self.assertIs(engine.process,process)



class ManagedIdentityTests(unittest.TestCase):
    def test_default_and_active_models_do_not_change_service_identity(self):
        import manage
        state=dict(service='voicegen-service',mode='generator',api_version='4.0.0',queue_scope='shared',
            runtime_model_switching=True,project=str(manage.PROJECT_ROOT),storage=str(manage.ROOT),queue_database=str(manage.QUEUE_DB.resolve()))
        self.assertTrue(manage.matching({**state,'default_model':'higgs','active_model':'voxcpm2'}))
        self.assertTrue(manage.matching({**state,'default_model':'voxcpm2','active_model':None}))
        for changed in ({'project':'elsewhere'},{'queue_database':'elsewhere'},{'api_version':'2.1.0'},{'mode':'queue'},{'queue_scope':'model'}):
            self.assertFalse(manage.matching({**state,**changed}))



if __name__=='__main__':unittest.main()
