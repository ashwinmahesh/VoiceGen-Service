"""Durability/concurrency tests: fake engines only, no weights or GPU work."""
from contextlib import ExitStack, closing
import fcntl
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from config import ROOT, PROJECT_ROOT
from durable_queue import QueueStore, QueueConflict
from jobs import JobManager, BusyError
from schemas import Generation, CandidateGeneration
from .test_contract import FakeEngine, wav
from voice_catalog import atomic_json


def wait_for(check, timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=check()
        if value:return value
        time.sleep(.01)
    raise AssertionError('Timed out waiting for fake worker')


class QueueFixture(unittest.TestCase):
    def setUp(self):
        (ROOT/'cache/tmp').mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'cache/tmp')
        self.root=Path(self.tmp.name)
        for name in ['jobs','outputs','artifact-index','idempotency','references']:
            (self.root/name).mkdir()
        self.managers=[];self.engines=[]
    def tearDown(self):
        for engine in self.engines:
            if engine.gate:engine.gate.set()
        for manager in self.managers:manager.close()
        self.tmp.cleanup()
    def manager(self,root=None,backend='voxcpm2',lock=None):
        engine=FakeEngine();self.engines.append(engine)
        manager=JobManager(engine,root or self.root,lock or self.root/'gpu.lock',
            profile={'backend':backend,'id':backend,'modes':{'design':backend}})
        self.managers.append(manager);return manager
    def submit(self,manager,text='Hello.',**kwargs):
        request=Generation(text=text,description='A calm voice.',**kwargs)
        return manager.submit(request,request,None,'',self.root/'outputs')
    def done(self,manager,identity):
        return wait_for(lambda: (r if (r:=manager.read(identity))['status'] in ['done','error','deleted'] else None))


