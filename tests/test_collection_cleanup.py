"""Expired media is removed only from disposable fixtures, never from real catalogs."""
from datetime import datetime, timezone, timedelta
import fcntl
import json
import sqlite3
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from collection_cleanup import CollectionCleaner
import storage
import voice_catalog as catalog
from . import test_contract


class CollectionCleanupTests(TestCase):
    def setUp(self):
        self.f=test_contract.ContractTests('runTest');self.f.setUp();self.addCleanup(self.f.tearDown)
        self.cleaner=CollectionCleaner(self.f.manager,self.f.root/'outputs')

    def voice(self, reference=None):
        reference=reference or self.f.upload().json()['id']
        row=self.f.client.post('/api/voices',json={'reference_id':reference,'name':'Disposable narrator'}).json()
        return row,reference

    def deleted(self, kind, identity, expired=False):
        api='voices' if kind=='voices' else 'artifacts'
        response=self.f.client.delete('/api/'+api+'/'+identity)
        self.assertEqual(response.status_code,200,response.text)
        marker=catalog.collection_marker(self.f.root,kind,identity)
        if expired:
            record=json.loads(marker.read_text())
            record['deleted_at']=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()
            catalog.atomic_json(marker,record)
        return marker

    def test_exact_24_hour_boundary_removes_voice_and_unshared_original_reference(self):
        voice,reference=self.voice();marker=self.deleted('voices',voice['id'])
        info=catalog.deletion_info(self.f.root,'voices',voice['id'])
        deadline=datetime.fromisoformat(info.purge_after).timestamp()
        self.assertEqual(deadline-datetime.fromisoformat(info.deleted_at).timestamp(),86400)
        self.assertEqual(self.cleaner.sweep(deadline-1)['removed_last_run'],0)
        paths=[self.f.root/'voices'/f'{voice["id"]}{suffix}' for suffix in ('.wav','.json')]
        paths += [self.f.root/'references'/f'{reference}{suffix}' for suffix in ('.wav','.json')]
        paths += [self.f.root/'references/originals'/f'{reference}.bin']
        companions=[path.with_name('._'+path.name) for path in [*paths,marker]]
        for path in companions:path.write_bytes(b'\x00\x05\x16\x07Mac metadata fixture')
        self.assertTrue(all(path.is_file() for path in paths))
        self.assertEqual(self.cleaner.sweep(deadline)['removed_last_run'],1)
        self.assertIsNone(self.cleaner.snapshot()['last_error'])
        self.assertTrue(all(not path.exists() for path in [*paths,marker,*companions]))
        self.assertEqual(self.f.client.get(voice['audio_url']).status_code,404)
        self.assertEqual(self.f.client.get('/api/voices?deleted=true').json()['total'],0)
        self.assertEqual(self.f.client.post('/api/voices/'+voice['id']+'/restore').status_code,404)
        self.assertEqual(self.cleaner.sweep(deadline+1)['removed_last_run'],0)

    def test_restore_before_deadline_cancels_cleanup_and_expired_restore_is_gone(self):
        voice,_=self.voice();self.deleted('voices',voice['id'])
        self.assertEqual(self.f.client.post('/api/voices/'+voice['id']+'/restore').status_code,200)
        self.assertEqual(self.cleaner.sweep(datetime.now(timezone.utc).timestamp()+90000)['removed_last_run'],0)
        self.assertEqual(self.f.client.get(voice['audio_url']).status_code,200)
        self.deleted('voices',voice['id'],expired=True)
        self.assertEqual(self.f.client.post('/api/voices/'+voice['id']+'/restore').status_code,410)
        row=self.f.client.get('/api/voices?deleted=true').json()['data'][0]
        self.assertIsNotNone(row['deletion']['purge_after'])

    def test_shared_upload_remains_until_last_saved_copy_is_deleted(self):
        first,reference=self.voice();second,_=self.voice(reference)
        self.deleted('voices',first['id'],expired=True);self.cleaner.sweep()
        self.assertTrue((self.f.root/'references/originals'/f'{reference}.bin').is_file())
        self.assertEqual(self.f.client.get(second['audio_url']).status_code,200)
        self.deleted('voices',second['id'],expired=True);self.cleaner.sweep()
        self.assertFalse((self.f.root/'references/originals'/f'{reference}.bin').exists())

    def test_custom_output_files_and_index_removed_but_independent_voice_and_job_survive(self):
        custom=self.f.root/'custom-audio'
        done=self.f.finish(self.f.generate(output_dir=str(custom)).json());artifact=done['result']
        saved=self.f.client.post('/api/voices',json={'output_id':artifact['id'],'name':'Independent saved voice'}).json()
        audio=Path(artifact['path']);marker=self.deleted('outputs',artifact['id'],expired=True)
        self.assertEqual(self.cleaner.sweep()['removed_last_run'],1)
        for path in (audio,audio.with_suffix('.json'),self.f.root/'artifact-index'/f'{artifact["id"]}.json',marker):
            self.assertFalse(path.exists(),path)
        self.assertEqual(self.f.client.get(saved['audio_url']).status_code,200)
        self.assertEqual(self.f.client.get('/api/jobs/'+done['id']).json()['status'],'done')
        self.assertEqual(self.f.client.get('/api/history?deleted=true').json()['total'],0)

    def test_frozen_and_opaque_unfinished_jobs_delay_cleanup_then_release_files(self):
        for opaque in (False,True):
            with self.subTest(opaque=opaque),self.f.manager.lock_file.open('a+') as reservation:
                fcntl.flock(reservation,fcntl.LOCK_EX|fcntl.LOCK_NB)
                voice,_=self.voice()
                request={'voice_id':voice['id'],'text':'Hello.'}
                if opaque:
                    job=self.f.manager.store.admit_opaque('voxcpm2',request)
                else:
                    response=self.f.client.post('/api/generate',json=request);self.assertEqual(response.status_code,202,response.text);job=response.json()
                self.deleted('voices',voice['id'],expired=True)
                self.assertEqual(self.cleaner.sweep()['deferred_last_run'],1)
                self.assertEqual(self.f.client.get(voice['audio_url']).status_code,200)
                row=self.f.client.get('/api/voices?deleted=true').json()['data'][0]
                self.assertIn('unfinished job',row['deletion']['cleanup_deferred_reason'])
                self.f.manager.store.remove(job['id'])
                self.assertEqual(self.cleaner.sweep()['removed_last_run'],1)
                self.assertEqual(self.f.client.get(voice['audio_url']).status_code,404)

    def test_partial_unlink_failure_resumes_persisted_plan_after_restart(self):
        voice,_=self.voice();marker=self.deleted('voices',voice['id'],expired=True)
        original=Path.unlink;failure=self.f.root/'voices'/f'{voice["id"]}.json'
        def unlink(path,*args,**kwargs):
            if path==failure:raise PermissionError('Disposable write failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink):
            state=self.cleaner.sweep()
        self.assertIn('write failure',state['last_error'])
        self.assertTrue(json.loads(marker.read_text())['cleanup_started'])
        self.assertFalse((self.f.root/'voices'/f'{voice["id"]}.wav').exists())
        restarted=CollectionCleaner(self.f.manager,self.f.root/'outputs')
        self.assertEqual(restarted.sweep()['removed_last_run'],1)
        self.assertFalse(marker.exists());self.assertFalse(failure.exists())

    def test_missing_volume_symlinks_and_untrusted_output_path_preserve_files(self):
        voice,_=self.voice();marker=self.deleted('voices',voice['id'],expired=True)
        with patch('collection_cleanup.validate_mount',side_effect=RuntimeError('Volume disconnected')):
            self.assertIn('disconnected',self.cleaner.sweep()['last_error'])
        with patch.object(self.f.manager.store,'transaction',side_effect=sqlite3.OperationalError('Queue unavailable')):
            self.assertIn('Queue unavailable',self.cleaner.sweep()['last_error'])
        self.assertEqual(self.f.client.get(voice['audio_url']).status_code,200)
        original=self.f.root/'voices'/f'{voice["id"]}.wav'
        outside=self.f.root/'unrelated.wav';original.rename(outside);original.symlink_to(outside)
        self.assertIn('symbolic-link',self.cleaner.sweep()['last_error'])
        self.assertTrue(outside.is_file());self.assertTrue(marker.is_file())
        done=self.f.finish(self.f.generate().json());artifact=done['result']
        output_marker=self.deleted('outputs',artifact['id'],expired=True)
        index=self.f.root/'artifact-index'/f'{artifact["id"]}.json';index.write_text(json.dumps({'path':str(outside)}))
        self.assertIn('ID-named',self.cleaner.sweep()['last_error'])
        self.assertTrue(outside.is_file());self.assertTrue(output_marker.is_file())

    def test_cleanup_status_and_new_submissions_do_not_reuse_deleted_voices(self):
        state=self.f.client.get('/api/collection/cleanup').json()
        self.assertEqual(state['retention_seconds'],86400);self.assertTrue(state['running'])
        voice,_=self.voice();self.deleted('voices',voice['id'])
        result=self.f.client.post('/api/generate',json={'voice_id':voice['id'],'text':'Hello.'})
        self.assertEqual(result.status_code,422,result.text)
        self.assertEqual(self.f.client.get(voice['audio_url']).status_code,200)

    def test_background_sweep_and_shutdown_need_no_http_cleanup_request(self):
        with patch('collection_cleanup.INTERVAL_SECONDS',.02):
            self.cleaner.start()
            try:
                voice,_=self.voice();marker=self.deleted('voices',voice['id'],expired=True)
                end=time.monotonic()+3
                while marker.exists() and time.monotonic()<end:time.sleep(.01)
                self.assertFalse(marker.exists())
            finally:self.cleaner.close()
        self.assertFalse(self.cleaner.snapshot()['running'])
