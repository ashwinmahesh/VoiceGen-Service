"""Punctuation boundaries, unchanged speech samples, frozen jobs and strict model options; no GPU."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import capabilities
from config import PROJECT_ROOT
from jobs import JobManager
from schemas import Generation,PACING_FIELDS
from punctuation_pacing import BACKENDS,phrases,synthesize


TEXT='My guests. You came. Sit, please. I trust the carriage was comfortable. Come, my dear. This house has waited a very long time to hear your step.'


class PunctuationPacingTests(unittest.TestCase):
    def request(self,backend,**options):return Generation(text=TEXT,reference_id='a'*32,language='auto' if backend=='fish' else 'English',**{backend:options})
    def effective(self,backend,request):
        profile=json.loads((PROJECT_ROOT/'backends'/backend/'service.json').read_text())
        with patch('capabilities.PROFILE',profile):return capabilities.validate_controls(request)

    def test_defaults_and_reference_guard_leave_other_models_alone(self):
        for backend in BACKENDS:
            effective=self.effective(backend,self.request(backend))
            options=getattr(effective,backend)
            self.assertTrue(options.punctuation_pauses)
            self.assertEqual((options.sentence_pause_ms,options.comma_pause_ms),(500,200))
        designed=Generation(text=TEXT,description='Cultured baritone.')
        self.assertFalse(self.effective('qwen3',designed).qwen3.punctuation_pauses)
        with self.assertRaises(ValueError):self.effective('qwen3',designed.model_copy(update={'qwen3':self.request('qwen3',punctuation_pauses=True).qwen3}))
        for backend in ['voxcpm2','higgs']:
            profile=json.loads((PROJECT_ROOT/'backends'/backend/'service.json').read_text())
            with patch('capabilities.PROFILE',profile):self.assertNotIn('punctuation_pacing',capabilities.capabilities().limits)

    def test_boundaries_preserve_words_and_protect_abbreviations_numbers_annotations(self):
        rows=phrases(TEXT,500,200)
        self.assertEqual(' '.join(row['text'] for row in rows),TEXT)
        self.assertEqual(sum(row['after']=='comma' for row in rows),2)
        self.assertEqual(rows[-1]['pause_ms'],0)
        text='Dr. Smith paid 1,234.50, then left. "Really?" <going|G OW1 . IH0 NG> home.'
        rows=phrases(text,500,200)
        self.assertEqual([row['text'] for row in rows],['Dr. Smith paid 1,234.50,','then left.','"Really?"','<going|G OW1 . IH0 NG> home.'])
        self.assertEqual(len(phrases('你好，朋友。再见！',500,200)),3)
        self.assertEqual(len(phrases('Hello, friend. Welcome.',500,0)),2)
        self.assertEqual(len(phrases('Hello, friend. Welcome.',0,200)),2)
        with self.assertRaises(ValueError):phrases('A phrase. '*34,500,200)

    def test_sample_exact_silence_and_unchanged_conditioning_for_all_four_backends(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend):
                request=self.effective(backend,self.request(backend));calls=[];source=[]
                def generate(part):
                    calls.append(part);data=np.array([.1,.2,-.3,.4],dtype=np.float32);source.append(data)
                    self.assertEqual(part.reference_id,request.reference_id)
                    self.assertEqual(getattr(part,backend),getattr(request,backend))
                    return 16000,data
                sr,audio,info=synthesize(request,backend,generate)
                self.assertEqual(sr,16000);self.assertEqual(info['strategy'],'punctuation_segments')
                for data,row in zip(source,info['phrases']):
                    start=row['start_sample'];end=start+row['audio_samples']
                    np.testing.assert_array_equal(audio[start:end],data)
                    self.assertEqual(row['inserted_samples'],row['pause_ms']*16)
                    self.assertFalse(audio[end:end+row['inserted_samples']].any())
                self.assertEqual(len(calls),len(phrases(TEXT,500,200)))

    def test_whole_line_opt_out_and_old_frozen_settings(self):
        for backend in BACKENDS:
            for request in [self.request(backend),self.effective(backend,self.request(backend,punctuation_pauses=False))]:
                calls=[]
                _,_,info=synthesize(request,backend,lambda part:(calls.append(part.text) or (16000,np.ones(4))))
                self.assertEqual(calls,[TEXT]);self.assertEqual(info['strategy'],'whole_line')
        tagged=self.request('fish').model_copy(update={'text':'My guests. [pause] You came.'})
        self.assertFalse(self.effective('fish',tagged).fish.punctuation_pauses)

    def test_failed_phrase_never_returns_partial_take(self):
        request=self.effective('fish',self.request('fish'))
        for invalid in [np.zeros(4),np.array([float('nan')]),np.array([])]:
            with self.assertRaises(RuntimeError):synthesize(request,'fish',lambda part:(16000,invalid))

    def test_moss_pacing_uses_one_native_pass_with_honest_timing_targets(self):
        from punctuation_pacing import moss_instructions
        import native_engine
        request=Generation(text=TEXT,description='Cultured baritone.',language='English',moss={'sentence_pause_ms':900,'comma_pause_ms':350})
        effective=self.effective('moss',request)
        self.assertTrue(effective.moss.punctuation_pauses)
        with patch('native_engine.PROFILE',json.loads((PROJECT_ROOT/'backends/moss/service.json').read_text())),patch('native_engine.MODEL_SPECS',{'moss':{'files':[{'path':'voice.gguf'}]}}),patch('native_engine.Path.is_file',return_value=True):
            args=native_engine.NativeEngine().command(effective,None,'',Path('/tmp/voice-pacing-fixture.wav'))
        self.assertEqual(args[args.index('--text')+1],TEXT)
        self.assertIn('instruct='+moss_instructions(effective),args)
        self.assertIn('0.9 seconds between sentences',moss_instructions(effective))
        self.assertIn('0.35 seconds after commas',moss_instructions(effective))
        self.assertNotIn('Delivery pacing:',moss_instructions(request.model_copy(update={'moss':None})))

    def test_strict_namespaces_ranges_and_pre_pacing_key_compatibility(self):
        for backend in BACKENDS:
            for options in [{'comma_pause_ms':1501},{'sentence_pause_ms':-1},{'sentence_pause_ms':float('nan')},{'unknown':1}]:
                with self.assertRaises(ValueError):self.request(backend,**options)
            request=self.request(backend)
            exclusions={'model':True,'idempotency_key':True,'prompt_expansion_id':True,'qwen3':True,'moss':True}
            if backend!='qwen3':exclusions[backend]=PACING_FIELDS
            # qwen3 did not exist as a namespace before this change.
            old=hashlib.sha256(request.model_dump_json(exclude=exclusions).encode()).hexdigest()
            if backend!='qwen3':self.assertEqual(JobManager.fingerprint(request),old)
            changed=self.request(backend,punctuation_pauses=False)
            self.assertNotEqual(JobManager.fingerprint(request),JobManager.fingerprint(changed))
        with self.assertRaises(ValueError):Generation(text=TEXT,reference_id='a'*32,sentence_pause_ms=800)