class QueueTests(QueueFixture):
    def candidates(self,manager,**kwargs):
        request=CandidateGeneration(text='Welcome home.',description='A calm baritone.',**kwargs)
        return manager.submit_candidates(request,request,None,'',self.root/'outputs')
    def test_candidate_batch_survives_restart_and_interrupted_option(self):
        old=self.manager();batch=self.candidates(old,candidate_count=5,idempotency_key='persistent-options')
        old.store.claim('voxcpm2');old.close()
        new=self.manager();new.recover()
        for row in batch['jobs']:self.done(new,row['id'])
        recovered=new.read_candidates(batch['id'])
        self.assertEqual((recovered['status'],recovered['done_count']),('done',5))
        self.assertEqual([call[0].seed for call in new.engine.calls],list(range(42,47)))
        self.assertEqual(self.candidates(new,candidate_count=5,idempotency_key='persistent-options')['id'],batch['id'])
    def test_candidate_admission_is_atomic_and_concurrent_retries_share_one_group(self):
        manager=self.manager()
        real_dumps=json.dumps;calls=[]
        def fail_second_payload(value,*args,**kwargs):
            if isinstance(value,dict) and 'candidate_batch_id' in value:
                calls.append(value)
                if len(calls)==2:raise OSError('simulated serialization failure')
            return real_dumps(value,*args,**kwargs)
        with patch('durable_queue.json.dumps',side_effect=fail_second_payload):
            with self.assertRaises(OSError):self.candidates(manager,candidate_count=3)
        self.assertEqual(manager.listing()['total'],0)
        from concurrent.futures import ThreadPoolExecutor
        peer=self.manager()
        with ThreadPoolExecutor(max_workers=4) as pool:
            groups=list(pool.map(lambda i:self.candidates(peer if i%2 else manager,candidate_count=3,idempotency_key='raced'),range(4)))
        self.assertEqual(len({group['id'] for group in groups}),1)
        self.assertEqual(manager.listing()['total'],3)
        with manager.store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM candidate_batches').fetchone()[0],1)
    def test_candidate_failures_clear_and_retry_preserve_successes(self):
        manager=self.manager();batch=self.candidates(manager,candidate_count=3)
        first=manager.store.claim('voxcpm2');manager.store.change(first['id'],status='error',error='test failure')
        self.assertEqual(manager.remove(batch['jobs'][1]['id'])['deleted'],1)
        manager.recover();self.done(manager,batch['jobs'][2]['id'])
        partial=manager.read_candidates(batch['id']);self.assertEqual(partial['status'],'partial')
        self.assertEqual(partial['done_count'],1)
        manager.retry(first['id']);self.done(manager,first['id'])
        self.assertEqual(manager.read_candidates(batch['id'])['done_count'],2)
        self.assertEqual(manager.remove()['deleted'],0)
        self.assertTrue(Path(partial['jobs'][2]['result']['path']).is_file())
    def test_candidate_priority_preserves_fifo_and_does_not_preempt(self):
        manager=self.manager();manager.engine.gate=threading.Event();manager.recover()
        active=self.submit(manager,'Active.');self.assertTrue(manager.engine.started.wait(2))
        normal=self.submit(manager,'Normal.')
        batch=self.candidates(manager,candidate_count=2,generate_immediately=True)
        self.assertEqual(len(manager.engine.calls),1)
        manager.engine.gate.set();self.done(manager,normal['id'])
        self.assertEqual([call[0].text for call in manager.engine.calls],['Active.','Welcome home.','Welcome home.','Normal.'])
        self.assertEqual(manager.read(active['id'])['status'],'done')
        self.assertEqual(manager.read_candidates(batch['id'])['status'],'done')
    def test_startup_consumes_surviving_queue_and_retains_effective_settings(self):
        old=self.manager()
        row=self.submit(old,steps=12,idempotency_key='restart')
        old.close()
        new=self.manager();new.recover()
        result=self.done(new,row['id'])
        self.assertEqual(result['status'],'done');self.assertEqual(result['attempts'],1)
        self.assertEqual(new.engine.calls[0][0].steps,12)
        self.assertEqual(self.submit(new,steps=12,idempotency_key='restart')['id'],row['id'])
    def test_priority_fifo_and_no_preemption(self):
        manager=self.manager();manager.engine.gate=threading.Event();manager.recover()
        first=self.submit(manager,'Active.');self.assertTrue(manager.engine.started.wait(2))
        normal=[self.submit(manager,t) for t in ['Normal one.','Normal two.']]
        fast=[self.submit(manager,t,generate_immediately=True) for t in ['Immediate one.','Immediate two.']]
        self.assertEqual(len(manager.engine.calls),1)
        manager.engine.gate.set();self.done(manager,normal[-1]['id'])
        self.assertEqual([call[0].text for call in manager.engine.calls],
            ['Active.','Immediate one.','Immediate two.','Normal one.','Normal two.'])
        self.assertEqual(manager.read(first['id'])['status'],'done')
        self.assertLess(fast[0]['sequence'],fast[1]['sequence'])
    def test_interrupted_running_recovered_in_original_order(self):
        old=self.manager()
        first=self.submit(old,'First.');second=self.submit(old,'Second.')
        claimed=old.store.claim('voxcpm2');self.assertEqual(claimed['id'],first['id']);old.close()
        new=self.manager();new.recover();self.done(new,second['id'])
        self.assertEqual([c[0].text for c in new.engine.calls],['First.','Second.'])
        self.assertEqual(new.read(first['id'])['attempts'],2)
        self.assertEqual(new.read(first['id'])['sequence'],first['sequence'])
    def test_crash_after_complete_artifact_reconciles_without_inference(self):
        old=self.manager();row=self.submit(old);record=old.store.claim('voxcpm2')
        destination=Path(record['payload']['destination']);destination.write_bytes(wav())
        atomic_json(destination.with_suffix('.json'),dict(id=row['id'],text='Hello.',created_at=row['created_at'],
            sample_rate=16000,duration=2.,path=str(destination),backend='voxcpm2'))
        old.close();new=self.manager();new.recover()
        self.assertEqual(new.read(row['id'])['status'],'done');self.assertEqual(new.engine.calls,[])
        self.assertTrue((self.root/'artifact-index'/f'{row["id"]}.json').is_file())
    def test_corrupt_completion_marker_requires_review(self):
        old=self.manager();row=self.submit(old);record=old.store.claim('voxcpm2')
        Path(record['payload']['destination']).with_suffix('.json').write_text('{broken')
        old.close();new=self.manager();new.recover()
        self.assertEqual(new.read(row['id'])['status'],'error');self.assertEqual(new.engine.calls,[])
    def test_queued_completion_marker_is_also_reconciled(self):
        old=self.manager();row=self.submit(old)
        destination=Path(old.store.read(row['id'])['payload']['destination']);destination.write_bytes(wav())
        atomic_json(destination.with_suffix('.json'),dict(id=row['id'],text='Hello.',created_at=row['created_at'],sample_rate=16000,duration=2.))
        old.close();new=self.manager();new.recover()
        self.assertEqual(self.done(new,row['id'])['status'],'done');self.assertEqual(new.engine.calls,[])
    def test_error_requires_explicit_retry_and_same_key_does_not_reexecute(self):
        manager=self.manager();manager.engine.fail=True;manager.recover()
        row=self.submit(manager,idempotency_key='failure');self.assertEqual(self.done(manager,row['id'])['status'],'error')
        self.assertEqual(self.submit(manager,idempotency_key='failure')['id'],row['id']);self.assertEqual(len(manager.engine.calls),1)
        manager.engine.fail=False;manager.retry(row['id'])
        self.assertEqual(self.done(manager,row['id'])['status'],'done');self.assertEqual(manager.read(row['id'])['attempts'],2)
        with self.assertRaises(BusyError):manager.retry(row['id'])
        with self.assertRaises(BusyError):self.submit(manager,text='Different.',idempotency_key='failure')
    def test_delete_clear_leave_active_and_retain_retry_tombstones(self):
        manager=self.manager();manager.engine.gate=threading.Event();manager.recover()
        active=self.submit(manager,'Active.');self.assertTrue(manager.engine.started.wait(2))
        removed=self.submit(manager,'Delete.',idempotency_key='deleted')
        self.assertEqual(manager.remove(removed['id'])['deleted'],1)
        self.assertEqual(self.submit(manager,'Delete.',idempotency_key='deleted')['status'],'deleted')
        self.submit(manager,'Clear one.');self.submit(manager,'Clear two.')
        self.assertEqual(manager.remove()['deleted'],2)
        with self.assertRaises(BusyError):manager.remove(active['id'])
        self.assertEqual(manager.read(active['id'])['status'],'running')
        manager.engine.gate.set();self.done(manager,active['id'])
        self.assertEqual(len(manager.engine.calls),1);self.assertEqual(manager.remove()['deleted'],0)
    def test_delete_or_clear_vs_claim_is_transactional(self):
        manager=self.manager()
        for clear in [False,True]:
            for i in range(10):
                row=self.submit(manager,str(i));barrier=threading.Barrier(2);out=[]
                def claim():
                    barrier.wait();out.append(('claim',manager.store.claim('voxcpm2')))
                def remove():
                    barrier.wait()
                    try:out.append(('remove',manager.store.remove(None if clear else row['id'])))
                    except QueueConflict:out.append(('remove',0))
                a=threading.Thread(target=claim);b=threading.Thread(target=remove);a.start();b.start();a.join();b.join()
                current=manager.read(row['id'])
                self.assertIn(current['status'],['running','deleted'])
                claim_row=next(v for k,v in out if k=='claim')
                if current['status']=='deleted':self.assertIsNone(claim_row)
                else:manager.store.change(row['id'],status='done')
    def test_pagination_filters_counts_and_backend_pausing(self):
        manager=self.manager();peer=self.manager(backend='qwen3')
        one=self.submit(manager,'One.');two=self.submit(manager,'Two.',generate_immediately=True)
        other=self.submit(peer,'Other.')
        page=manager.listing(limit=1)
        self.assertEqual(page['total'],3);self.assertEqual(page['data'][0]['id'],two['id']);self.assertEqual(page['next_offset'],1)
        page=manager.listing(backend='qwen3');self.assertTrue(page['data'][0]['paused']);self.assertEqual(page['counts'],{'queued':1})
        manager.recover();self.done(manager,one['id'])
        self.assertEqual(manager.read(other['id'])['status'],'queued');self.assertEqual(len(manager.engine.calls),2)
        self.assertEqual(manager.remove(backend='qwen3')['deleted'],1)
    def test_shared_accelerator_lock_waits_then_consumes(self):
        handle=(self.root/'gpu.lock').open('a+');fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        manager=self.manager();manager.recover();row=self.submit(manager)
        time.sleep(.25);self.assertEqual(manager.read(row['id'])['status'],'queued');self.assertEqual(manager.engine.calls,[])
        handle.close();self.assertEqual(self.done(manager,row['id'])['status'],'done')
    def test_one_owner_and_two_data_roots_share_one_slot(self):
        manager=self.manager();manager.engine.gate=threading.Event();manager.recover()
        peer=self.manager()
        with self.assertRaises(BusyError):peer.recover()
        root=self.root/'peer'
        for name in ['jobs','artifact-index','outputs']:(root/name).mkdir(parents=True,exist_ok=True)
        other=self.manager(root=root,lock=self.root/'gpu.lock');other.recover()
        first=self.submit(manager);self.assertTrue(manager.engine.started.wait(2))
        second=self.submit(other,'Peer.');time.sleep(.25);self.assertEqual(other.engine.calls,[])
        manager.engine.gate.set()
        self.assertEqual(self.done(manager,first['id'])['status'],'done')
        self.assertEqual(self.done(other,second['id'])['status'],'done')
    def test_concurrent_identical_admission_is_one_entry(self):
        manager=self.manager();barrier=threading.Barrier(5);rows=[]
        def submit():barrier.wait();rows.append(self.submit(manager,idempotency_key='race'))
        threads=[threading.Thread(target=submit) for _ in range(5)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len({r['id'] for r in rows}),1);self.assertEqual(manager.listing()['total'],1)
    def test_missing_volume_fails_before_database_creation(self):
        path=Path('/Volumes/absent-voice-queue-validation/jobs/queue.sqlite3')
        with self.assertRaises(RuntimeError):QueueStore(path.parent.parent).initialize()
        self.assertFalse(path.parent.exists())
    def test_changed_model_mapping_and_revision_are_not_reinterpreted(self):
        manager=self.manager();row=self.submit(manager)
        manager.profile['modes']['design']='another-model'
        manager.recover();self.assertEqual(self.done(manager,row['id'])['status'],'error')
        self.assertEqual(manager.engine.calls,[])

    def test_truncated_wav_with_sidecar_is_not_reconciled_as_complete(self):
        old=self.manager();row=self.submit(old);record=old.store.claim('voxcpm2')
        destination=Path(record['payload']['destination']);destination.write_bytes(wav()[:64])
        atomic_json(destination.with_suffix('.json'),dict(id=row['id'],text='Hello.',created_at=row['created_at'],sample_rate=16000,duration=2.))
        old.close();new=self.manager();new.recover()
        self.assertEqual(new.read(row['id'])['status'],'error');self.assertEqual(new.engine.calls,[])

    def test_all_backend_namespaces_defaults_and_transcripts_survive_queue_restart(self):
        from capabilities import validate_controls
        cases={
            'voxcpm2':{'steps':12},'qwen3':{'temperature':.8},
            'chatterbox':{'chatterbox':{'exaggeration':.7,'cfg_weight':.3}},
            'indextts':{'language':'English','indextts':{'duration_factor':1.2,'interval_silence':650,'emotion_vector':[.1]*8,'emotion_alpha':.6}},
            'moss':{'language':'English'},'fish':{'ref_text':'Exact frozen words.'},'higgs':{'ref_text':'Exact frozen words.'}}
        reference=self.root/'references'/('a'*32+'.wav');reference.write_bytes(wav())
        for backend,settings in cases.items():
            with self.subTest(backend=backend):
                directory=PROJECT_ROOT/'backends'/backend
                profile=json.loads((directory/'service.json').read_text())
                specs=json.loads((directory/'model_specs.json').read_text())
                referenced=backend in ['chatterbox','indextts','fish','higgs']
                request=Generation(text='Backend fixture.',description='' if referenced else 'A calm voice.',
                    reference_id='a'*32 if referenced else None,**settings)
                with patch('capabilities.PROFILE',profile),patch('capabilities.MODEL_SPECS',specs),patch('capabilities.EXTENSION_SPECS',{}),patch('jobs.MODEL_SPECS',specs):
                    effective=validate_controls(request)
                    manager=self.manager(backend=backend);manager.profile=profile
                    row=manager.submit(request,effective,reference if referenced else None,
                        request.ref_text or '',self.root/'outputs')
                    frozen=manager.store.read(row['id'])['payload']['effective']
                    manager.close()
                    fresh=self.manager(backend=backend);fresh.profile=profile;fresh.recover()
                    result=self.done(fresh,row['id']);self.assertEqual(result['status'],'done',result)
                    applied=fresh.engine.calls[0][0]
                    self.assertEqual(applied.temperature,effective.temperature)
                    self.assertEqual(result['backend'],backend)
                    self.assertEqual(result['result']['description'],effective.description)
                    self.assertEqual(result['result']['effective']['temperature'],frozen['temperature'])
                    if backend in ['chatterbox','indextts']:
                        self.assertEqual(result['result']['effective'][backend],frozen[backend])
                        self.assertNotIn('duration_factor',result['effective'])
                        self.assertEqual(applied.duration_factor,effective.duration_factor)
                    if backend in ['fish','higgs']:
                        self.assertEqual(fresh.engine.calls[0][2],'Exact frozen words.')
                        self.assertEqual(result['result']['effective']['ref_text'],'Exact frozen words.')
                    fresh.close()

    def test_emotion_reference_hash_blocks_changed_secondary_conditioning(self):
        from capabilities import validate_controls
        profile=json.loads((PROJECT_ROOT/'backends/indextts/service.json').read_text())
        speaker=self.root/'references'/('a'*32+'.wav');speaker.write_bytes(wav())
        emotion=self.root/'references'/('b'*32+'.wav');emotion.write_bytes(wav())
        manager=self.manager(backend='indextts');manager.profile=profile
        for fields in [{'indextts':{'emotion_reference_id':'b'*32}}, {'emotion_reference_id':'b'*32}]:
            with patch('capabilities.PROFILE',profile):
                request=Generation(text='Emotion fixture.',reference_id='a'*32,language='English',**fields)
                effective=validate_controls(request)
                row=manager.submit(request,effective,speaker,'',self.root/'outputs')
                self.assertIn('emotion',manager.store.read(row['id'])['payload']['aux_references'])
        emotion.write_bytes(wav(seconds=3));manager.recover()
        page=manager.listing(statuses=())
        for row in page['data']:
            self.assertEqual(self.done(manager,row['id'])['status'],'error')
        self.assertEqual(manager.engine.calls,[])

    def test_unreadable_wav_marker_fails_job_without_blocking_startup(self):
        old=self.manager();row=self.submit(old);record=old.store.claim('voxcpm2')
        destination=Path(record['payload']['destination']);destination.write_bytes(b'RIFF')
        atomic_json(destination.with_suffix('.json'),dict(id=row['id'],text='Hello.',created_at=row['created_at'],sample_rate=16000,duration=2.))
        old.close();new=self.manager();new.recover()
        self.assertEqual(new.read(row['id'])['status'],'error')
        next_row=self.submit(new,'Valid work after invalid marker.')
        self.assertEqual(self.done(new,next_row['id'])['status'],'done')

    def test_worker_binds_actual_server_owner_and_accelerator_handles(self):
        import os
        from data_owner import own_data_directory
        from inference_locks import reservation_descriptors
        manager=self.manager();original=manager.engine.generate;captured=[]
        with own_data_directory(self.root) as owner:
            manager.data_owner=owner
            def generate(*args,**kwargs):
                descriptors=reservation_descriptors();captured.extend(descriptors)
                self.assertIn(owner.fileno(),descriptors)
                self.assertIn(manager._owner.fileno(),descriptors)
                self.assertEqual(len(descriptors),3)
                self.assertIn(os.stat(self.root/'gpu.lock').st_ino,[os.fstat(fd).st_ino for fd in descriptors])
                return original(*args,**kwargs)
            manager.engine.generate=generate;manager.recover();row=self.submit(manager)
            self.assertEqual(self.done(manager,row['id'])['status'],'done')
            manager.close()
        self.assertEqual(len(captured),3)

    def test_same_key_cannot_change_backend(self):
        manager=self.manager();self.submit(manager,idempotency_key='backend')
        other=self.manager(backend='qwen3')
        with self.assertRaises(BusyError):self.submit(other,idempotency_key='backend')

    def test_reference_is_pinned_to_admitted_bytes_and_transcript(self):
        manager=self.manager();reference=self.root/'references'/('a'*32+'.wav');reference.write_bytes(wav())
        request=Generation(text='Reference.',reference_id='a'*32,ref_text=None)
        row=manager.submit(request,request,reference,'Original transcript.',self.root/'outputs')
        reference.write_bytes(wav(seconds=3));manager.recover()
        self.assertEqual(self.done(manager,row['id'])['status'],'error');self.assertEqual(manager.engine.calls,[])


