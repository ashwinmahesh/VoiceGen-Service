"""Persisted scheduling, request identity and worker races; fake speech only."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from config import PROJECT_ROOT
from durable_queue import QueueStore, QueueConflict
from queue_schemas import QueueMove
import queue_server
from . import test_contract, test_queue_service


class QueueOrderTests(unittest.TestCase):
    def setUp(self):
        base=PROJECT_ROOT/'state/validation';base.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='order-',dir=base);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=QueueStore(self.root,self.root/'queue.sqlite3');self.store.initialize()

    def add(self,text,immediate=False,key=None,model='voxcpm2'):
        return self.store.admit_opaque(model,{'text':text,'description':'A calm voice.'},immediate,key)

    def order(self):return [row['id'] for row in self.store.listing(statuses=('queued',),limit=100)['data']]

    def test_migrate_old_schema_once_and_preserve_reordered_state(self):
        a=self.add('A');b=self.add('B',True);c=self.add('C',key='stable')
        originals={row['id']:{k:v for k,v in row.items() if k!='queue_order'} for row in (a,b,c)}
        with self.store.transaction() as db:
            db.execute('DROP INDEX runnable_order');db.execute('ALTER TABLE entries DROP COLUMN queue_order')
        self.store.initialize()
        self.assertEqual(self.order(),[b['id'],a['id'],c['id']])
        for row in (a,b,c):
            migrated=self.store.read(row['id']);self.assertEqual(migrated.pop('queue_order'),row['sequence'])
            self.assertEqual(migrated,originals[row['id']])
        self.store.move(c['id'],before_id=b['id'])
        reopened=QueueStore(self.root,self.store.path);reopened.initialize()
        self.assertEqual([r['id'] for r in reopened.listing()['data']],[c['id'],b['id'],a['id']])

    def test_moves_across_priority_boundary_and_later_admissions(self):
        a=self.add('A');b=self.add('B');c=self.add('C',True)
        self.store.move(b['id'],before_id=c['id'])
        self.assertEqual(self.order(),[b['id'],c['id'],a['id']]);self.assertTrue(self.store.read(b['id'])['generate_immediately'])
        self.store.move(c['id'],after_id=a['id'])
        self.assertEqual(self.order(),[b['id'],a['id'],c['id']]);self.assertFalse(self.store.read(c['id'])['generate_immediately'])
        d=self.add('D',True);e=self.add('E')
        self.assertEqual(self.order(),[b['id'],d['id'],a['id'],c['id'],e['id']])
        self.store.move(e['id'],direction='up');self.assertEqual(self.order()[-2:],[e['id'],c['id']])
        self.store.move(e['id'],direction='down');self.assertEqual(self.order()[-2:],[c['id'],e['id']])

    def test_noop_moves_do_not_demote_priority_or_change_sequence(self):
        a=self.add('A',True);b=self.add('B')
        for target in ({'direction':'up'},{'before_id':b['id']}):
            unchanged=self.store.move(a['id'],**target)
            self.assertTrue(unchanged['generate_immediately']);self.assertEqual(unchanged['sequence'],a['sequence'])
        self.assertEqual(self.store.move(b['id'],direction='down')['id'],b['id'])

    def test_run_now_is_first_preserves_frozen_payload_and_key_replay(self):
        a=self.add('A',True);b=self.add('B',True);c=self.add('C',key='keyed')
        promoted=self.store.run_now(c['id'])
        self.assertEqual(self.order(),[c['id'],a['id'],b['id']])
        for name in ('id','sequence','payload','fingerprint','key_hash','created_at'):
            self.assertEqual(promoted[name],c[name])
        self.assertTrue(promoted['generate_immediately'])
        self.assertEqual(self.add('C',key='keyed')['id'],c['id'])
        with self.assertRaises(QueueConflict):self.add('C',True,key='keyed')
        self.store.run_now(c['id']);self.assertEqual(self.order(),[c['id'],a['id'],b['id']])
        self.assertEqual(self.store.claim('voxcpm2')['id'],c['id'])

    def test_unselected_models_wait_and_active_jobs_are_unchanged(self):
        active=self.add('Active');self.store.claim('voxcpm2');snapshot=self.store.read(active['id'])
        a=self.add('A');other=self.add('Other',model='qwen3')
        self.store.run_now(other['id'])
        self.assertEqual(self.store.claim('voxcpm2')['id'],a['id'])
        self.assertEqual(self.store.read(other['id'])['status'],'queued')
        self.assertEqual(self.store.read(active['id']),snapshot)
        for action in (self.store.run_now,lambda identity:self.store.move(identity,direction='up')):
            with self.assertRaises(QueueConflict):action(active['id'])

    def test_invalid_targets_and_terminal_states_do_not_change_order(self):
        a=self.add('A');b=self.add('B');expected=self.order()
        with self.assertRaises(ValueError):self.store.move(a['id'],before_id=a['id'])
        with self.assertRaises(FileNotFoundError):self.store.move(a['id'],after_id='f'*32)
        for status in ('running','done','error','deleted'):
            self.store.change(b['id'],status=status)
            with self.assertRaises(QueueConflict):self.store.move(a['id'],before_id=b['id'])
            with self.assertRaises(QueueConflict):self.store.run_now(b['id'])
            self.store.change(b['id'],status='queued')
        self.assertEqual(self.order(),expected)
        for body in ({},{'direction':'left'},{'direction':'up','before_id':a['id']},{'before_id':'bad'},{'direction':'up','unknown':True}):
            with self.assertRaises(ValueError):QueueMove.model_validate(body)

    def test_claim_and_run_now_serialize_without_duplicate_work(self):
        row=self.add('Race');barrier=threading.Barrier(2)
        def promote():
            barrier.wait()
            try:return self.store.run_now(row['id'])
            except QueueConflict:return None
        def claim():barrier.wait();return self.store.claim('voxcpm2')
        with ThreadPoolExecutor(2) as pool:
            futures=[pool.submit(promote),pool.submit(claim)]
            results=[future.result() for future in futures]
        self.assertEqual(results[1]['id'],row['id'])
        self.assertEqual(self.store.read(row['id'])['attempts'],1)
        self.assertIsNone(self.store.claim('voxcpm2'))


class QueueOrderApiTests(unittest.TestCase):
    def setUp(self):
        self.f=test_contract.ContractTests('runTest');self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.engine.gate=threading.Event()
        self.active=self.f.generate().json();self.assertTrue(self.f.engine.started.wait(2))
        self.a=self.f.generate(text='A.',idempotency_key='original').json();self.b=self.f.generate(text='B.').json()
        self.patcher=patch.object(queue_server,'store',self.f.manager.store);self.patcher.start();self.addCleanup(self.patcher.stop)
        self.queue=TestClient(queue_server.app);self.addCleanup(self.queue.close)

    def test_both_generator_aliases_and_standalone_routes(self):
        for client,prefix in ((self.f.client,'/api/jobs'),(self.f.client,'/api/queue'),(self.queue,'/api/queue')):
            with self.subTest(prefix=prefix,client=client):
                moved=client.post(prefix+'/'+self.b['id']+'/move',json={'before_id':self.a['id']})
                self.assertEqual(moved.status_code,200,moved.text);self.assertIsInstance(moved.json()['queue_order'],int)
                promoted=client.post(prefix+'/'+self.a['id']+'/run-now')
                self.assertEqual(promoted.status_code,200,promoted.text);self.assertTrue(promoted.json()['generate_immediately'])
                self.assertEqual(client.post(prefix+'/'+self.active['id']+'/run-now').status_code,409)
                self.assertEqual(client.post(prefix+'/'+self.a['id']+'/move',json={'before_id':self.active['id']}).status_code,409)
                self.assertEqual(client.post(prefix+'/'+self.a['id']+'/move',json={'after_id':'f'*32}).status_code,404)
                for body in ({},{'before_id':self.a['id']},{'direction':'down','after_id':self.b['id']}):
                    self.assertEqual(client.post(prefix+'/'+self.a['id']+'/move',json=body).status_code,422)
        replay=self.f.generate(text='A.',idempotency_key='original').json()
        self.assertEqual(replay['id'],self.a['id']);self.assertTrue(replay['generate_immediately'])
        self.assertFalse(replay['requested']['generate_immediately'])
        self.assertEqual(self.f.client.get('/api/jobs/'+self.active['id']).json()['status'],'running')

    def test_scheduling_storage_errors_remain_retryable_on_both_services(self):
        for method,suffix,body in (('move','move',{'direction':'up'}),('run_now','run-now',None)):
            for client in (self.f.client,self.queue):
                with patch.object(self.f.manager.store,method,side_effect=sqlite3.OperationalError('private SQL failure')):
                    response=client.post('/api/queue/'+self.a['id']+'/'+suffix,json=body)
                    self.assertEqual(response.status_code,503,response.text)
                    self.assertEqual(response.headers['Retry-After'],'2');self.assertNotIn('private SQL',response.text)


class QueueOrderProcessTests(unittest.TestCase):
    def test_run_next_after_active_job_and_queue_api_restart(self):
        f=test_queue_service.QueueServiceTests('runTest');f.setUp();self.addCleanup(f.tearDown)
        f.start_queue();active=f.submit('Held.');f.start_generator()
        test_queue_service.wait_for(lambda:f.row(active['id'])['status']=='running')
        a=f.submit('A.');b=f.submit('B.',generate_immediately=True);c=f.submit('C.')
        def order(port):return [row['id'] for row in test_queue_service.request(port,'/api/queue')[1]['data']]
        self.assertEqual(test_queue_service.request(f.queue_port,'/api/queue/'+c['id']+'/run-now','POST')[0],200)
        self.assertEqual(test_queue_service.request(f.queue_port,'/api/queue/'+a['id']+'/move','POST',{'before_id':b['id']})[0],200)
        stopped=subprocess.run([sys.executable,'queue_manage.py','stop','--port',str(f.queue_port)],cwd=PROJECT_ROOT,env=f.env,capture_output=True,text=True)
        self.assertEqual(stopped.returncode,0,stopped.stdout+stopped.stderr)
        self.assertEqual(test_queue_service.request(f.gen_port,'/api/jobs/'+b['id']+'/run-now','POST')[0],200)
        self.assertEqual(order(f.gen_port),[active['id'],b['id'],c['id'],a['id']])
        f.start_queue();self.assertEqual(order(f.queue_port),[active['id'],b['id'],c['id'],a['id']])
        self.assertEqual(len((f.media/'calls.jsonl').read_text().splitlines()),1)
        (f.media/'release').touch()
        for row in (active,b,c,a):self.assertEqual(f.done(row['id'])['status'],'done')
        self.assertEqual([json.loads(line)['text'] for line in (f.media/'calls.jsonl').read_text().splitlines()],['Held.','B.','C.','A.'])
