"""Resource history, truthful scopes, isolation, and fake-job instrumentation."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from config import PROJECT_ROOT
from durable_queue import QueueStore
from observability import MetricsStore, ResourceMonitor, METRICS, WINDOWS
from resource_probe import ResourceProbe, cpu_seconds, gpu_counters, process_tree


class FakeProbe:
    def sample(self):
        return dict(zip(METRICS,(140.,2**30,72.,3*2**30,2**30))),[]


class ProbeTests(unittest.TestCase):
    def test_real_child_cpu_and_resident_memory_are_included(self):
        child=subprocess.Popen([sys.executable,'-c',
            'import time\ndata=bytearray(b"x"*(32*1024**2))\nprint("ready",flush=True)\n'
            'end=time.monotonic()+5\nwhile time.monotonic()<end:pass\n'],stdout=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline(),b'ready\n')
            probe=ResourceProbe()
            probe.sample()
            time.sleep(.3)
            sample,_=probe.sample()
            self.assertGreater(sample['cpu_percent'],0)
            self.assertGreater(sample['rss_bytes'],32*1024**2)
            raw=subprocess.check_output(['/bin/ps','-axo','pid=,ppid=,time=,rss=,lstart='])
            self.assertIn(child.pid,process_tree(raw.decode(),os.getpid()))
        finally:
            child.terminate();child.wait(timeout=5);child.stdout.close()

    def test_cpu_clock_formats_and_descendants_exclude_unrelated_processes(self):
        self.assertEqual(cpu_seconds('1-02:03:04.50'),93784.5)
        self.assertEqual(cpu_seconds('02:04.50'),124.5)
        rows=process_tree('1 0 0:00.01 50 Mon Jan 1\n2 1 0:00.30 100 Mon Jan 1\n'
                          '3 2 0:00.20 200 Mon Jan 1\n8 7 2:00.00 900 Mon Jan 1\ninvalid',1)
        self.assertEqual(set(rows),{1,2,3})
        self.assertEqual(sum(row[2] for row in rows.values()),350*1024)

    def test_interval_cpu_new_children_pid_reuse_and_subquarter_second_debounce(self):
        samples=iter([b'1 0 00:01.00 100 original\n',b'1 0 00:01.01 100 original\n',
            b'1 0 00:01.20 100 original\n2 1 00:00.30 200 child\n',
            b'1 0 00:01.40 100 original\n2 1 00:00.10 200 different-start\n'])
        times=iter([10,10.01,11,12])
        probe=ResourceProbe(pid=1,runner=lambda *a,**kw:next(samples),clock=lambda:next(times))
        with patch('resource_probe.sys.platform','linux'):
            self.assertIsNone(probe.sample()[0]['cpu_percent'])
            self.assertIsNone(probe.sample()[0]['cpu_percent'])
            sample,_=probe.sample();self.assertAlmostEqual(sample['cpu_percent'],50)
            self.assertEqual(sample['rss_bytes'],300*1024)
            self.assertAlmostEqual(probe.sample()[0]['cpu_percent'],30)

    def test_gpu_plist_missing_invalid_and_zero_are_distinct(self):
        stats={'Device Utilization %':0,'Alloc system memory':300,'In use system memory':150}
        value=gpu_counters(plistlib.dumps([{'PerformanceStatistics':stats}]))
        self.assertEqual(value,dict(gpu_percent=0,gpu_allocated_bytes=300,gpu_in_use_bytes=150))
        self.assertIsNone(gpu_counters(plistlib.dumps([]))['gpu_percent'])
        stats['Device Utilization %']=101
        self.assertIsNone(gpu_counters(plistlib.dumps([{'PerformanceStatistics':stats}]))['gpu_percent'])

    def test_gpu_failure_does_not_discard_cpu_or_memory(self):
        def run(command,**kwargs):
            if command[0]=='/bin/ps':return b'1 0 00:01.00 100 original\n'
            raise subprocess.TimeoutExpired(command,.75)
        with patch('resource_probe.sys.platform','darwin'):
            sample,errors=ResourceProbe(pid=1,runner=run).sample()
        self.assertEqual(sample['rss_bytes'],100*1024)
        self.assertIsNone(sample['gpu_percent'])
        self.assertIn('GPU counters unavailable',errors[0])


class ResourceHistoryTests(unittest.TestCase):
    def setUp(self):
        parent=PROJECT_ROOT/'state/validation';parent.mkdir(parents=True,exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(prefix='resources-',dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.queue=QueueStore(self.root,self.root/'queue.sqlite3');self.queue.initialize()
        self.store=MetricsStore(self.queue.path);self.store.initialize()

    def sample(self,ts,**values):
        sample=dict(ts=ts,session_id='session',run_id=None,job_id=None,backend='higgs',model='higgs',
                    stage='ready',**dict(zip(METRICS,(50,100,40,200,100))))
        self.store.insert({**sample,**values})

    def test_shared_database_preserves_queue_and_survives_reopen(self):
        row=self.queue.admit_opaque('higgs',{'fixture':True})
        self.sample(100)
        self.store.initialize()
        self.assertEqual(self.queue.read(row['id'])['payload']['queue_request'],{'fixture':True})
        self.assertEqual(MetricsStore(self.queue.path).series(0,200)['summary']['samples'],1)

    def test_windows_aggregation_preserves_peaks_model_filter_and_bounded_points(self):
        self.assertEqual(list(WINDOWS),['5m','30m','1h','6h','24h','3d','7d'])
        for index in range(1,21):self.sample(index,cpu_percent=index*10)
        self.sample(15,backend='voxcpm2',model='base',session_id='second')
        data=self.store.series(0,7200,backend='higgs')
        self.assertEqual(data['summary']['samples'],20)
        self.assertEqual(data['summary']['peak_cpu_percent'],200)
        self.assertLessEqual(len(data['points']),2)
        self.assertTrue(any(p['peak_cpu_percent']>p['cpu_percent'] for p in data['points']))
        self.assertEqual(data['models'],[{'backend':'higgs','model':'higgs'}])
        self.assertEqual(self.store.series(0,7200,backend="higgs' OR 1=1")['summary']['samples'],0)

    def test_gaps_and_null_counters_never_become_zero(self):
        self.sample(1,gpu_percent=None)
        self.sample(50,session_id='new-session',gpu_percent=None)
        data=self.store.series(0,100)
        self.assertEqual(len(data['points']),2)
        self.assertIsNone(data['summary']['peak_gpu_percent'])
        self.assertTrue(all(p['gpu_percent'] is None for p in data['points']))

    def test_retention_prunes_only_resource_tables(self):
        row=self.queue.admit_opaque('higgs',{})
        self.sample(1);self.sample(700000)
        self.store.prune(700000)
        self.assertEqual(self.store.series(0,800000)['summary']['samples'],1)
        self.assertEqual(self.queue.read(row['id'])['status'],'queued')

    def monitor(self):
        monitor=ResourceMonitor(self.queue.path,'higgs','higgs',lambda:'generating',probe=FakeProbe())
        monitor._initialize()
        return monitor

    def test_subsecond_jobs_and_retries_have_separate_attempts_and_samples(self):
        monitor=self.monitor();job=dict(id='a'*32,model='higgs',attempts=1)
        for attempt in (1,2):
            monitor.job_started({**job,'attempts':attempt});monitor.job_finished('done' if attempt==2 else 'error')
        page=self.store.jobs(job_id=job['id'])
        self.assertEqual(page['total'],2)
        self.assertEqual({r['status'] for r in page['data']},{'done','error'})
        for run in page['data']:
            self.assertEqual(run['samples'],2)
            data=self.store.series(run['started_at'],run['ended_at'],job_id=job['id'],run_id=run['id'])
            self.assertEqual(data['summary']['samples'],2)
            self.assertTrue(all(p['run_id']==run['id'] for p in data['points']))

    def test_unclean_restart_marks_monitor_attempt_interrupted_without_requeue(self):
        monitor=self.monitor();monitor.job_started(dict(id='b'*32,model='higgs',attempts=1))
        monitor2=ResourceMonitor(self.queue.path,'voxcpm2','base',lambda:'ready',probe=FakeProbe())
        monitor2._initialize()
        run=self.store.jobs()['data'][0]
        self.assertEqual(run['status'],'interrupted')
        self.assertIsNotNone(run['ended_at'])
        self.assertEqual(self.queue.listing()['total'],0)
        monitor2._capture()
        self.assertEqual({r['backend'] for r in self.store.series(0,time.time()+1)['points']},{'higgs','voxcpm2'})

    def test_storage_failure_is_bounded_visible_and_recovers(self):
        monitor=self.monitor()
        with self.assertLogs(level='WARNING'),patch.object(monitor.store,'insert',side_effect=sqlite3.OperationalError('locked')):
            monitor._capture()
        self.assertEqual(monitor.status()['dropped_samples'],1)
        self.assertIn('generation can continue',monitor.status()['storage_error'])
        monitor._capture()
        self.assertIsNone(monitor.status()['storage_error'])

    def test_expansion_records_actual_llm_while_retaining_selected_speech_backend(self):
        monitor=self.monitor();monitor.state=lambda:'expanding';monitor._capture()
        row=self.store.series(0,time.time()+1)['points'][0]
        self.assertEqual(row['model'],'mlx-community/Qwen3.5-2B-4bit')
        self.assertEqual(row['backend'],'higgs')
        self.assertEqual(row['stage'],'expanding')

    def test_collector_lifetime_closes_thread(self):
        monitor=ResourceMonitor(self.queue.path,'higgs','higgs',lambda:'ready',probe=FakeProbe())
        monitor.start();time.sleep(.03);monitor.close()
        self.assertFalse(monitor.status()['running'])
        self.assertGreaterEqual(self.store.series(0,time.time()+1)['summary']['samples'],1)

    def test_failed_job_summary_write_is_retried_without_leaving_it_running(self):
        monitor=self.monitor();monitor.job_started(dict(id='e'*32,model='higgs',attempts=1))
        with self.assertLogs(level='WARNING'),patch.object(monitor.store,'finish_run',side_effect=sqlite3.OperationalError('locked')):
            monitor.job_finished('done')
        self.assertIsNone(monitor.context)
        self.assertEqual(len(monitor.pending_finishes),1)
        self.assertEqual(self.store.jobs()['data'][0]['status'],'running')
        monitor._capture()
        self.assertEqual(self.store.jobs()['data'][0]['status'],'done')
        self.assertFalse(monitor.pending_finishes)
        self.assertIsNone(monitor.last_error)

    def test_real_sqlite_contention_drops_sample_without_stalling_and_then_recovers(self):
        monitor=self.monitor()
        with sqlite3.connect(self.queue.path) as blocker:
            blocker.execute('BEGIN EXCLUSIVE')
            started=time.monotonic()
            with self.assertLogs(level='WARNING'):monitor._capture()
            self.assertLess(time.monotonic()-started,1)
            self.assertEqual(monitor.dropped_samples,1)
            blocker.rollback()
        monitor._capture()
        self.assertIsNone(monitor.last_error)

    def test_week_of_samples_returns_aggregates_with_original_peaks(self):
        with self.store.connect() as db:
            db.executemany('INSERT INTO resource_samples(ts,session_id,backend,model,stage,cpu_percent,rss_bytes) VALUES(?,?,?,?,?,?,?)',
                ((index*60,'session','higgs','higgs','generating',300 if index==99 else 30,2**30) for index in range(10000)))
        data=self.store.series(0,WINDOWS['7d'])
        self.assertEqual(data['summary']['samples'],10000)
        self.assertEqual(data['summary']['peak_cpu_percent'],300)
        self.assertLessEqual(len(data['points']),360)

    def test_api_windows_pagination_job_attempts_and_no_history(self):
        import server
        monitor=self.monitor();monitor.job_started(dict(id='c'*32,model='higgs',attempts=1));monitor.job_finished('done')
        fake_manager=Mock();fake_manager.resources=monitor
        with patch('server.manager',fake_manager):
            client=TestClient(server.app)
            for window in WINDOWS:
                response=client.get('/api/observability',params={'window':window})
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn('Device-wide',response.json()['scopes']['gpu_percent'])
                self.assertGreater(response.json()['summary']['samples'],0)
            self.assertEqual(client.get('/api/observability?window=2years').status_code,422)
            self.assertEqual(client.get('/api/observability/jobs?limit=101').status_code,422)
            self.assertEqual(client.get('/api/observability/jobs?offset=-1').status_code,422)
            self.assertEqual(client.get('/api/observability/jobs?limit=1').json()['total'],1)
            job=client.get('/api/observability/jobs/'+'c'*32).json()
            self.assertEqual(job['selected_run']['attempt'],1)
            self.assertEqual(client.get('/api/observability/jobs/'+'c'*32+'?run_id='+'d'*32).status_code,404)
            self.assertEqual(client.get('/api/observability/jobs/'+'d'*32).status_code,404)
            self.assertEqual(client.get('/api/observability/jobs/invalid').status_code,404)
            self.assertEqual(client.get('/observability').status_code,200)
            self.assertEqual(client.get('/assets/observability.js').status_code,200)
            self.assertEqual(client.get('/assets/observability.css').status_code,200)
            schema=client.get('/openapi.json').json()
            self.assertIn('/api/observability/jobs/{identity}',schema['paths'])
            self.assertIn('ResourceOverview',schema['components']['schemas'])
            self.assertIn('gpu_percent',schema['components']['schemas']['ResourcePoint']['properties'])

    def test_job_manager_monitor_failure_cannot_break_generation(self):
        from jobs import JobManager
        manager=JobManager(Mock(),self.root,self.root/'gpu.lock',db_path=self.queue.path)
        manager.resources=Mock();manager.resources.job_started.side_effect=RuntimeError('probe failed')
        with self.assertLogs(level='WARNING'):manager.observe('job_started',{'id':'d'*32})
        manager.executor.shutdown()


if __name__=='__main__':unittest.main()
