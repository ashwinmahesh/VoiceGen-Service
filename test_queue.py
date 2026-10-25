"""Durability/concurrency tests: fake engines only, no weights or GPU work."""
from contextlib import ExitStack
import fcntl
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from config import ROOT
from durable_queue import QueueStore, QueueConflict
from jobs import JobManager, BusyError
from schemas import Generation
from test_contract import FakeEngine, wav
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
        root=self.root/'peer';(root/'jobs').mkdir(parents=True)
        other=self.manager(root=root,lock=self.root/'gpu.lock');other.recover()
        first=self.submit(manager);self.assertTrue(manager.engine.started.wait(2))
        second=self.submit(other,'Peer.');time.sleep(.25);self.assertEqual(other.engine.calls,[])
        manager.engine.gate.set();self.done(manager,first['id']);self.done(other,second['id'])
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
            for key,value in [('manager',manager),('engine',manager.engine),('all_errors',lambda:[]),('prepare_storage',lambda:None),('ROOT',self.root)]:
                stack.enter_context(patch('server.'+key,value))
            with TestClient(server.app) as client:
                body={'text':'API fixture.','description':'A calm voice.'}
                first=client.post('/api/generate',json=body);self.assertEqual(first.status_code,202)
                self.assertTrue(manager.engine.started.wait(2));self.assertFalse(first.json()['generate_immediately'])
                row=client.post('/api/jobs',json={**body,'generate_immediately':True,'idempotency_key':'api'}).json()
                self.assertTrue(row['generate_immediately']);self.assertEqual(row['status'],'queued')
                self.assertEqual(client.get('/api/jobs?limit=1').json()['total'],2)
                self.assertEqual(client.get('/api/queue/'+row['id']).status_code,200)
                self.assertEqual(client.delete('/api/jobs/'+first.json()['id']).status_code,409)
                self.assertEqual(client.delete('/api/queue/'+row['id']).json()['deleted'],1)
                self.assertEqual(client.get('/api/jobs/'+row['id']).json()['status'],'deleted')
                client.post('/api/queue',json=body)
                self.assertEqual(client.delete('/api/queue').json()['deleted'],1)
                self.assertIn('queue_counts',client.get('/health').json())
                for url in ['/api/queue?limit=0','/api/queue?offset=-1','/api/queue?status=bad']:
                    self.assertEqual(client.get(url).status_code,422)
                for fields in [{'unknown':1},{'text':' '},{'steps':99},{'temperature':float('inf')}]:
                    response=client.post('/api/queue',content=json.dumps({**body,**fields}),headers={'Content-Type':'application/json'})
                    self.assertEqual(response.status_code,422,response.text)
                self.assertEqual(client.delete('/api/queue/bad').status_code,404)
                self.assertEqual(client.post('/api/queue/'+row['id']+'/retry',json={}).status_code,409)
                manager.engine.gate.set()


if __name__=='__main__':unittest.main()
