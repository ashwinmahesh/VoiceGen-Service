"""Process-level queue/generator isolation tests. No model assets or real inference."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]


def request(port,path='/health',method='GET',payload=None,origin=None):
    headers={'Content-Type':'application/json'}
    if origin:headers['Origin']=origin
    req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,
        data=json.dumps(payload).encode() if payload is not None else None,method=method,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=2) as response:
            return response.status,json.load(response)
    except urllib.error.HTTPError as exc:
        with exc:return exc.code,json.load(exc)


def wait_for(check,timeout=10):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            value=check()
            if value:return value
        except (OSError,ValueError):pass
        time.sleep(.04)
    raise AssertionError('Timed out waiting for process state')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def fake_generator():
    """Test harness injects a fake adapter into the real generator/lifecycle."""
    import math
    import struct
    import wave
    import server
    import storage
    from jobs import JobManager
    from queue_config import QUEUE_DB
    root=Path(os.environ['DATA_DIR'])
    for folder in ('jobs','outputs','voices','references','artifact-index','idempotency','cache/tmp'):
        (root/folder).mkdir(parents=True,exist_ok=True)
    class FakeEngine:
        model=None
        variant=None
        def unload(self):self.model=None;self.variant=None
        def generate(self,req,reference,destination,on_stage,transcript=''):
            on_stage('Generating test fixture')
            with (root/'calls.jsonl').open('a') as stream:
                stream.write(json.dumps({'id':destination.stem,'text':req.text})+'\n')
            if req.text=='Held.':
                wait_for(lambda:(root/'release').exists(),timeout=30)
            with wave.open(str(destination),'wb') as audio:
                audio.setparams((1,2,16000,0,'NONE','not compressed'))
                audio.writeframes(b''.join(struct.pack('<h',int(3000*math.sin(n*.1))) for n in range(16000)))
            return {'duration':1.,'sample_rate':16000,'generation_seconds':.01,'waveform':[.1]}
    engine=FakeEngine()
    server.ROOT=storage.ROOT=root
    storage.OUTPUT_DIR=root/'outputs'
    server.engine=engine
    server.manager=JobManager(engine,root,root/'gpu.lock',db_path=QUEUE_DB)
    server.all_errors=lambda:[]
    server.runtime_errors=lambda:[]
    server.prepare_storage=lambda:None
    server.app.state.llm_enabled=False
    server.run_server(int(os.environ['TEST_GENERATOR_PORT']),int(os.environ['QUEUE_PORT']))


class QueueServiceTests(unittest.TestCase):
    def setUp(self):
        validation=PROJECT/'state/validation';validation.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='queue-services-',dir=validation)
        self.root=Path(self.tmp.name);self.media=self.root/'media'
        self.queue_port=free_port();self.gen_port=free_port()
        self.env={**os.environ,'QUEUE_DB':str(self.root/'queue.sqlite3'),'QUEUE_PORT':str(self.queue_port),
                  'QUEUE_PYTHON':sys.executable,'DATA_DIR':str(self.media),'MODEL_BACKEND':'voxcpm2',
                  'TEST_GENERATOR_PORT':str(self.gen_port)}
        self.processes=[];self.streams=[]
    def tearDown(self):
        (self.media).mkdir(exist_ok=True);(self.media/'release').touch()
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        record=self.root/f'queue-{self.queue_port}.pid'
        if record.exists():
            # Only the child created by this isolated fixture is ever targeted.
            subprocess.run([sys.executable,'queue_manage.py','stop','--port',str(self.queue_port)],
                           cwd=PROJECT,env=self.env,capture_output=True,timeout=15)
        for stream in self.streams:stream.close()
        self.tmp.cleanup()
    def spawn(self,*args,env=None):
        stream=(self.root/f'process-{len(self.processes)}.log').open('w')
        self.streams.append(stream)
        process=subprocess.Popen([sys.executable,*args],cwd=PROJECT,env=env or self.env,
                                 stdout=stream,stderr=stream,start_new_session=True)
        self.processes.append(process);return process
    def start_queue(self):
        result=subprocess.run([sys.executable,'queue_manage.py','start','--port',str(self.queue_port)],
                              cwd=PROJECT,env=self.env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        return int((self.root/f'queue-{self.queue_port}.pid').read_text())
    def start_generator(self):
        process=self.spawn('-m','tests.test_queue_service','--fake-generator')
        wait_for(lambda:request(self.gen_port)[1].get('service'))
        return process
    def submit(self,text='Hello.',**kw):
        status,row=request(self.queue_port,'/api/queue','POST',{
            'model':'voxcpm2','payload':{'text':text,'description':'A calm baritone.'},**kw})
        self.assertEqual(status,202,row);return row
    def row(self,identity):return request(self.queue_port,'/api/queue/'+identity)[1]
    def done(self,identity):return wait_for(lambda:(r if (r:=self.row(identity))['status'] in ('done','error') else None))
    def test_queue_import_and_crud_without_models_or_external_volume(self):
        code='''import sys, importlib.abc
class NoInference(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in {'config','server','engine','jobs','schemas','backend_selection','torch','mlx','numpy','soundfile','transformers'}:raise RuntimeError('Queue imported '+fullname)
sys.meta_path.insert(0,NoInference())
import queue_server
assert not any(n in sys.modules for n in ('config','engine','jobs','torch','mlx','numpy'))
assert queue_server.list_models()['default_model']=='higgs'
assert len(queue_server.list_models()['models'])==7
'''
        env={**self.env,'DATA_DIR':'/Volumes/voicegen-unmounted-test/absent','MODEL_BACKEND':'not-a-model'}
        probe=subprocess.run([sys.executable,'-c',code],cwd=PROJECT,env=env,capture_output=True,text=True)
        self.assertEqual(probe.returncode,0,probe.stderr)
        process=self.spawn('queue_server.py','--port',str(self.queue_port),env=env)
        wait_for(lambda:request(self.queue_port)[1].get('role')=='queue-crud')
        # Routing and payload are opaque; unsupported model-like strings are accepted.
        status,row=request(self.queue_port,'/api/queue','POST',{'model':'anything','payload':{'arbitrary':['json',1]}})
        self.assertEqual(status,202);self.assertEqual(row['status'],'queued')
        status,changed=request(self.queue_port,'/api/queue/'+row['id'],'PATCH',{'payload':{'new':True},'generate_immediately':True})
        self.assertEqual(status,200);self.assertEqual(changed['payload'],{'new':True})
        status,page=request(self.queue_port,'/api/queue?limit=1')
        self.assertEqual((status,page['total']),(200,1))
        self.assertEqual(request(self.queue_port,'/api/queue/'+row['id'],'DELETE')[1]['deleted'],1)
        self.assertEqual(request(self.queue_port,'/api/queue','DELETE')[1]['deleted'],0)
        self.assertEqual(request(self.queue_port,'/api/queue/missing')[0],404)
        self.assertEqual(request(self.queue_port,'/api/queue?limit=0')[0],422)
        self.assertEqual(request(self.queue_port,'/')[0],404)
        schema=request(self.queue_port,'/openapi.json')[1]['paths']
        self.assertNotIn('/api/generate',schema);self.assertNotIn('/api/capabilities',schema)
        self.assertIn('/api/models',schema)
        status,models=request(self.queue_port,'/api/models')
        self.assertEqual(status,200)
        self.assertEqual(models['default_model'],'higgs')
        expected={'voxcpm2':'VoxCPM2','qwen3':'Qwen3-TTS','chatterbox':'Chatterbox Original',
                  'indextts':'IndexTTS 2.5','moss':'MOSS VoiceGenerator','fish':'Fish Audio S2 Pro','higgs':'Higgs TTS 3'}
        self.assertEqual({row['id']:row['name'] for row in models['models']},expected)
        self.assertEqual([row['id'] for row in models['models'] if row['is_default']],['higgs'])
        self.assertEqual(len(models['models']),len(expected))
        # Listing is static metadata: it must not create media directories or queue work.
        self.assertEqual(request(self.queue_port,'/api/queue')[1]['total'],0)
        status,defaulted=request(self.queue_port,'/api/queue','POST',{'payload':{'arbitrary':True}})
        self.assertEqual((status,defaulted['model']),(202,'higgs'))
        self.assertEqual(request(self.queue_port)[1]['default_model'],'higgs')
        submission_schema=request(self.queue_port,'/openapi.json')[1]['components']['schemas']['Submission']
        self.assertNotIn('model',submission_schema['required'])
        self.assertEqual(submission_schema['properties']['model']['default'],'higgs')
        self.assertFalse(self.media.exists());self.assertIsNone(process.poll())
    def test_idempotency_priority_waiting_only_and_bad_json(self):
        self.start_queue()
        first=self.submit(idempotency_key='key')
        self.assertEqual(self.submit(idempotency_key='key')['id'],first['id'])
        status,_=request(self.queue_port,'/api/queue','POST',{'model':'voxcpm2','payload':{'text':'changed'},'idempotency_key':'key'})
        self.assertEqual(status,409)
        self.assertEqual(request(self.queue_port,'/api/queue/'+first['id'],'PATCH',{'payload':{}})[0],409)
        fast=self.submit('Priority.',generate_immediately=True)
        page=request(self.queue_port,'/api/queue?limit=1')[1]
        self.assertEqual(page['data'][0]['id'],fast['id']);self.assertEqual(page['next_offset'],1)
        self.assertEqual(request(self.queue_port,'/api/queue','POST',{'model':'x','payload':{'value':float('nan')}})[0],422)
        self.assertEqual(request(self.queue_port,'/api/queue','POST',{'model':'x','payload':{}},origin='http://evil.example')[0],403)
        self.assertEqual(request(self.queue_port,'/api/queue','DELETE')[1]['deleted'],2)
        self.assertEqual(self.submit(idempotency_key='key')['status'],'deleted')

    def test_dedicated_queue_environment_can_be_managed_by_generator_runtime(self):
        python=PROJECT/'.venv-queue/bin/python'
        if not python.is_file():self.skipTest('Optional dedicated queue environment is not installed')
        self.env['QUEUE_PYTHON']=str(python)
        self.start_queue()
        status,row=request(self.queue_port,'/api/queue','POST',{'payload':{'text':'Default Higgs request.'},'idempotency_key':'default'})
        self.assertEqual((status,row['model']),(202,'higgs'))
        status,again=request(self.queue_port,'/api/queue','POST',{'model':'higgs','payload':{'text':'Default Higgs request.'},'idempotency_key':'default'})
        self.assertEqual((status,again['id']),(202,row['id']))
        stopped=subprocess.run([sys.executable,'queue_manage.py','stop','--port',str(self.queue_port)],
                               cwd=PROJECT,env=self.env,capture_output=True,text=True,timeout=15)
        self.assertEqual(stopped.returncode,0,stopped.stderr)
    def test_generator_keeps_ui_api_queue_survives_stop_and_restart(self):
        generator=self.start_generator()  # Automatically starts an independent queue process.
        self.assertEqual(request(self.gen_port,'/api/capabilities')[0],200)
        with urllib.request.urlopen(f'http://127.0.0.1:{self.gen_port}/') as response:
            self.assertIn(b'<html',response.read().lower())
        active=self.submit('Held.');pending=self.submit('After restart.')
        wait_for(lambda:self.row(active['id'])['status']=='running')
        self.assertEqual(request(self.queue_port,'/api/queue/'+active['id'],'DELETE')[0],409)
        generator.send_signal(signal.SIGINT)
        time.sleep(.25)
        self.assertIsNone(generator.poll())  # Graceful finish, not cancellation.
        self.assertEqual(self.row(pending['id'])['status'],'queued')
        (self.media/'release').touch();generator.wait(timeout=10)
        self.assertEqual(self.row(active['id'])['status'],'done')
        self.assertEqual(self.row(pending['id'])['status'],'queued')
        self.assertEqual(request(self.queue_port)[1]['role'],'queue-crud')
        restarted=self.start_generator()
        result=self.done(pending['id']);self.assertEqual(result['status'],'done',result)
        self.assertEqual(result['attempts'],1)
        restarted.terminate();restarted.wait(timeout=10)
        calls=[json.loads(line)['text'] for line in (self.media/'calls.jsonl').read_text().splitlines()]
        self.assertEqual(calls,['Held.','After restart.'])
    def test_queue_restart_does_not_recover_active_job_or_duplicate_consumers(self):
        self.start_queue();generator=self.start_generator()
        active=self.submit('Held.')
        wait_for(lambda:(self.media/'calls.jsonl').exists())
        second=self.spawn('-m','tests.test_queue_service','--fake-generator',env={**self.env,'TEST_GENERATOR_PORT':str(free_port())})
        self.assertNotEqual(second.wait(timeout=10),0)
        duplicate=self.spawn('queue_server.py','--port',str(free_port()))
        self.assertNotEqual(duplicate.wait(timeout=10),0)
        pid=self.start_queue();self.assertEqual(self.start_queue(),pid)
        stopped=subprocess.run([sys.executable,'queue_manage.py','stop','--port',str(self.queue_port)],
                               cwd=PROJECT,env=self.env,capture_output=True,text=True,timeout=15)
        self.assertEqual(stopped.returncode,0,stopped.stderr)
        self.start_queue()
        row=self.row(active['id']);self.assertEqual((row['status'],row['attempts']),('running',1))
        (self.media/'release').touch()
        self.assertEqual(self.done(active['id'])['status'],'done')
        self.assertEqual(len((self.media/'calls.jsonl').read_text().splitlines()),1)
        self.assertIsNone(generator.poll())

    def test_queue_crash_is_reported_while_generator_keeps_processing_and_recovers(self):
        pid=self.start_queue();generator=self.start_generator()
        wait_for(lambda:request(self.gen_port)[1]['queue_service']['status']=='ready')
        active=self.submit('Held.')
        wait_for(lambda:(self.media/'calls.jsonl').exists())
        os.kill(pid,signal.SIGKILL)
        outage=wait_for(lambda:(state if (state:=request(self.gen_port)[1])['queue_service']['status']=='unavailable' else None))
        self.assertTrue(outage['busy']);self.assertEqual(outage['active_job'],active['id'])
        self.assertEqual(outage['queue_storage']['status'],'ready')
        self.assertIn('make queue',outage['queue_service']['message'])
        self.assertEqual(outage['queue_service']['url'],f'http://127.0.0.1:{self.queue_port}')
        body={'text':'Accepted during outage.','description':'A calm baritone.','idempotency_key':'outage-safe'}
        code,pending=request(self.gen_port,'/api/generate','POST',body)
        self.assertEqual(code,202,pending)
        self.assertEqual(request(self.gen_port,'/api/generate','POST',body)[1]['id'],pending['id'])
        removed=request(self.gen_port,'/api/generate','POST',{**body,'text':'Remove while offline.','idempotency_key':'remove-offline'})[1]
        self.assertEqual(request(self.gen_port,'/api/queue/'+removed['id'],'DELETE')[1]['deleted'],1)
        self.assertEqual(request(self.gen_port,'/api/jobs/'+active['id'])[1]['status'],'running')
        # Health requests diagnose only; they must not silently restart the API.
        self.assertEqual(int((self.root/f'queue-{self.queue_port}.pid').read_text()),pid)
        (self.media/'release').touch()
        completed=wait_for(lambda:(row if (row:=request(self.gen_port,'/api/jobs/'+pending['id'])[1])['status']=='done' else None))
        self.assertEqual(completed['attempts'],1)
        self.start_queue()
        restored=wait_for(lambda:(state if (state:=request(self.gen_port)[1])['queue_service']['status']=='ready' else None))
        self.assertIsNone(restored['queue_service']['message'])
        self.assertEqual(self.row(active['id'])['attempts'],1)
        self.assertEqual(self.row(pending['id'])['status'],'done')
        self.assertEqual(self.row(removed['id'])['status'],'deleted')
        self.assertEqual([json.loads(line)['text'] for line in (self.media/'calls.jsonl').read_text().splitlines()],
                         ['Held.','Accepted during outage.'])
        self.assertIsNone(generator.poll())
    def test_bad_payload_fails_in_generator_without_blocking_following_work(self):
        self.start_queue()
        status,bad=request(self.queue_port,'/api/queue','POST',{'model':'voxcpm2','payload':{'nonsense':True}})
        self.assertEqual(status,202)
        other=request(self.queue_port,'/api/queue','POST',{'model':'unknown-routing','payload':{}})[1]
        good=self.submit()
        self.start_generator()
        self.assertEqual(self.done(bad['id'])['status'],'error')
        result=self.done(good['id']);self.assertEqual(result['status'],'done',result)
        self.assertTrue(result['prepared']);self.assertEqual(result['payload']['text'],'Hello.')
        self.assertEqual(self.row(other['id'])['status'],'queued')
    def test_unrelated_listener_is_never_replaced(self):
        listener=self.spawn('-m','http.server',str(self.queue_port),'--bind','127.0.0.1')
        def ready():
            with urllib.request.urlopen(f'http://127.0.0.1:{self.queue_port}/') as response:
                return response.status==200
        wait_for(ready)
        result=subprocess.run([sys.executable,'queue_manage.py','start','--port',str(self.queue_port)],
                              cwd=PROJECT,env=self.env,capture_output=True,text=True,timeout=15)
        self.assertNotEqual(result.returncode,0);self.assertIn('occupied',result.stderr)
        self.assertIsNone(listener.poll())
        self.assertFalse((self.root/f'queue-{self.queue_port}.pid').exists())

    def test_generator_crash_recovers_under_exclusive_owner_with_same_id(self):
        self.start_queue();generator=self.start_generator()
        active=self.submit('Held.');pending=self.submit('Following.')
        wait_for(lambda:(self.media/'calls.jsonl').exists())
        generator.kill();generator.wait(timeout=5)
        # CRUD never changes a live/interrupted running status on its own.
        self.assertEqual((self.row(active['id'])['status'],self.row(active['id'])['attempts']),('running',1))
        (self.media/'release').touch();self.start_generator()
        result=self.done(active['id']);self.assertEqual((result['status'],result['attempts']),('done',2),result)
        self.assertEqual(self.done(pending['id'])['status'],'done')
        calls=[json.loads(line) for line in (self.media/'calls.jsonl').read_text().splitlines()]
        self.assertEqual([row['id'] for row in calls],[active['id'],active['id'],pending['id']])


class QueueFailureTests(unittest.TestCase):
    def test_health_probe_handles_wrong_identity_malformed_json_and_timeout(self):
        import queue_manage
        import queue_status
        database=PROJECT/'state/example.sqlite3'
        healthy={'service':'voicegen-queue','role':'queue-crud','project':str(PROJECT),
                 'queue_database':str(database),'status':'ready'}
        with patch('queue_status.health',return_value=healthy) as probe:
            self.assertEqual(queue_status.api_status(12345,database)['status'],'ready')
            probe.assert_called_once_with(12345,timeout=.3)
        for body in ([1],{'unexpected':True},{**healthy,'queue_database':'/another/project.sqlite3'},
                     {**healthy,'project':'/another/project'},{**healthy,'role':'another-service'}):
            with patch('queue_status.health',return_value=body):
                result=queue_status.api_status(12345,database)
                self.assertEqual(result['status'],'mismatch');self.assertIn('Inspect',result['message'])
        self.assertFalse(queue_manage.matching([1]))
        for error in (TimeoutError('slow listener'),ConnectionResetError('disconnected'),ValueError('malformed JSON')):
            with patch('queue_manage.urllib.request.urlopen',side_effect=error):
                self.assertEqual(queue_status.api_status(12345,database)['status'],'unavailable')

    def test_queue_crud_sqlite_failure_returns_retryable_json_without_raw_error(self):
        import sqlite3
        import queue_server
        from fastapi.testclient import TestClient
        client=TestClient(queue_server.app)
        cases=[('admit_opaque','POST','/api/queue',{'payload':{}}),('listing','GET','/api/queue',None),
               ('read','GET','/api/queue/example',None),('edit_pending','PATCH','/api/queue/example',{'payload':{}}),
               ('remove','DELETE','/api/queue/example',None),('remove','DELETE','/api/queue',None)]
        for method,verb,path,body in cases:
            with self.subTest(method=method),patch.object(queue_server.store,method,side_effect=sqlite3.OperationalError('private raw database error')):
                response=client.request(verb,path,json=body)
                self.assertEqual(response.status_code,503,response.text)
                self.assertEqual(response.headers['Retry-After'],'2')
                self.assertEqual(response.json()['code'],'queue_storage_unavailable')
                self.assertNotIn('private raw',response.text)

    def test_generator_storage_errors_are_503_and_health_remains_available(self):
        import sqlite3
        from .test_contract import ContractTests
        fixture=ContractTests();fixture.setUp()
        try:
            with patch.object(fixture.manager,'existing',side_effect=sqlite3.OperationalError('locked')):
                response=fixture.generate(idempotency_key='storage-retry')
                self.assertEqual(response.status_code,503,response.text)
                self.assertEqual(response.json()['code'],'queue_storage_unavailable')
                self.assertEqual(response.headers['Retry-After'],'2')
            with patch.object(fixture.manager.store,'listing',side_effect=sqlite3.DatabaseError('not available')):
                response=fixture.client.get('/health')
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['queue_storage']['status'],'unavailable')
                self.assertTrue(response.json()['downloaded'])
            self.assertEqual(fixture.client.get('/health').json()['queue_storage']['status'],'ready')
            first=fixture.generate(idempotency_key='storage-retry').json()
            again=fixture.generate(idempotency_key='storage-retry').json()
            self.assertEqual(first['id'],again['id'])
            self.assertEqual(fixture.finish(first)['status'],'done')
            self.assertEqual(len(fixture.engine.calls),1)
        finally:fixture.tearDown()

    def test_health_does_not_wait_for_full_queue_timeout_when_sqlite_is_locked(self):
        import sqlite3
        from .test_contract import ContractTests
        fixture=ContractTests();fixture.setUp()
        try:
            with sqlite3.connect(fixture.manager.store.path) as db:
                db.execute('BEGIN IMMEDIATE')
                started=time.monotonic();response=fixture.client.get('/health')
                self.assertLess(time.monotonic()-started,1)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['queue_storage']['status'],'unavailable')
                db.rollback()
            self.assertEqual(fixture.client.get('/health').json()['queue_storage']['status'],'ready')
        finally:fixture.tearDown()


if __name__=='__main__':
    if '--fake-generator' in sys.argv:fake_generator()
    else:unittest.main()
