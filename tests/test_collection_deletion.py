"""Reversible catalog deletion preserves frozen jobs and every recording."""
import json
import fcntl
from unittest import TestCase
from unittest.mock import patch
import voice_catalog
import storage
from . import test_contract


class CollectionDeletionTests(TestCase):
    def setUp(self):
        self.f=test_contract.ContractTests('runTest');self.f.setUp();self.addCleanup(self.f.tearDown)

    def voice(self,name='A demonstration voice',tags=None):
        reference=self.f.upload().json()
        response=self.f.client.post('/api/voices',json={'reference_id':reference['id'],'name':name,
            'tags':tags or ['character'],'transcript':'The exact recorded words.'})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def test_delete_restore_voice_updates_search_tags_and_keeps_all_source_files(self):
        selected=self.voice('A courteous host',['villain','baritone']);other=self.voice('A friendly guide',['narrator'])
        identity=selected['id'];base='/api/voices/'+identity
        before={p:p.read_bytes() for p in (self.f.root/'voices').glob(identity+'.*')}
        result=self.f.client.delete(base)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json(),dict(id=identity,kind='voices',deleted=True,changed=True))
        self.assertEqual([r['id'] for r in self.f.client.get('/api/voices').json()['data']],[other['id']])
        trash=self.f.client.get('/api/voices',params={'deleted':True,'q':'courteous','tag':'villain','limit':1}).json()
        self.assertEqual((trash['total'],trash['data'][0]['id']),(1,identity))
        self.assertEqual(self.f.client.get('/api/voices?q=courteous').json()['total'],0)
        self.assertEqual(self.f.client.get('/api/voice-tags').json()['data'],[{'name':'narrator','count':1}])
        self.assertEqual({r['name'] for r in self.f.client.get('/api/voice-tags?deleted=true').json()['data']},{'baritone','villain'})
        self.assertEqual(self.f.client.get(base).status_code,404)
        self.assertEqual(self.f.client.patch(base,json={'name':'Changed'}).status_code,404)
        self.assertEqual(self.f.client.get(selected['audio_url']).status_code,200)
        marker=voice_catalog.collection_marker(self.f.root,'voices',identity)
        self.assertEqual(json.loads(marker.read_text())['id'],identity)
        stamp=marker.stat().st_mtime_ns
        self.assertFalse(self.f.client.delete(base).json()['changed'])
        self.assertEqual(marker.stat().st_mtime_ns,stamp)
        self.assertEqual({p:p.read_bytes() for p in before},before)
        self.assertTrue(self.f.client.post(base+'/restore').json()['changed'])
        self.assertFalse(self.f.client.post(base+'/restore').json()['changed'])
        self.assertFalse(marker.exists())
        self.assertEqual(self.f.client.get('/api/voices').json()['total'],2)
        self.assertEqual(self.f.client.get(base).json()['name'],selected['name'])
        self.assertEqual(self.f.client.get('/api/voices?deleted=true').json()['total'],0)

    def test_deleting_a_voice_does_not_break_waiting_jobs_or_mutate_the_queue(self):
        voice=self.voice()
        with self.f.manager.lock_file.open('a+') as reservation:
            fcntl.flock(reservation,fcntl.LOCK_EX|fcntl.LOCK_NB)
            accepted=self.f.client.post('/api/generate',json={'voice_id':voice['id'],'text':'Welcome home.','clone_mode':'auto'})
            self.assertEqual(accepted.status_code,202,accepted.text)
            identity=accepted.json()['id'];before=self.f.manager.store.read(identity)
            self.assertEqual(self.f.client.delete('/api/voices/'+voice['id']).status_code,200)
            after=self.f.manager.store.read(identity)
            for field in ('status','payload','fingerprint','backend','sequence'):
                self.assertEqual(before[field],after[field],field)
        self.f.manager._wake.set()
        done=self.f.finish(accepted.json());self.assertEqual(done['status'],'done',done)
        self.assertEqual(self.f.engine.calls[-1][2],voice['transcript'])
        self.assertEqual(done['result']['text'],'Welcome home.')

    def test_history_deletion_keeps_custom_outputs_saved_voice_and_job_links(self):
        custom=self.f.root/'custom-audio'
        job=self.f.generate(output_dir=str(custom)).json();done=self.f.finish(job)
        self.assertEqual(done['status'],'done',done)
        artifact=done['result'];identity=artifact['id'];base='/api/artifacts/'+identity
        saved=self.f.client.post('/api/voices',json={'output_id':identity,'name':'A saved performance'}).json()
        audio=storage.asset_path('outputs',identity)
        paths=[audio,audio.with_suffix('.json'),self.f.root/'artifact-index'/f'{identity}.json']
        before={p:p.read_bytes() for p in paths}
        self.assertTrue(self.f.client.delete(base).json()['changed'])
        self.assertFalse(self.f.client.delete(base).json()['changed'])
        self.assertEqual(self.f.client.get('/api/history').json()['total'],0)
        deleted=self.f.client.get('/api/history',params={'deleted':True,'q':'Welcome','limit':1}).json()
        self.assertEqual((deleted['total'],deleted['data'][0]['id']),(1,identity))
        self.assertEqual(self.f.client.get(base).status_code,200)
        self.assertEqual(self.f.client.get(artifact['audio_url']).status_code,200)
        self.assertEqual(self.f.client.get('/api/jobs/'+job['id']).json()['status'],'done')
        self.assertEqual(self.f.client.get('/api/voices/'+saved['id']).status_code,200)
        self.assertEqual({p:p.read_bytes() for p in paths},before)
        # Metadata reconciliation cannot undo a separately persisted deletion.
        storage.persist_artifact(audio,artifact)
        self.assertEqual(storage.artifact_rows(),[])
        self.assertEqual([r['id'] for r in storage.artifact_rows(deleted=True)],[identity])
        self.assertTrue(self.f.client.post(base+'/restore').json()['changed'])
        self.assertFalse(self.f.client.post(base+'/restore').json()['changed'])
        self.assertEqual(self.f.client.get('/api/history').json()['total'],1)
        self.assertEqual(self.f.client.get('/api/history?deleted=true').json()['total'],0)

    def test_unknown_ids_storage_failures_and_symlinks_never_create_deletions(self):
        for kind in ('voices','artifacts'):
            for identity in ('not-an-id','f'*32):
                self.assertEqual(self.f.client.delete('/api/'+kind+'/'+identity).status_code,404)
                self.assertEqual(self.f.client.post('/api/'+kind+'/'+identity+'/restore').status_code,404)
        self.assertFalse((self.f.root/'collection-deletions').exists())
        voice=self.voice();base='/api/voices/'+voice['id']
        with patch('server.validate_mount',side_effect=RuntimeError('Fixture volume disconnected')):
            self.assertEqual(self.f.client.delete(base).status_code,422)
        self.assertFalse((self.f.root/'collection-deletions').exists())
        with patch('voice_catalog.atomic_json',side_effect=OSError('Fixture storage is read-only')):
            response=self.f.client.delete(base);self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.f.client.get(base).status_code,200)
        folder=self.f.root/'collection-deletions';folder.rmdir()
        outside=self.f.root/'outside';outside.mkdir();folder.symlink_to(outside,target_is_directory=True)
        response=self.f.client.delete(base);self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(list(outside.iterdir()),[])
        self.assertEqual(self.f.client.get(base).status_code,200)

    def test_generator_openapi_documents_collection_filters_and_lifecycle(self):
        schema=self.f.client.get('/openapi.json').json();paths=schema['paths']
        for root in ('voices','artifacts'):
            self.assertIn('delete',paths['/api/'+root+'/{identity}'])
            self.assertIn('post',paths['/api/'+root+'/{identity}/restore'])
        for path in ('/api/voices','/api/history','/api/voice-tags'):
            self.assertIn('deleted',{p['name'] for p in paths[path]['get']['parameters']})
        self.assertEqual(set(schema['components']['schemas']['CollectionChange']['required']),{'id','kind','deleted','changed'})
