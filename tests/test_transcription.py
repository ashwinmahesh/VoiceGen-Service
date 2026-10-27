"""Automatic upload transcripts, CPU process lifetime and recovery without inference weights."""
import asyncio
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
from config import PROJECT_ROOT
from tests import test_contract
from tests.test_contract import wav
import transcription

RECEIPT={'engine':'whisper.cpp','model':'Whisper base multilingual Q5_1','language':'en','seconds':.1,'device':'CPU'}


class UploadTranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.f=test_contract.ContractTests();self.f.setUp();self.addCleanup(self.f.tearDown)
    def upload(self,**fields):
        return self.f.client.post('/api/voices/upload',files={'file':('spoken.wav',wav(),'audio/wav')},data={'name':'Narrator',**fields})
    def test_voice_upload_automatically_saves_transcript_and_provenance(self):
        with patch('server.transcribe_reference',new=AsyncMock(return_value=('My guests. You came.',RECEIPT))) as run:
            response=self.upload()
        self.assertEqual(response.status_code,201,response.text);row=response.json()
        self.assertEqual(row['transcript'],'My guests. You came.');self.assertEqual(row['transcription']['device'],'CPU')
        self.assertEqual(run.await_count,1)
        ref=json.loads((self.f.root/'references'/f'{row["source_id"]}.json').read_text())
        self.assertEqual(ref['transcript'],row['transcript'])
        edited=self.f.client.patch('/api/voices/'+row['id'],json={'transcript':'My guests. Please sit.'}).json()
        self.assertEqual(edited['transcript'],'My guests. Please sit.');self.assertIsNone(edited['transcription'])
    def test_explicit_words_and_opt_out_never_start_the_transcriber(self):
        with patch('server.transcribe_reference',new=AsyncMock(side_effect=AssertionError('must not run'))) as run:
            supplied=self.upload(transcript='Written exact words.')
            skipped=self.upload(transcribe='false')
        self.assertEqual(supplied.status_code,201,supplied.text);self.assertEqual(supplied.json()['transcript'],'Written exact words.')
        self.assertEqual(skipped.status_code,201,skipped.text);self.assertEqual(skipped.json()['transcript'],'');run.assert_not_awaited()
    def test_reference_preview_is_opt_in_and_edited_words_are_saved(self):
        with patch('server.transcribe_reference',new=AsyncMock(return_value=('I reina.',RECEIPT))) as run:
            legacy=self.f.upload();self.assertEqual(legacy.status_code,201);run.assert_not_awaited()
            preview=self.f.client.post('/api/references',files={'file':('speech.wav',wav())},data={'transcribe':'true','transcription_language':'en'})
        self.assertEqual(preview.status_code,201,preview.text)
        self.assertEqual(run.await_args.args[2],'en')
        saved=self.f.client.post('/api/voices',json={'reference_id':preview.json()['id'],'name':'Count','transcript':'Ireena.'})
        self.assertEqual(saved.status_code,201,saved.text);self.assertEqual(saved.json()['transcript'],'Ireena.');self.assertIsNone(saved.json()['transcription'])
    def test_transcription_failure_rolls_back_only_this_new_upload(self):
        old=self.f.upload().json();before=set((self.f.root/'references').rglob('*'))
        with patch('server.transcribe_reference',new=AsyncMock(side_effect=transcription.TranscriptionError('Run make setup-transcriber'))):
            response=self.upload()
        self.assertEqual(response.status_code,503,response.text);self.assertIn('setup-transcriber',response.json()['detail'])
        self.assertEqual(set((self.f.root/'references').rglob('*')),before)
        self.assertEqual(self.f.client.get(old['audio_url']).status_code,200)
        self.assertEqual(self.f.client.get('/api/voices').json()['total'],0)
    def test_saving_failure_removes_automatically_transcribed_reference(self):
        with patch('server.transcribe_reference',new=AsyncMock(return_value=('Hello.',RECEIPT))),patch('server.save_voice',side_effect=OSError('write failed')):
            response=self.upload()
        self.assertEqual(response.status_code,422,response.text)
        self.assertFalse(list((self.f.root/'references').glob('*.wav')))
    def test_openapi_and_readiness_describe_the_default_and_offline_failure(self):
        schema=self.f.client.get('/openapi.json').json();operation=schema['paths']['/api/voices/upload']['post']
        name=operation['requestBody']['content']['multipart/form-data']['schema']['$ref'].split('/')[-1]
        self.assertTrue(schema['components']['schemas'][name]['properties']['transcribe']['default'])
        self.assertIn('/api/transcription',schema['paths'])
        with patch('transcription_assets.check_ready',side_effect=RuntimeError('Not installed')):
            status=self.f.client.get('/api/transcription').json()
        self.assertFalse(status['available']);self.assertEqual(status['device'],'CPU')


class TranscriberProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        parent=PROJECT_ROOT/'state/validation';parent.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=parent,prefix='transcription-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'state').mkdir();self.audio=self.root/'speech.wav';self.audio.write_bytes(wav())
        self.binary=self.root/'transcriber';self.model=self.root/'weights';self.model.write_bytes(b'fixture')
        self.ready=patch('transcription.check_ready',return_value=(self.binary,self.model));self.ready.start();self.addCleanup(self.ready.stop)
        self.project=patch('transcription.PROJECT_ROOT',self.root);self.project.start();self.addCleanup(self.project.stop)
    def worker(self,body):
        self.binary.write_text('#!'+sys.executable+'\nimport sys,json,os,time\nfrom pathlib import Path\n'+body+'\n');self.binary.chmod(0o700)
    async def test_cpu_arguments_result_and_scratch_cleanup(self):
        self.worker("Path(sys.argv[sys.argv.index('--file')+1]).with_suffix('.args').write_text(json.dumps(sys.argv))\nPath(sys.argv[sys.argv.index('--output-file')+1]+'.json').write_text(json.dumps({'transcription':[{'text':' Hello.'},{'text':' Welcome back.'}],'result':{'language':'en'}}))")
        self.binary.write_text(self.binary.read_text()+"""
audio=Path(sys.argv[sys.argv.index('--file')+1])
lease=(audio.parent/'state/transcription.lock').stat()
received=False
for descriptor in range(3,256):
 try:
  candidate=os.fstat(descriptor)
  received=received or (candidate.st_dev,candidate.st_ino)==(lease.st_dev,lease.st_ino)
 except OSError:pass
audio.with_suffix('.lease').write_text(json.dumps(received))
""")
        text,receipt=await transcription.transcribe_reference(self.audio,self.root,'en')
        self.assertEqual(text,'Hello. Welcome back.');self.assertEqual(receipt['device'],'CPU')
        args=json.loads(self.audio.with_suffix('.args').read_text());self.assertIn('--no-gpu',args);self.assertEqual(args[args.index('--threads')+1],'4')
        self.assertTrue(json.loads(self.audio.with_suffix('.lease').read_text()))
        self.assertFalse(list((self.root/'cache/tmp').iterdir()))
    async def test_busy_lock_and_invalid_language_do_not_launch_a_process(self):
        with (self.root/'state/transcription.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(transcription.TranscriptionError) as caught:await transcription.transcribe_reference(self.audio,self.root)
            self.assertEqual(caught.exception.status,409)
        with self.assertRaises(transcription.TranscriptionError) as caught:await transcription.transcribe_reference(self.audio,self.root,'../bad')
        self.assertEqual(caught.exception.status,422)
    async def test_empty_invalid_and_failed_outputs_are_actionable(self):
        for body,status in [("raise SystemExit(1)",503),("Path(sys.argv[sys.argv.index('--output-file')+1]+'.json').write_text('broken')",503),("Path(sys.argv[sys.argv.index('--output-file')+1]+'.json').write_text(json.dumps({'transcription':[]}))",422)]:
            self.worker(body)
            with self.assertRaises(transcription.TranscriptionError) as caught:await transcription.transcribe_reference(self.audio,self.root)
            self.assertEqual(caught.exception.status,status)
            self.assertFalse(list((self.root/'cache/tmp').iterdir()))
    async def test_timeout_and_disconnect_reap_owned_process_and_release_lock(self):
        self.worker("Path(sys.argv[sys.argv.index('--file')+1]).with_suffix('.pid').write_text(str(os.getpid()))\ntime.sleep(30)")
        with patch.dict(transcription.SPEC,timeout_seconds=.1):
            with self.assertRaises(transcription.TranscriptionError) as caught:await transcription.transcribe_reference(self.audio,self.root)
            self.assertEqual(caught.exception.status,504)
        pid=int(self.audio.with_suffix('.pid').read_text())
        with self.assertRaises(ProcessLookupError):os.kill(pid,0)
        self.audio.with_suffix('.pid').unlink()
        async def disconnected():return self.audio.with_suffix('.pid').exists()
        with self.assertRaises(transcription.TranscriptionError) as caught:await transcription.transcribe_reference(self.audio,self.root,disconnected=disconnected)
        self.assertEqual(caught.exception.status,499)
        pid=int(self.audio.with_suffix('.pid').read_text())
        with self.assertRaises(ProcessLookupError):os.kill(pid,0)
        with (self.root/'state/transcription.lock').open('a+') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
