"""Extension validation, adapter routing, and pure-numpy steering lifetime checks. No GPU/downloads."""
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import numpy as np
import capabilities,engine
from schemas import Generation
from config import PROJECT_ROOT
from emotion_steering import read_vector,steering_weight,preserve_norm,steer_layers


class ExtensionTests(unittest.TestCase):
    def request(self,**extra):return Generation(text='Welcome home.',description='A calm baritone.',**extra)
    def profile(self,backend):return {'backend':backend,'id':backend,'name':backend,'sample_rate':24000,'quantization':'test'}
    def test_native_customvoice_routing(self):
        adapter=engine.Engine();adapter.model=Mock()
        with patch('engine.PROFILE',self.profile('qwen3')):
            adapter.backend_call(self.request(preset_voice='ryan',accent='Romanian-accented English',emotion='Quiet contempt.',temperature=1.1),None,'')
        call=adapter.model.generate_custom_voice.call_args.kwargs
        self.assertEqual(call['speaker'],'ryan');self.assertIn('Romanian',call['instruct']);self.assertIn('contempt',call['instruct'])
        adapter.model.generate.assert_not_called();adapter.model.generate_voice_design.assert_not_called()
    def test_vox_continuation_routing(self):
        adapter=engine.Engine();adapter.model=Mock();reference=Path('/tmp/reference.wav')
        with patch('engine.PROFILE',self.profile('voxcpm2')):
            adapter.backend_call(self.request(clone_mode='continuation',voice_id='a'*32,steps=10,guidance=2),reference,'These exact words.')
        call=adapter.model.generate.call_args.kwargs
        self.assertEqual(call['prompt_audio'],str(reference));self.assertEqual(call['prompt_text'].strip(),'These exact words.')
        self.assertNotIn('ref_audio',call);self.assertIsNone(call['instruct'])
    def test_vox_combined_routing_and_backend_restrictions(self):
        adapter=engine.Engine();adapter.model=Mock();reference=Path('/tmp/reference.wav')
        request=self.request(clone_mode='combined',voice_id='a'*32,ref_text='Exact words.')
        with patch('engine.PROFILE',self.profile('voxcpm2')):
            adapter.backend_call(request,reference,'Exact words.  \n')
        call=adapter.model.generate.call_args.kwargs
        self.assertEqual(call['prompt_audio'],str(reference))
        self.assertEqual(call['ref_audio'],str(reference))
        self.assertEqual(call['prompt_text'],'Exact words. ')
        self.assertIsNone(call['instruct'])
        for backend in ['qwen3','chatterbox','index','moss','fish','higgs']:
            with patch('capabilities.PROFILE',self.profile(backend)):
                with self.assertRaises(ValueError):capabilities.validate_controls(request)
        with patch('capabilities.PROFILE',self.profile('voxcpm2')):
            for fields in [dict(emotion='Quiet'),dict(accent='Romanian')]:
                with self.assertRaises(ValueError):capabilities.validate_controls(request.model_copy(update=fields))
            self.assertIn('combined',capabilities.capabilities().modes['reference']['clone_modes'])
    def test_combined_transcript_counts_toward_budget(self):
        adapter=engine.Engine();adapter.model=Mock()
        adapter.model.tokenizer.encode.side_effect=lambda text:list(text)
        with patch('engine.PROFILE',self.profile('voxcpm2')):
            request=self.request(clone_mode='combined',voice_id='a'*32)
            with self.assertRaises(ValueError):adapter.check_input_budget(request,Path('/tmp/ref.wav'),'x'*4096)
            self.assertLess(len(adapter.check_input_budget(request.model_copy(update={'clone_mode':'reference'}),Path('/tmp/ref.wav'),'x'*4096)),4096)
    def test_exclusive_speaker_sources(self):
        for fields in [dict(preset_voice='ryan',voice_id='a'*32),dict(preset_voice='ryan',reference_id='b'*32)]:
            with self.assertRaises(ValueError):self.request(**fields)
    def test_supported_and_unsupported_combinations(self):
        for backend in ['voxcpm2','qwen3']:
            specs={'custom':{}} if backend=='qwen3' else {}
            extension={'emotion_steering':{'source':'test','revision':'abc','license':'MIT'}} if backend=='qwen3' else {}
            with patch('capabilities.PROFILE',self.profile(backend)),patch('capabilities.MODEL_SPECS',specs),patch('capabilities.EXTENSION_SPECS',extension):
                if backend=='qwen3':
                    effective=capabilities.validate_controls(self.request(preset_voice='ryan',steering_emotion='contempt'))
                    self.assertEqual(effective.steering_strength,8);self.assertEqual(effective.temperature,1.1)
                    for kw in [dict(voice_id='a'*32,accent='Romanian'),dict(clone_mode='continuation',voice_id='a'*32),dict(steering_emotion='joy'),dict(preset_voice='ryan',steering_emotion='fake')]:
                        with self.assertRaises(ValueError):capabilities.validate_controls(self.request(**kw))
                else:
                    effective=capabilities.validate_controls(self.request(clone_mode='continuation',voice_id='a'*32,ref_text='Exact text.'))
                    self.assertEqual(effective.steps,10)
                    with self.assertRaises(ValueError):capabilities.validate_controls(self.request(clone_mode='continuation',voice_id='a'*32,emotion='Sad'))
                    with self.assertRaises(ValueError):capabilities.validate_controls(self.request(preset_voice='ryan'))
                    with self.assertRaises(ValueError):capabilities.validate_controls(self.request(voice_id='a'*32,ref_text='Ignored?'))
                with self.assertRaises(ValueError):capabilities.validate_controls(self.request(steering_strength=5))
    def test_steering_strength_bounds(self):
        for strength in [-1,13]:
            with self.assertRaises(ValueError):self.request(preset_voice='ryan',steering_emotion='sad',steering_strength=strength)
    def test_vector_architecture_and_corrupt_data(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT/'cache/tmp') as tmp:
            path=Path(tmp)/'fake.qlsteer'
            data=np.ones((29,2048),dtype='<f4')
            path.write_bytes(struct.pack('<Iii',0x54534C51,29,2048)+data.tobytes())
            self.assertEqual(read_vector(path).shape,(29,2048))
            for blob in [b'',struct.pack('<Iii',0x54534C51,29,1024)+data.tobytes(),path.read_bytes()[:-1]]:
                path.write_bytes(blob)
                with self.assertRaises(ValueError):read_vector(path)
            data[0,0]=np.nan;path.write_bytes(struct.pack('<Iii',0x54534C51,29,2048)+data.tobytes())
            with self.assertRaises(ValueError):read_vector(path)
    def test_norm_preservation_and_decode_schedule(self):
        x=np.array([[[1.,2.,3.]]]);vector=np.array([2.,-1.,0.])
        result=preserve_norm(x,vector,4,np)
        self.assertFalse(np.allclose(x,result));self.assertAlmostEqual(float(np.linalg.norm(x)),float(np.linalg.norm(result)))
        self.assertEqual(steering_weight(30,0,0,8),0)
        self.assertEqual(steering_weight(1,0,0,8),0)
        self.assertEqual(steering_weight(1,30,0,8),8)
        self.assertAlmostEqual(steering_weight(1,31,1,8),8*.985)
    def test_wrappers_only_steer_decode_and_restore_after_failure(self):
        class Layer:
            def __call__(self,x,*args):return x
        originals=[Layer() for _ in range(28)]
        model=SimpleNamespace(talker=SimpleNamespace(model=SimpleNamespace(layers=originals.copy())))
        vectors=np.ones((29,2048),dtype=np.float32);vectors[:,0]=-1
        x=np.arange(1,2049,dtype=np.float32).reshape(1,1,2048)
        try:
            with steer_layers(model,vectors,8,array_api=np):
                layer=model.talker.model.layers[21]
                self.assertIs(model.talker.model.layers[20],originals[20])
                self.assertTrue(np.array_equal(layer(x,None,cache=SimpleNamespace(offset=0)),x))
                self.assertFalse(np.array_equal(layer(x,None,cache=SimpleNamespace(offset=100)),x))
                raise RuntimeError('simulated worker failure')
        except RuntimeError:pass
        self.assertTrue(all(a is b for a,b in zip(model.talker.model.layers,originals)))
    def test_zero_strength_preserves_outputs(self):
        class Layer:
            def __call__(self,x,*args):return x
        model=SimpleNamespace(talker=SimpleNamespace(model=SimpleNamespace(layers=[Layer() for _ in range(28)])))
        x=np.ones((1,1,2048),dtype=np.float32)
        with steer_layers(model,np.ones((29,2048)),0,array_api=np):
            self.assertTrue(np.array_equal(model.talker.model.layers[21](x,None,cache=SimpleNamespace(offset=100)),x))


if __name__=='__main__':unittest.main()