class QueueAPITests(QueueFixture):
    # API test uses this shared SSD fixture without duplicating the worker tests.
    def test_typed_api_submission_listing_deletion_and_validation(self):
        import server
        manager=self.manager();manager.engine.gate=threading.Event()
        with ExitStack() as stack:
            stack.enter_context(patch.object(server.app.state,'llm_enabled',False))
            for key,value in [('manager',manager),('engine',manager.engine),('all_errors',lambda:[]),('runtime_errors',lambda:[]),('prepare_storage',lambda:None),('ROOT',self.root)]:
                stack.enter_context(patch('server.'+key,value))
            with TestClient(server.app) as client:
                body={'text':'API fixture.','description':'A calm voice.'}
                first=client.post('/api/generate',json=body);self.assertEqual(first.status_code,202)
                self.assertTrue(manager.engine.started.wait(2));self.assertFalse(first.json()['generate_immediately'])
                row=client.post('/api/generate',json={**body,'generate_immediately':True,'idempotency_key':'api'}).json()
                self.assertTrue(row['generate_immediately']);self.assertEqual(row['status'],'queued')
                self.assertEqual(client.get('/api/queue?limit=1').json()['total'],2)
                self.assertEqual(client.get('/api/jobs/'+row['id']).status_code,200)
                self.assertEqual(client.delete('/api/queue/'+first.json()['id']).status_code,409)
                self.assertEqual(client.delete('/api/queue/'+row['id']).json()['deleted'],1)
                self.assertEqual(client.get('/api/jobs/'+row['id']).json()['status'],'deleted')
                client.post('/api/generate',json=body)
                self.assertEqual(client.delete('/api/queue').json()['deleted'],1)
                self.assertIn('queue_counts',client.get('/health').json())
                for url in ['/api/queue?limit=0','/api/queue?offset=-1','/api/queue?status=bad']:
                    self.assertEqual(client.get(url).status_code,422)
                for fields in [{'unknown':1},{'text':' '},{'steps':99},{'temperature':float('inf')}]:
                    response=client.post('/api/generate',content=json.dumps({**body,**fields}),headers={'Content-Type':'application/json'})
                    self.assertEqual(response.status_code,422,response.text)
                self.assertEqual(client.delete('/api/queue/bad').status_code,404)
                self.assertEqual(client.post('/api/jobs/'+row['id']+'/retry',json={}).status_code,409)
                manager.engine.gate.set()


