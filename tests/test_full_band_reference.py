"""Every model clones from the retained original upload at full band, not the 16 kHz working copy."""
import json
from datetime import datetime, timezone, timedelta
from unittest import TestCase
from unittest.mock import patch

import soundfile as sf

from collection_cleanup import CollectionCleaner
from config import PROJECT_ROOT
import server
import voice_catalog as catalog
from . import test_contract


class FullBandReferenceTests(TestCase):
    def setUp(self):
        self.f=test_contract.ContractTests('runTest');self.f.setUp();self.addCleanup(self.f.tearDown)

    def conditioning(self, **source):
        job=self.f.finish(self.f.generate(**source).json())
        self.assertEqual(job['status'],'done',job)
        return self.f.engine.calls[-1][1]

    def test_wide_upload_conditions_on_its_full_band_original(self):
        reference=self.f.upload(rate=44100).json()['id']
        path=self.conditioning(reference_id=reference)
        self.assertEqual(path,self.f.root/'references/full'/f'{reference}.wav')
        self.assertEqual((sf.info(path).samplerate,sf.info(path).channels),(44100,1))
        self.assertEqual(sf.info(self.f.root/'references'/f'{reference}.wav').samplerate,16000,'the working copy stays 16 kHz')
        self.assertEqual(self.conditioning(reference_id=reference),path,'the cached copy is reused')

    def test_saved_voice_copied_from_a_reference_uses_that_original(self):
        reference=self.f.upload(rate=48000,channels=2).json()['id']
        voice=self.f.client.post('/api/voices',json={'reference_id':reference,'name':'Wide narrator'}).json()
        path=self.conditioning(voice_id=voice['id'])
        self.assertEqual(path,self.f.root/'references/full'/f'{reference}.wav')
        self.assertEqual((sf.info(path).samplerate,sf.info(path).channels),(48000,1))

    def test_narrow_or_very_wide_uploads(self):
        narrow=self.f.upload(rate=16000).json()['id']
        self.assertEqual(self.conditioning(reference_id=narrow),self.f.root/'references'/f'{narrow}.wav')
        wide=self.f.upload(rate=96000).json()['id']
        self.assertEqual(sf.info(self.conditioning(reference_id=wide)).samplerate,48000,'capped at 48 kHz')

    def test_every_cloning_backend_uses_the_full_band_original(self):
        reference=self.f.upload(rate=44100).json()['id']
        for backend in ['qwen3','fish','indextts','chatterbox','higgs']:
            with self.subTest(backend=backend):
                profile=json.loads((PROJECT_ROOT/'backends'/backend/'service.json').read_text())
                with patch('capabilities.PROFILE',profile),patch('server.PROFILE',profile):
                    # Fish and Higgs require the reference transcript; the torch backends refuse one.
                    transcript={'ref_text':'Welcome home.'} if backend in ('fish','higgs') else {}
                    language='English' if backend=='indextts' else 'auto'
                    effective,path,_,info=server.prepare_generation(server.Generation(text='Welcome home.',reference_id=reference,language=language,**transcript))
                self.assertEqual(path,self.f.root/'references/full'/f'{reference}.wav')
                self.assertTrue(info['full_band'])

    def test_cleanup_removes_the_full_band_copy_with_its_reference(self):
        reference=self.f.upload(rate=44100).json()['id']
        voice=self.f.client.post('/api/voices',json={'reference_id':reference,'name':'Disposable narrator'}).json()
        full=self.conditioning(voice_id=voice['id']);self.assertTrue(full.is_file())
        self.assertEqual(self.f.client.delete('/api/voices/'+voice['id']).status_code,200)
        marker=catalog.collection_marker(self.f.root,'voices',voice['id'])
        record=json.loads(marker.read_text())
        record['deleted_at']=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat();catalog.atomic_json(marker,record)
        CollectionCleaner(self.f.manager,self.f.root/'outputs').sweep()
        self.assertFalse(full.exists())
