"""Meaningful design contrasts, frozen LLM planning and unchanged speaker/text boundaries."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from schemas import CandidateGeneration
from jobs import JobManager
from candidate_diversity import definition,built_in,build
from . import test_contract
from . import test_queue
import server


class DiversityTests(unittest.TestCase):
    def setUp(self):
        self.f=test_contract.ContractTests('runTest');self.f.setUp();self.addCleanup(self.f.tearDown)
    def submit(self,**updates):
        return self.f.client.post('/api/candidates',json=dict(text='  EXACT dialogue.\nKeep these words.  ',description='A rich, smooth cultured baritone.',accent='Strong Romanian accent.',emotion='Calm, courteous and mocking.',candidate_count=3,**updates))
    def test_default_designs_vary_descriptions_and_preserve_explicit_settings(self):
        response=self.submit();self.assertEqual(response.status_code,202,response.text);batch=response.json()
        results=[self.f.finish(job) for job in batch['jobs']]
        self.assertTrue(all(row['status']=='done' for row in results))
        calls=[row[0] for row in self.f.engine.calls]
        self.assertEqual(calls[0].description,'A rich, smooth cultured baritone.')
        self.assertEqual(len({row.description for row in calls}),3)
        self.assertEqual([row.seed for row in calls],[42,43,44])
        for row in results:
            result=row['result'];self.assertEqual(result['text'],'  EXACT dialogue.\nKeep these words.  ')
            self.assertEqual(result['accent'],'Strong Romanian accent.')
            self.assertEqual(result['emotion'],'Calm, courteous and mocking.')
            self.assertEqual(result['requested']['description'],'A rich, smooth cultured baritone.')
            self.assertEqual(result['candidate_variant']['description'],result['effective']['description'])
        self.assertEqual(batch['candidate_variation'],'distinct')
    def test_seed_only_and_reference_modes_preserve_the_same_voice_prompt(self):
        batch=self.submit(candidate_variation='seed_only').json()
        for row in batch['jobs']:self.f.finish(row)
        self.assertEqual(len({call[0].description for call in self.f.engine.calls}),1)
        reference=self.f.upload().json()
        body={'text':'Exact words.','reference_id':reference['id'],'candidate_count':2}
        response=self.f.client.post('/api/candidates',json=body);self.assertEqual(response.status_code,202,response.text)
        self.assertEqual(response.json()['candidate_variation'],'seed_only')
        for row in response.json()['jobs']:self.f.finish(row)
        self.assertEqual(self.f.client.post('/api/candidates',json={**body,'candidate_variation':'distinct'}).status_code,422)
    def test_per_request_llm_opt_out_uses_built_in_plan_even_when_served(self):
        self.f.manager.llm_enabled=True
        with patch.object(server.app.state,'llm_enabled',True),patch('candidate_diversity.generate',side_effect=AssertionError('LLM must stay off')):
            batch=self.submit(candidate_use_llm=False).json()
            for row in batch['jobs']:
                result=self.f.finish(row);self.assertEqual(result['status'],'done')
                self.assertEqual(result['candidate_variant']['method'],'built_in')
    def test_llm_plan_runs_once_and_freezes_before_any_speech_even_after_failure(self):
        self.f.manager.llm_enabled=True;calls=[]
        def worker(*args,**kwargs):
            request=json.loads(kwargs['input']);calls.append(request)
            serialized=json.dumps(request);self.assertNotIn('EXACT dialogue',serialized)
            self.assertIsNone(self.f.engine.model)
            self.assertTrue(kwargs['pass_fds'])
            for fd in kwargs['pass_fds']:os.fstat(fd)
            return SimpleNamespace(returncode=0,stdout=json.dumps({'completions':[json.dumps({'addition':value}) for value in [
                'Brighter forward resonance with lighter vocal weight, keeping the stated traits.',
                'Rounder chest resonance with fuller vocal weight, keeping the stated traits.']]}))
        with patch.object(server.app.state,'llm_enabled',True),patch('candidate_diversity.check_assets',return_value=Path('/weights')),\
             patch('candidate_diversity.check_runtime',return_value=Path('/python')),patch('candidate_diversity.subprocess.run',side_effect=worker):
            self.f.engine.fail=True
            batch=self.submit(idempotency_key='frozen-plan').json()
            for row in batch['jobs']:self.f.finish(row)
            frozen=[self.f.manager.store.read(row['id'])['payload'] for row in batch['jobs']]
            self.assertTrue(all(row['candidate_plan']['ready'] for row in frozen))
            self.assertEqual(len(calls),1)
            self.f.engine.fail=False;self.f.manager.llm_enabled=False
            with patch.object(server.app.state,'llm_enabled',False):
                for row in batch['jobs']:
                    self.assertEqual(self.f.client.post('/api/jobs/'+row['id']+'/retry').status_code,202)
                    self.assertEqual(self.f.finish(row)['status'],'done')
            self.assertEqual(len(calls),1)
            self.assertEqual(self.submit(idempotency_key='frozen-plan').json()['id'],batch['id'])
    def test_invalid_llm_plan_fails_group_without_speech_or_silent_fallback(self):
        self.f.manager.llm_enabled=True
        with patch.object(server.app.state,'llm_enabled',True),patch('candidate_diversity.generate',side_effect=ValueError('Invalid diversity schema')) as mocked:
            batch=self.submit().json()
            for row in batch['jobs']:self.assertEqual(self.f.finish(row)['status'],'error')
            self.assertEqual(mocked.call_count,1);self.assertFalse(self.f.engine.calls)
    def test_boundaries_and_old_candidate_fingerprints(self):
        from candidate_diversity import validate_additions
        import hashlib
        r=CandidateGeneration(text='Exact words.',description='A calm voice.',candidate_count=5)
        for backend in ('voxcpm2','qwen3','moss'):
            plan=definition(r,r,backend);variants=built_in(plan)
            self.assertEqual(len({row['description'] for row in variants}),5)
            for invalid in [['same']*4,['x'*301]*4,['[laugh]']*4,['one'],['word '*46]*4]:
                with self.assertRaises(ValueError):validate_additions(plan,invalid)
        old=r.model_dump_json(exclude={'model','idempotency_key','prompt_expansion_id','candidate_variation','candidate_use_llm'})
        self.assertEqual(JobManager.candidate_fingerprint(r),hashlib.sha256(old.encode()).hexdigest())
        self.assertNotEqual(JobManager.candidate_fingerprint(r),JobManager.candidate_fingerprint(r.model_copy(update={'candidate_variation':'seed_only'})))


class DiversityRecoveryTests(test_queue.QueueFixture):
    def test_paused_llm_group_does_not_block_runnable_jobs_when_disabled(self):
        manager=self.manager();manager.recover()
        request=CandidateGeneration(text='Exact dialogue.',description='A calm baritone.',candidate_count=2)
        paused=manager.submit_candidates(request,request,None,'',self.root/'outputs',llm_enabled=True)
        ready=self.submit(manager,text='Runnable without the LLM.')
        self.assertEqual(self.done(manager,ready['id'])['status'],'done')
        self.assertTrue(all(manager.read(row['id'])['paused'] for row in paused['jobs']))

    def test_unplanned_llm_group_waits_disabled_then_reuses_snapshot_after_restart(self):
        old=self.manager();request=CandidateGeneration(text='Exact dialogue.',description='A calm baritone.',candidate_count=2)
        batch=old.submit_candidates(request,request,None,'',self.root/'outputs',llm_enabled=True)
        self.assertIsNone(old.store.claim('voxcpm2',allow_llm=False));self.assertTrue(old.read(batch['jobs'][0]['id'])['paused'])
        original=old.store.read(batch['jobs'][0]['id'])['payload']['candidate_plan'];variants=build(original,['Bright forward resonance within the stated traits.'])
        old.store.freeze_candidate_variants(batch['id'],variants);old.close()
        new=self.manager();new.recover()
        with patch('candidate_diversity.generate',side_effect=AssertionError('Must not regenerate a frozen plan')):
            for row in batch['jobs']:self.assertEqual(self.done(new,row['id'])['status'],'done')
        self.assertEqual([call[0].description for call in new.engine.calls],[x['description'] for x in variants])
