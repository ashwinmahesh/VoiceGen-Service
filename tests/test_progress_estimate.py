"""Estimated progress: history fitting, clamping, batches and agreement between both APIs. Fake inference only."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3
import threading
import unittest
from unittest.mock import patch
import progress_estimate
from progress_estimate import samples, expected_seconds, started, take_progress, batch_progress
from durable_queue import QueueStore
from schemas import Job, CandidateBatch
from .test_queue import QueueFixture, wait_for

START = datetime(2026,10,30,12,0,tzinfo=timezone.utc)


def at(seconds):
    return START+timedelta(seconds=seconds)


def saved(expected=40.):
    return started(100,[(100,expected)]*3,(),'voxcpm2',now=START)


class EstimatorTests(unittest.TestCase):
    def test_samples_keep_newest_valid_takes_only(self):
        results=[{'text':'Hello there.','generation_seconds':3.5},{'text':'','generation_seconds':2},
                 {'text':'No timing.'},{'text':'Bad','generation_seconds':float('nan')},{'text':'Zero','generation_seconds':0},
                 {'text':'Flag','generation_seconds':True},None,*({'text':'x'*10,'generation_seconds':1} for _ in range(30))]
        pairs=samples(results)
        self.assertEqual(pairs[0],(12,3.5))
        self.assertEqual(len(pairs),progress_estimate.HISTORY_TAKES)

    def test_default_rate_before_any_history(self):
        self.assertEqual(expected_seconds(100,(),(),'higgs'),(23.,'default',0))
        self.assertEqual(expected_seconds(100,(),(),'unknown')[0],100*progress_estimate.FALLBACK_SECONDS_PER_CHARACTER)
        self.assertEqual(expected_seconds(1,(),(),'qwen3')[0],1.)  # Never a zero-length estimate.

    def test_one_or_two_takes_use_their_median_rate(self):
        self.assertEqual(expected_seconds(200,[(100,30.)],(),'voxcpm2'),(60.,'history',1))
        self.assertEqual(expected_seconds(200,[(100,30.),(100,30.)],(),'voxcpm2')[0],60.)

    def test_robust_line_separates_overhead_and_ignores_an_outlier(self):
        history=[(count,10+.2*count) for count in (50,100,150,200,250)]+[(120,400.)]
        seconds,basis,count=expected_seconds(300,history,(),'higgs')
        self.assertAlmostEqual(seconds,70.,places=6);self.assertEqual((basis,count),('history',6))
        # Same-length lines cannot fit a slope; fall back to the median rate.
        self.assertEqual(expected_seconds(100,[(100,20.)]*4,(),'higgs')[0],20.)

    def test_exact_variant_preferred_once_it_has_enough_takes(self):
        wide=[(100,50.)]*10
        self.assertEqual(expected_seconds(100,[(100,10.)]*3,wide,'qwen3')[0],10.)
        self.assertEqual(expected_seconds(100,[(100,10.)]*2,wide,'qwen3')[0],50.)

    def test_take_progress_states_and_clamping(self):
        record=saved(40.)
        self.assertIsNone(take_progress('queued',record,at(10)))
        self.assertIsNone(take_progress('error',record,at(10)))
        self.assertEqual(take_progress('done',None),{'percent':100,'phase':'done','estimated':False,'eta_seconds':0,'overdue':False,'basis':None})
        self.assertEqual(take_progress('running',None,at(10))['phase'],'preparing')
        self.assertEqual(take_progress('running',{'expected_seconds':5,'generating_since':'nonsense'},at(1))['percent'],0)
        half=take_progress('running',record,at(20))
        self.assertEqual(half,{'percent':50,'phase':'generating','estimated':True,'eta_seconds':20,'overdue':False,'basis':'history'})
        near=take_progress('running',record,at(39))
        self.assertEqual((near['percent'],near['overdue'],near['eta_seconds']),(95,False,1))
        late=take_progress('running',record,at(400))
        self.assertEqual((late['percent'],late['overdue'],late['eta_seconds']),(95,True,None))
        self.assertEqual(take_progress('running',record,at(-30))['percent'],0)  # Clock behind the start.

    def test_percent_never_decreases_while_running(self):
        record=saved(33.)
        values=[take_progress('running',record,at(tenth/10))['percent'] for tenth in range(0,800)]
        self.assertEqual(values,sorted(values))
        self.assertEqual(max(values),95)

    def test_batch_counts_finished_takes_plus_running_share(self):
        record=saved(40.)
        rows=[{'status':'done'},{'status':'running','progress':record},{'status':'queued'}]
        value=batch_progress(rows,at(20))
        self.assertEqual((value['percent'],value['phase'],value['eta_seconds']),(50,'generating',20+40))
        self.assertIsNone(batch_progress([{'status':'queued'}]*2))
        self.assertEqual(batch_progress([{'status':'done'},{'status':'queued'},{'status':'queued'}])['phase'],'waiting')
        self.assertEqual(batch_progress([{'status':'done'},{'status':'queued'},{'status':'queued'}])['percent'],33)
        self.assertEqual(batch_progress([{'status':'done'},{'status':'error'}])['percent'],100)
        self.assertIsNone(batch_progress([{'status':'error'},{'status':'deleted'}]))
        preparing=batch_progress([{'status':'running','progress':None},{'status':'queued'}])
        self.assertEqual((preparing['percent'],preparing['phase'],preparing['eta_seconds']),(0,'preparing',None))
        # Even an overdue final take leaves room below 100 until it is saved.
        self.assertLess(batch_progress([{'status':'done'},{'status':'running','progress':record}],at(999))['percent'],100)


class ManagerProgressTests(QueueFixture):
    def test_running_take_freezes_an_estimate_and_learns_from_finished_takes(self):
        manager=self.manager();manager.recover();engine=self.engines[-1]
        engine.gate=threading.Event()
        first=self.submit(manager,text='Welcome home, everyone.')
        self.assertIsNone(first['progress'])
        wait_for(lambda:manager.read(first['id'])['stage']=='Generating speech')
        running=manager.read(first['id'])['progress']
        self.assertEqual((running['phase'],running['estimated'],running['basis']),('generating',True,'default'))
        self.assertLessEqual(running['percent'],95)
        Job.model_validate(manager.read(first['id']))
        saved_row=manager.store.read(first['id'])['progress']
        self.assertEqual(saved_row['characters'],len('Welcome home, everyone.'))
        engine.gate.set()
        done=self.done(manager,first['id'])
        self.assertEqual(done['progress']['percent'],100)
        # FakeEngine reports 0.1 generation seconds for that line; the next estimate learns it.
        engine.gate=threading.Event()
        second=self.submit(manager,text='Welcome home, everyone.',seed=2)
        wait_for(lambda:manager.read(second['id'])['stage']=='Generating speech')
        record=manager.store.read(second['id'])['progress']
        self.assertEqual((record['basis'],record['samples'],record['expected_seconds']),('history',1,1.))
        engine.gate.set();self.done(manager,second['id'])

    def test_estimate_failure_never_fails_the_take(self):
        manager=self.manager()
        with patch.object(manager.store,'finished_results',side_effect=OSError('unreadable history')),self.assertLogs(level='WARNING') as logs:
            manager.recover()
            job=self.submit(manager)
            self.assertEqual(self.done(manager,job['id'])['status'],'done')
        self.assertIn('Progress estimate unavailable',logs.output[0])
        self.assertEqual(manager.read(job['id'])['progress']['percent'],100)

    def test_candidate_batch_reports_whole_batch_progress(self):
        from schemas import CandidateGeneration
        manager=self.manager();engine=self.engines[-1];engine.gate=threading.Event();manager.recover()
        request=CandidateGeneration(text='Welcome home.',description='A calm baritone.',candidate_count=2)
        batch=manager.submit_candidates(request,request,None,'',self.root/'outputs')
        wait_for(lambda:manager.read_candidates(batch['id'])['jobs'][0]['stage']=='Generating speech')
        live=manager.read_candidates(batch['id'])
        CandidateBatch.model_validate(live)
        self.assertEqual(live['progress']['phase'],'generating');self.assertLess(live['progress']['percent'],50)
        engine.gate.set()
        wait_for(lambda:manager.read_candidates(batch['id'])['status']=='done')
        self.assertEqual(manager.read_candidates(batch['id'])['progress']['percent'],100)

    def test_new_attempt_clears_the_previous_estimate(self):
        store=QueueStore(self.root,self.root/'queue.sqlite3');store.initialize()
        row=store.admit('voxcpm2','voxcpm2','design',False,{'destination':str(self.root/'outputs')},None,'f')
        store.claim('voxcpm2')
        store.change(row['id'],progress=saved(),status='error',error='failed')
        store.retry(row['id'])
        self.assertIsNone(store.claim('voxcpm2')['progress'])

    def test_progress_column_migrates_existing_databases(self):
        path=self.root/'old.sqlite3';store=QueueStore(self.root,path);store.initialize()
        row=store.admit('higgs','higgs','reference',False,{'destination':str(self.root/'outputs')},None,'f')
        with closing(sqlite3.connect(path)) as db:
            db.execute('ALTER TABLE entries DROP COLUMN progress');db.commit()
        store.initialize();store.initialize()
        self.assertIsNone(store.read(row['id'])['progress'])
        self.assertEqual(store.read(row['id'])['stage'],'Waiting for execution slot')


class FrozenClock(datetime):
    @classmethod
    def now(cls,tz=None):return at(12)


class QueueServiceProgressTests(QueueFixture):
    def test_generator_and_queue_service_report_identical_progress(self):
        import queue_server
        manager=self.manager();manager._ready()
        row=manager.store.admit('voxcpm2','voxcpm2','design',False,{'destination':str(self.root/'outputs')},None,'f')
        manager.store.claim('voxcpm2')
        manager.store.change(row['id'],stage='Generating speech',progress=saved(40.))
        raw=manager.store.read(row['id'])
        with patch.object(progress_estimate,'datetime',FrozenClock):
            queue_entry=queue_server.Entry.model_validate(queue_server.public(raw)).model_dump()
            generator_entry=Job.model_validate(manager.public(raw)).model_dump()
        self.assertEqual(queue_entry['progress'],generator_entry['progress'])
        self.assertEqual(queue_entry['progress']['percent'],30)

    def test_both_openapi_contracts_share_the_progress_schema(self):
        import queue_server,server
        queue=queue_server.app.openapi()['components']['schemas'];generator=server.app.openapi()['components']['schemas']
        self.assertEqual(queue['JobProgress'],generator['JobProgress'])
        for schemas,name in ((queue,'Entry'),(generator,'Job'),(generator,'CandidateBatch')):
            self.assertIn('progress',schemas[name]['properties'])


if __name__ == '__main__':
    unittest.main()
