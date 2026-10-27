"""Sparse model timing, durable attribution, safe native instrumentation and API bounds."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from config import PROJECT_ROOT, model_context
from durable_queue import QueueStore
from lifecycle_events import measure, observe, reason, event_pipe
from lifecycle_store import LIFECYCLE_RETENTION
from native_instrumentation import prepare_native
from observability import MetricsStore, ResourceMonitor
from tests.test_observability import FakeProbe


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        parent=PROJECT_ROOT/'state/validation';parent.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=parent,prefix='lifecycle-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.queue=QueueStore(self.root,self.root/'queue.sqlite3');self.queue.initialize()
        self.store=MetricsStore(self.queue.path);self.store.initialize()
        self.monitor=ResourceMonitor(self.queue.path,'higgs','higgs',lambda:'ready',probe=FakeProbe())
        self.monitor._initialize()

    def event(self,**changes):
        return dict(id=uuid4().hex,session_id=self.monitor.session_id,run_id=None,job_id=None,attempt=None,
                    backend='higgs',model='fixture-model',action='load',scope='native_weights',reason='per_job',
                    started_at=time.time(),ended_at=time.time()+2,duration_seconds=2.,status='success',**changes)

    def test_monotonic_duration_survives_wall_clock_change_and_preserves_error(self):
        events=[]
        with observe(events.append),patch('lifecycle_events.time.time',side_effect=[1000,900]),patch('lifecycle_events.time.monotonic',side_effect=[20,23]):
            with self.assertRaisesRegex(RuntimeError,'model failed'):
                with measure('load','weights','voxcpm2','fixture'):raise RuntimeError('model failed')
        self.assertEqual(events[0]['status'],'running');self.assertIsNone(events[0]['duration_seconds'])
        self.assertEqual(events[1]['status'],'error');self.assertEqual(events[1]['duration_seconds'],3)
        self.assertLess(events[1]['ended_at'],events[1]['started_at'])
        self.assertNotIn('error',events[1])  # No exception text, prompts or paths in telemetry.
        with self.assertLogs(level='WARNING'),observe(Mock(side_effect=sqlite3.OperationalError('locked'))):
            with measure('unload','weights','voxcpm2','fixture'):pass

    def test_migration_reopen_retention_preserve_queue_and_longer_event_history(self):
        queued=self.queue.admit_opaque('higgs',{'fixture':True})
        for days in (1,30,89,91):
            row=self.event();row['started_at']=time.time()-days*86400;self.store.lifecycle_event(row)
        self.store.initialize();self.store.prune(time.time())
        self.assertEqual(MetricsStore(self.queue.path).lifecycle()['total'],3)
        self.assertEqual(self.queue.read(queued['id'])['status'],'queued')
        self.assertEqual(LIFECYCLE_RETENTION,90*86400)

    def test_start_observer_wait_is_not_charged_to_model_duration(self):
        clock=[0];events=[]
        def report(event):
            events.append(event)
            if event['status']=='running':clock[0]+=10
        with observe(report),patch('lifecycle_events.time.monotonic',side_effect=lambda:clock[0]):
            with measure('load','weights','voxcpm2','fixture'):clock[0]+=4
        self.assertEqual(events[-1]['duration_seconds'],4)

    def test_filter_pagination_and_summaries_use_all_matches_excluding_failure(self):
        for index in range(37):
            row=self.event();row.update(duration_seconds=index+1,started_at=100+index);self.store.lifecycle_event(row)
        row=self.event();row.update(status='error',duration_seconds=900,started_at=200);self.store.lifecycle_event(row)
        other=self.event();other['backend']='voxcpm2';self.store.lifecycle_event(other)
        page=self.store.lifecycle(100,200,backend='higgs',limit=5,offset=5)
        self.assertEqual(page['total'],38);self.assertEqual(len(page['data']),5);self.assertTrue(page['has_more'])
        self.assertEqual(page['counts'],{'error':1,'success':37})
        self.assertEqual(page['summaries'][0]['count'],37);self.assertEqual(page['summaries'][0]['mean_seconds'],19)
        self.assertEqual(page['summaries'][0]['max_seconds'],37)
        self.assertEqual(self.store.lifecycle(backend="higgs' OR 1=1")['total'],0)
        self.assertEqual(self.store.lifecycle(action='unload')['total'],0)

    def test_incoming_attempt_keeps_old_model_release_and_unknown_crash_duration(self):
        job=dict(id='a'*32,backend='higgs',model='higgs',attempts=2)
        self.monitor.job_started(job)
        with observe(self.monitor.lifecycle_event),reason('model_switch'):
            with measure('unload','runtime_process','voxcpm2','old-model'):pass
        pending=self.event();pending.update(status='running',ended_at=None,duration_seconds=None)
        self.monitor.lifecycle_event(pending)
        self.monitor.job_finished('error')
        page=self.store.lifecycle(job_id=job['id'])
        self.assertEqual(page['total'],2)
        release=next(r for r in page['data'] if r['action']=='unload')
        self.assertEqual(release['backend'],'voxcpm2');self.assertEqual(release['attempt'],2)
        self.assertEqual(release['reason'],'model_switch')
        crashed=next(r for r in page['data'] if r['action']=='load')
        self.assertEqual(crashed['status'],'interrupted');self.assertIsNone(crashed['duration_seconds']);self.assertIsNone(crashed['ended_at'])
        self.assertEqual(self.queue.listing()['total'],0)

    def test_restart_marks_only_unfinished_timings_without_measuring_downtime(self):
        row=self.event();row.update(status='running',ended_at=None,duration_seconds=None);self.store.lifecycle_event(row)
        finished=self.event();self.store.lifecycle_event(finished)
        self.store.recover(time.time()+100000)
        events={r['id']:r for r in self.store.lifecycle()['data']}
        self.assertEqual(events[row['id']]['status'],'interrupted');self.assertIsNone(events[row['id']]['duration_seconds'])
        self.assertEqual(events[finished['id']]['status'],'success')

    def test_storage_outage_buffers_boundaries_then_recovers_without_duplicates(self):
        with patch.object(self.monitor.store,'lifecycle_event',side_effect=sqlite3.OperationalError('locked')):
            with observe(self.monitor.lifecycle_event):
                with measure('load','weights','voxcpm2','fixture'):pass
        self.assertEqual(len(self.monitor.pending_events),2)
        self.assertIn('generation can continue',self.monitor.status()['lifecycle_error'])
        self.monitor._capture()
        self.assertEqual(self.store.lifecycle()['total'],1);self.assertEqual(self.store.lifecycle()['data'][0]['status'],'success')
        self.assertIsNone(self.monitor.status()['lifecycle_error'])
        self.assertFalse(self.monitor.pending_events)

    def test_api_bounds_and_job_filters_are_typed_and_queue_remains_independent(self):
        import server,queue_server
        row=self.event();row.update(job_id='b'*32,run_id='c'*32,attempt=1);self.store.lifecycle_event(row)
        with patch('server.manager',Mock(resources=self.monitor)):
            client=TestClient(server.app)
            result=client.get('/api/observability/models',params={'job_id':'b'*32,'run_id':'c'*32,'window':'all'}).json()
            self.assertEqual(result['total'],1);self.assertEqual(result['retention_seconds'],LIFECYCLE_RETENTION)
            self.assertEqual(result['summaries'][0]['mean_seconds'],2)
            for params in ({'window':'1year'},{'model':'unknown'},{'action':'delete'},{'scope':'unknown'},{'job_id':'bad'},{'run_id':'bad'},{'limit':101},{'offset':-1}):
                response=client.get('/api/observability/models',params=params);self.assertEqual(response.status_code,422,response.text)
            self.assertEqual(client.get('/api/observability/models?model=voxcpm2').json()['total'],0)
            self.assertIn('ModelLifecyclePage',client.get('/openapi.json').json()['components']['schemas'])
        self.assertNotIn('/api/observability/models',TestClient(queue_server.app).get('/openapi.json').json()['paths'])

    def test_python_adapter_warm_reuse_and_variant_change_are_separate_operations(self):
        from engine import Engine
        specs={v:dict(repo='fixture/'+v,subdir=v) for v in ('one','two')}
        adapter=Engine(loader=lambda _:Mock(tokenizer=Mock()))
        def release():adapter.model=None;adapter.variant=None
        events=[]
        with patch('engine.MODEL_SPECS',specs),patch('engine.PROFILE',{'backend':'voxcpm2','id':'voxcpm2'}),patch('engine.verify_assets',return_value=[]),patch('clean_env.clean_appledouble'),patch.object(adapter,'_release',side_effect=release),observe(events.append):
            adapter.load('one');adapter.load('one');adapter.load('two');adapter.unload();adapter.unload()
        finished=[r for r in events if r['status']=='success']
        self.assertEqual([(r['action'],r['model']) for r in finished],[('load','fixture/one'),('unload','fixture/one'),('load','fixture/two'),('unload','fixture/two')])
        self.assertEqual(finished[1]['reason'],'variant_change');self.assertEqual(finished[2]['reason'],'variant_change')

    def test_native_instrumentation_safe_rerun_and_unrelated_change_rejection(self):
        source=self.root/'native';cli=source/'app/cli';cli.mkdir(parents=True)
        original='#include "args.h"\nminitts::app::print_build_info(std::cout);\n        auto model = registry.load(load_request);\n        auto session = model->create_task_session(task_spec, session_options);\n'
        (cli/'main.cpp').write_text(original)
        subprocess.run(['git','init','-q',str(source)],check=True)
        subprocess.run(['git','-C',str(source),'add','.'],check=True)
        subprocess.run(['git','-C',str(source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],check=True)
        prepare_native(source,PROJECT_ROOT);prepare_native(source,PROJECT_ROOT)
        changed=(cli/'main.cpp').read_text();self.assertIn('VoiceGenRelease',changed)
        (cli/'main.cpp').write_text(changed+'// user change\n')
        with self.assertRaisesRegex(RuntimeError,'unrelated local changes'):prepare_native(source,PROJECT_ROOT)
        self.assertTrue((cli/'main.cpp').read_text().endswith('// user change\n'))

    def test_real_backend_worker_ipc_reports_warm_reuse_switch_and_shutdown(self):
        from runtime_engine import RuntimeEngine
        from model_readiness import ReadyModels
        from schemas import Generation
        script=self.root/'worker.py'
        script.write_text('import sys;sys.path.insert(0,'+repr(str(PROJECT_ROOT))+')\n'+'''
import os,types
from lifecycle_events import measure
backend=os.environ['MODEL_BACKEND']
class FakeEngine:
 def __init__(self):self.model=None;self.variant=None
 def generate(self,*args,**kwargs):
  if self.model is None:
   with measure('load','weights',backend,'fixture/'+backend):self.model=True;self.variant=backend
  return {'fixture':True}
 def unload(self):
  if self.model is not None:
   with measure('unload','weights',backend,'fixture/'+backend):self.model=None;self.variant=None
sys.modules['engine']=types.SimpleNamespace(create_engine=FakeEngine)
import backend_worker
backend_worker.main()
''')
        catalog=ReadyModels(probe=lambda *args:[]);catalog.python=lambda model:Path(sys.executable)
        engine=RuntimeEngine(catalog,script);events=[]
        with (self.root/'owner').open('a+') as owner,observe(events.append):
            engine.consumer_owner=owner
            try:
                for backend in ('voxcpm2','voxcpm2','qwen3'):
                    with model_context(backend):
                        self.assertTrue(engine.generate(Generation(text='Hello.',description='A fixture.'),None,self.root/'unused.wav')['fixture'])
                with reason('shutdown'):engine.unload()
            finally:engine.unload()
        complete=[row for row in events if row['status']=='success']
        weights=[row for row in complete if row['scope']=='weights']
        self.assertEqual([(row['backend'],row['action']) for row in weights],[('voxcpm2','load'),('voxcpm2','unload'),('qwen3','load'),('qwen3','unload')])
        self.assertEqual(weights[1]['reason'],'model_switch');self.assertEqual(weights[-1]['reason'],'shutdown')
        processes=[row for row in complete if row['scope']=='runtime_process']
        self.assertEqual(len(processes),4)
        self.assertIsNone(engine.process)

    def test_llm_boundaries_include_release_after_success_or_generation_failure(self):
        import prompt_worker
        mx=SimpleNamespace(clear_cache=Mock())
        fake_mlx=SimpleNamespace(core=mx)
        for fails in (False,True):
            events=[]
            with patch.dict(sys.modules,{'mlx_lm':SimpleNamespace(load=lambda path:(object(),object())),'mlx':fake_mlx,'mlx.core':mx}),patch('prompt_worker.generate_loaded',side_effect=RuntimeError('fixture') if fails else None,return_value={'completion':'fixture'}),observe(events.append):
                if fails:
                    with self.assertRaisesRegex(RuntimeError,'fixture'):prompt_worker.run({'weights':'fixture','backend':'higgs'})
                else:self.assertEqual(prompt_worker.run({'weights':'fixture','backend':'higgs'}),{'completion':'fixture'})
            complete=[e for e in events if e['status']=='success']
            self.assertEqual([e['action'] for e in complete],['load','unload'])
            self.assertTrue(all(e['scope']=='llm_weights' and e['backend']=='higgs' for e in complete))

    @unittest.skipUnless(shutil.which('c++'),'C++ compiler not installed')
    def test_native_boundary_pipe_measures_load_release_and_errors_without_logs(self):
        program=self.root/'fixture.cpp';binary=self.root/'fixture'
        program.write_text('#include "native_lifecycle.h"\n#include <memory>\n#include <thread>\n'
            'struct Model { ~Model(){std::this_thread::sleep_for(std::chrono::milliseconds(8));} };\n'
            'int main(){ { VoiceGenLifecycle event("load","native_weights");auto model=std::make_unique<Model>();auto session=std::make_unique<Model>();event.finish();VoiceGenRelease release(model,session); }'
            'try{VoiceGenLifecycle event("load","native_weights");throw 1;}catch(...){}return 0;}')
        subprocess.run(['c++','-std=c++17','-I',str(PROJECT_ROOT),str(program),'-o',str(binary)],check=True,capture_output=True)
        events=[]
        with observe(events.append),event_pipe('higgs','fixture-model') as fd:
            response=subprocess.run([str(binary)],env={**os.environ,'VOICEGEN_LIFECYCLE_FD':str(fd)},pass_fds=(fd,),capture_output=True)
        self.assertEqual(response.returncode,0);self.assertFalse(response.stdout);self.assertFalse(response.stderr)
        completed=[e for e in events if e['status']!='running']
        self.assertEqual([e['status'] for e in completed],['success','success','error'])
        self.assertGreater(completed[1]['duration_seconds'],.01)
        self.assertEqual({e['backend'] for e in events},{'higgs'})
        self.assertEqual(len({e['id'] for e in events}),3)
        for event in completed:self.assertTrue(any(e['id']==event['id'] and e['status']=='running' for e in events))


if __name__=='__main__':unittest.main()