if __name__=='__main__':unittest.main()

class ProjectQueueTests(QueueFixture):
    def test_migration_preserves_entire_database_and_backup(self):
        original=QueueStore(self.root);original.initialize()
        original.admit('voxcpm2','test','design',True,{'destination':str(self.root/'outputs')},'key','hash')
        target=QueueStore(self.root,self.root/'state/queue.sqlite3')
        import sqlite3
        with closing(sqlite3.connect(original.path)) as db: before=list(db.iterdump())
        target.migrate()
        self.assertFalse(original.path.exists())
        self.assertTrue(original.path.with_name('queue.sqlite3.migrated-backup').is_file())
        with closing(sqlite3.connect(target.path)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(list(db.iterdump()),before)
        target.migrate() # clean restart is idempotent

    def test_migration_collision_preserves_both_databases(self):
        original=QueueStore(self.root);original.initialize()
        target=QueueStore(self.root,self.root/'state/queue.sqlite3');target.initialize()
        with self.assertRaisesRegex(RuntimeError,'Both legacy'):target.migrate()
        self.assertTrue(original.path.exists());self.assertTrue(target.path.exists())

    def test_same_database_different_data_roots_share_worker_ownership(self):
        db=self.root/'state/queue.sqlite3'
        first=JobManager(FakeEngine(),self.root,self.root/'gpu.lock',db_path=db)
        other=self.root/'other';(other/'jobs').mkdir(parents=True)
        second=JobManager(FakeEngine(),other,self.root/'gpu.lock',db_path=db)
        self.managers.extend([first,second]);first.recover()
        with self.assertRaisesRegex(BusyError,'SQLite'):second.recover()
