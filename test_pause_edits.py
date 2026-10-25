"""CPU-only reviewed pause editing: exact PCM preservation, API persistence and failure bounds."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import soundfile as sf
import pause_edits
from schemas import PauseAdjustment, Generation
import storage
import test_contract


def request(spans=None,**extra):
    return PauseAdjustment(spans=spans or [{'start_seconds':.4,'end_seconds':.6}],reviewed=True,**extra)


def samples():
    data=np.full((16000,1),4000,dtype=np.int16)
    data[6400:9600]=0
    return data


class PauseSampleTests(unittest.TestCase):
    def test_exact_preservation_and_minimum_total_gap(self):
        source=samples();original=source.copy()
        adjusted,edits=pause_edits.insert_reviewed_pauses(source,16000,request())
        edit=edits[0];self.assertEqual(edit['inserted_samples'],8800)
        self.assertEqual(edit['effective_gap_samples'],12000)
        at=edit['insertion_sample'];added=edit['inserted_samples']
        self.assertTrue(np.array_equal(np.concatenate([adjusted[:at],adjusted[at+added:]]),original))
        self.assertTrue(np.array_equal(source,original))
        self.assertFalse(np.any(adjusted[at:at+added]))
    def test_stereo_extreme_samples_are_preserved(self):
        source=np.concatenate([samples(),-samples()],axis=1);source[100]=[-32768,32767]
        adjusted,edits=pause_edits.insert_reviewed_pauses(source,16000,request())
        at=edits[0]['insertion_sample'];added=edits[0]['inserted_samples']
        self.assertTrue(np.array_equal(np.concatenate([adjusted[:at],adjusted[at+added:]]),source))
    def test_long_gap_noop_and_multiple_offsets(self):
        source=np.full((32000,1),4000,dtype=np.int16);source[4000:20000]=0
        adjusted,edits=pause_edits.insert_reviewed_pauses(source,16000,request([{'start_seconds':.25,'end_seconds':1.25}]))
        self.assertEqual(edits[0]['inserted_samples'],0);self.assertTrue(np.array_equal(source,adjusted))
        source=samples();source[11200:12800]=0
        adjusted,edits=pause_edits.insert_reviewed_pauses(source,16000,request([{'start_seconds':.4,'end_seconds':.6},{'start_seconds':.7,'end_seconds':.8}]))
        restored=adjusted.copy();shift=0
        # Remove inserted blocks in descending output position; original samples survive exactly.
        positions=[]
        for edit in edits:
            positions.append((edit['insertion_sample']+shift,edit['inserted_samples']));shift+=edit['inserted_samples']
        for at,count in reversed(positions):restored=np.concatenate([restored[:at],restored[at+count:]])
        self.assertTrue(np.array_equal(source,restored))
    def test_fail_closed_on_voiced_edges_short_spans(self):
        for spans in [[{'start_seconds':.1,'end_seconds':.3}],
                      [{'start_seconds':0,'end_seconds':.1}],
                      [{'start_seconds':.9,'end_seconds':1.1}],
                      [{'start_seconds':.4,'end_seconds':.41}]]:
            with self.assertRaises(ValueError):pause_edits.insert_reviewed_pauses(samples(),16000,request(spans))
    def test_schema_rejects_nonfinite_unreviewed_overlap_and_bounds(self):
        for spans in [[{'start_seconds':float('nan'),'end_seconds':.6}],
                      [{'start_seconds':.4,'end_seconds':float('inf')}],
                      [{'start_seconds':1e308,'end_seconds':1e308}],
                      [{'start_seconds':.6,'end_seconds':.4}],
                      [{'start_seconds':-.1,'end_seconds':.2}],
                      [{'start_seconds':.4,'end_seconds':.6},{'start_seconds':.5,'end_seconds':.7}]]:
            with self.assertRaises(ValueError):request(spans)
        for fields in [dict(reviewed=False),dict(reviewed=None),dict(minimum_gap_ms=5001),dict(spans=[]),dict(spans=[{'start_seconds':1,'end_seconds':2}]*31)]:
            with self.assertRaises(ValueError):PauseAdjustment(**{'spans':[{'start_seconds':.4,'end_seconds':.6}],'reviewed':True,**fields})
        self.assertNotIn('spans',Generation(text='Hello',description='A voice').model_dump())
    def test_output_bounds_and_fixed_quiet_threshold(self):
        with patch('pause_edits.MAX_OUTPUT_SECONDS',1):
            with self.assertRaises(ValueError):pause_edits.insert_reviewed_pauses(samples(),16000,request())
        source=samples();source[8000]=33
        with self.assertRaises(ValueError):pause_edits.insert_reviewed_pauses(source,16000,request())


class PauseAPITests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_contract.ContractTests(methodName='runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.client=self.fixture.client;self.root=self.fixture.root
        self.identity='a'*32;self.source=self.root/'custom-source'/f'{self.identity}.wav';self.source.parent.mkdir()
        sf.write(self.source,samples(),16000,subtype='PCM_16')
        self.original={'id':self.identity,'text':'Hello. Welcome.','created_at':'2026-10-25T00:00:00+00:00',
            'duration':1.,'sample_rate':16000,'channels':1,'format':'WAV/PCM_16','requested':{'seed':42,'clone_mode':'combined'},
            'effective':{'seed':42,'clone_mode':'combined'},'path':str(self.source),'audio_url':f'/api/audio/outputs/{self.identity}'}
        storage.persist_artifact(self.source,self.original)
    def edit(self,**extra):
        body={'spans':[{'start_seconds':.4,'end_seconds':.6}],'reviewed':True,**extra}
        return self.client.post(f'/api/artifacts/{self.identity}/pauses',json=body)
    def test_metadata_history_custom_folder_reuse_and_source_untouched(self):
        raw=self.source.read_bytes();folder=self.root/'custom-edited'
        response=self.edit(output_dir=str(folder),idempotency_key='pause-key');self.assertEqual(response.status_code,201,response.text)
        row=response.json();self.assertNotEqual(row['id'],self.identity);self.assertEqual(Path(row['path']).parent,folder.resolve())
        self.assertEqual(self.source.read_bytes(),raw);self.assertEqual(row['requested'],self.original['requested'])
        self.assertEqual(row['effective'],self.original['effective'])
        transform=row['transformation'];self.assertEqual(transform['source']['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(transform['source']['artifact_id'],self.identity)
        self.assertEqual(transform['effective']['spans'][0]['original_gap_samples'],3200)
        self.assertEqual(transform['effective']['inserted_samples'],8800)
        self.assertFalse(transform['effective']['automatic_sentence_alignment'])
        self.assertEqual(self.edit(output_dir=str(folder),idempotency_key='pause-key').json()['id'],row['id'])
        self.assertEqual(self.edit(output_dir=str(folder),idempotency_key='pause-key',minimum_gap_ms=1000).status_code,409)
        self.assertEqual(self.client.get('/api/history').json()['total'],2)
        self.assertEqual(self.client.get('/api/artifacts/'+row['id']).status_code,200)
        self.assertEqual(self.client.get('/api/audio/outputs/'+row['id']).status_code,200)
        decoded,sr=sf.read(row['path'],dtype='int16',always_2d=True)
        self.assertEqual(sr,16000);self.assertEqual(len(decoded),24800)
        self.assertTrue(np.array_equal(np.concatenate([decoded[:8000],decoded[16800:]]),samples()))
        self.assertEqual(len(self.fixture.engine.calls),0)
    def test_missing_mount_voiced_span_and_edit_busy_leave_no_derivative(self):
        self.assertEqual(self.edit(spans=[{'start_seconds':.1,'end_seconds':.2}]).status_code,422)
        with patch('storage.validate_mount',side_effect=RuntimeError('Storage volume is not mounted')):
            self.assertEqual(self.edit(output_dir='/Volumes/absent-test/outputs').status_code,422)
        with pause_edits.EDIT_LOCK:
            self.assertEqual(self.edit().status_code,409)
        self.assertEqual(self.client.get('/api/history').json()['total'],1)
    def test_pause_and_legacy_generation_retry_namespaces_do_not_collide(self):
        edited=self.edit(idempotency_key='shared-key');self.assertEqual(edited.status_code,201)
        generated=self.fixture.generate(idempotency_key='shared-key');self.assertEqual(generated.status_code,202)
        self.assertEqual(self.fixture.finish(generated.json())['status'],'done')
        self.assertEqual(self.fixture.generate(idempotency_key='shared-key').json()['id'],generated.json()['id'])
        self.assertEqual(self.edit(idempotency_key='shared-key').json()['id'],edited.json()['id'])
    def test_nonfinite_json_times_return_validation_errors(self):
        for value in ['NaN','Infinity','-Infinity','1e309']:
            response=self.client.post(f'/api/artifacts/{self.identity}/pauses',
                content='{"spans":[{"start_seconds":'+value+',"end_seconds":0.6}],"reviewed":true}',
                headers={'content-type':'application/json'})
            self.assertEqual(response.status_code,422,response.text)
            self.assertIn('finite',response.json()['detail'][0]['msg'])
    def test_unsupported_format_size_and_source_bounds(self):
        sf.write(self.source,samples().astype(np.float32),16000,subtype='FLOAT')
        self.assertEqual(self.edit().status_code,422)
        sf.write(self.source,samples(),16000,subtype='PCM_16')
        with patch('pause_edits.MAX_BYTES',10):self.assertEqual(self.edit().status_code,422)
        self.assertEqual(self.edit(spans=[{'start_seconds':.9,'end_seconds':1.1}]).status_code,422)
        self.assertEqual(self.client.post('/api/artifacts/'+'b'*32+'/pauses',json=request().model_dump()).status_code,404)
    def test_completed_derivative_chain_preserves_original_generation(self):
        row=self.edit().json();self.identity=row['id']
        next_row=self.edit(spans=[{'start_seconds':.4,'end_seconds':1.15}]).json()
        self.assertEqual(next_row['transformation']['effective']['inserted_samples'],0)
        self.assertEqual(next_row['transformation']['source']['original_artifact_id'],'a'*32)
        self.assertEqual(len(next_row['transformation_history']),1)
        self.assertEqual(next_row['requested'],self.original['requested'])


if __name__=='__main__':unittest.main()
