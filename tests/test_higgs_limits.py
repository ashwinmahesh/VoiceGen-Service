"""Higgs length budgets, failed-option recovery and native EOC handling; no inference."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import capabilities
import native_engine
from config import PROJECT_ROOT
from jobs import JobManager
from schemas import Generation, CandidateGeneration
from . import test_contract


def higgs_profile():
    return json.loads((PROJECT_ROOT/'backends/higgs/service.json').read_text())


class HiggsLimitTests(unittest.TestCase):
    def test_api_budget_is_honored_and_larger_ui_default_is_discoverable(self):
        with patch('capabilities.PROFILE',higgs_profile()):
            caps=capabilities.capabilities()
            self.assertEqual(caps.controls['max_tokens']['default'],1000)
            self.assertEqual(caps.controls['max_tokens']['ui_default'],2000)
            for limit in (None,160,1000,2000):
                r=Generation(text='Welcome.',reference_id='a'*32,**({} if limit is None else {'max_tokens':limit}))
                effective=capabilities.validate_controls(r)
                self.assertEqual(effective.max_tokens,1000 if limit is None else limit)
                self.assertEqual(effective.higgs.text_chunk_size,1024)
            r=Generation(text='Welcome.',reference_id='a'*32,higgs={'text_chunk_size':400})
            effective=capabilities.validate_controls(r)
            with patch('native_engine.PROFILE',higgs_profile()),patch('native_engine.MODEL_SPECS',{'higgs':{'files':[{'path':'model.gguf'}]}}),patch('native_engine.Path.is_file',return_value=True):
                command=native_engine.NativeEngine().command(effective,Path('/ref.wav'),'Hello.',Path('/out.wav'))
            self.assertEqual(command[command.index('--text-chunk-size')+1],'400')
            self.assertEqual(command[command.index('--max-tokens')+1],'1000')
            self.assertFalse(any('allow_incomplete' in value for value in command))
        for size in (0,79,1025):
            with self.assertRaises(ValueError):Generation(text='Welcome.',reference_id='a'*32,higgs={'text_chunk_size':size})
        with patch('capabilities.PROFILE',json.loads((PROJECT_ROOT/'backends/voxcpm2/service.json').read_text())):
            with self.assertRaises(ValueError):capabilities.validate_controls(r)

    def test_omitted_chunk_setting_preserves_prior_single_and_batch_fingerprints(self):
        for cls,batch in ((Generation,False),(CandidateGeneration,True)):
            r=cls(text='Welcome.',reference_id='a'*32,higgs={'emotion':'amusement'},idempotency_key='existing-key')
            excluded={'idempotency_key','model','prompt_expansion_id'}
            if batch:excluded.update({'candidate_variation','candidate_use_llm'})
            else:excluded.update({'qwen3','moss'})
            previous=r.model_dump_json(exclude=excluded).replace(',"text_chunk_size":null','')
            expected=hashlib.sha256(previous.encode()).hexdigest()
            actual=JobManager.candidate_fingerprint(r) if batch else JobManager.fingerprint(r)
            self.assertEqual(actual,expected)

    def test_eoc_failure_is_actionable_never_publishes_incomplete_audio_or_retries(self):
        parent=PROJECT_ROOT/'state/validation';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            root=Path(folder);(root/'logs').mkdir();destination=root/'take.wav'
            def fail(*args,**kwargs):
                kwargs['stdout'].write('Higgs TTS generation reached max_tokens (1000) before EOC for this text chunk\n')
                destination.with_suffix('.tmp.wav').write_bytes(b'incomplete')
                return SimpleNamespace(returncode=1)
            with patch('native_engine.ROOT',root),patch('native_engine.PROFILE',higgs_profile()),patch('native_engine.MODEL_SPECS',{'higgs':{'repo':'fixture/higgs'}}),patch('native_engine.all_errors',return_value=[]),patch.object(native_engine.NativeEngine,'command',return_value=['native']),patch('native_engine.subprocess.run',side_effect=fail) as run:
                with self.assertRaisesRegex(RuntimeError,r'reached max_tokens \(1000\) before EOC.*Advanced settings'):
                    native_engine.NativeEngine().generate(Generation(text='Welcome.',reference_id='a'*32),Path('/ref.wav'),destination)
                self.assertEqual(run.call_count,1)
                self.assertFalse(destination.exists())
                self.assertFalse(destination.with_suffix('.tmp.wav').exists())


class HiggsPartialBatchTests(unittest.TestCase):
    def test_replacement_with_higher_budget_keeps_original_success_and_failed_settings(self):
        fixture=test_contract.ContractTests(methodName='runTest');fixture.setUp();self.addCleanup(fixture.tearDown)
        reference=fixture.upload().json()['id'];original=fixture.engine.generate
        def generate(request,*args,**kwargs):
            if request.seed==43 and request.max_tokens==1000:
                raise RuntimeError('Higgs TTS generation reached max_tokens (1000) before EOC')
            return original(request,*args,**kwargs)
        fixture.engine.generate=generate
        with patch('server.PROFILE',higgs_profile()),patch('capabilities.PROFILE',higgs_profile()),patch.object(fixture.manager,'profile',higgs_profile()):
            response=fixture.client.post('/api/candidates',json={'reference_id':reference,'ref_text':'Hello.','text':'Welcome.','candidate_count':2,'max_tokens':1000,'higgs':{'text_chunk_size':400}})
            self.assertEqual(response.status_code,202,response.text)
            for job in response.json()['jobs']:fixture.finish(job)
            batch=fixture.client.get('/api/candidates/'+response.json()['id']).json()
            self.assertEqual((batch['status'],batch['done_count']),('partial',1))
            good,failed=batch['jobs'];self.assertEqual((good['status'],failed['status']),('done','error'))
            path=Path(good['result']['path']);before=path.read_bytes()
            replacement=fixture.client.post('/api/generate',json={**failed['requested'],'max_tokens':2000})
            self.assertEqual(replacement.status_code,202,replacement.text)
            self.assertEqual(fixture.finish(replacement.json())['status'],'done')
            self.assertEqual(path.read_bytes(),before)
            unchanged=fixture.client.get('/api/jobs/'+failed['id']).json()
            self.assertEqual((unchanged['status'],unchanged['effective']['max_tokens']),('error',1000))


if __name__=='__main__':unittest.main()
