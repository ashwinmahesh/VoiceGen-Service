"""Read-only, bounded, per-job log attribution without inference or private media."""
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from config import PROJECT_ROOT
from job_logs import job_log, MAX_LOG_BYTES


class JobLogFixture(unittest.TestCase):
    def setUp(self):
        parent=PROJECT_ROOT/'state/validation';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=parent,prefix='job-logs-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'logs').mkdir()
        self.row=dict(id='a'*32,backend='higgs',attempts=1,status='running',error=None,
                      started_at=datetime.fromtimestamp(time.time()-5,timezone.utc).isoformat())
        self.path=self.root/'logs'/f"native-{self.row['id']}.log"


class JobLogTests(JobLogFixture):
    def test_existing_native_output_is_read_live_and_after_completion(self):
        for backend in ('higgs','moss'):
            self.row['backend']=backend
            self.path.write_text('Loading model\n')
            self.assertEqual(job_log(self.row,self.root)['text'],'Loading model\n')
            with self.path.open('a') as stream:stream.write('Synthesis complete\n')
            self.row['status']='done'
            result=job_log(self.row,self.root)
            self.assertTrue(result['available']);self.assertIn('Synthesis complete',result['text'])
            self.assertFalse(result['truncated'])

    def test_tail_is_bounded_and_omits_the_partial_first_line(self):
        self.path.write_bytes(b'first secret from earlier output\n'+b'x'*(MAX_LOG_BYTES+100)+b'\nLast diagnostic\n')
        result=job_log(self.row,self.root)
        self.assertTrue(result['truncated']);self.assertEqual(result['text'],'Last diagnostic\n')
        self.assertLessEqual(len(result['text'].encode()),MAX_LOG_BYTES)

    def test_other_models_do_not_receive_unattributed_shared_logs(self):
        self.path.write_text('Native output from another backend')
        (self.root/'logs/backend.log').write_text('Shared output')
        for backend in ('voxcpm2','qwen3','fish','chatterbox','indextts'):
            result=job_log({**self.row,'backend':backend},self.root)
            self.assertFalse(result['available']);self.assertEqual(result['text'],'')

    def test_old_attempt_or_stale_file_never_returns_current_output(self):
        self.path.write_text('Attempt 2 only')
        row={**self.row,'attempts':2}
        result=job_log(row,self.root,1)
        self.assertFalse(result['available']);self.assertNotIn('Attempt 2 only',result['text'])
        os.utime(self.path,(1,1))
        self.assertFalse(job_log(row,self.root,2)['available'])
        with self.assertRaises(FileNotFoundError):job_log(row,self.root,3)

    def test_queued_missing_and_disconnected_logs_are_empty_without_creating_folders(self):
        queued=job_log({**self.row,'attempts':0,'started_at':None,'status':'queued'},self.root)
        self.assertEqual(queued['attempt'],0);self.assertFalse(queued['available'])
        self.assertFalse(job_log(self.row,self.root)['available'])
        missing=self.root/'disconnected'
        self.assertFalse(job_log(self.row,missing)['available']);self.assertFalse(missing.exists())

    def test_symlinks_fifos_and_metadata_paths_cannot_expose_other_files(self):
        private=self.root/'other.txt';private.write_text('UNRELATED FILE')
        self.row['result']={'higgs':{'native_log_path':str(private)}}
        self.assertNotIn('UNRELATED FILE',job_log(self.row,self.root)['text'])
        self.path.symlink_to(private)
        self.assertFalse(job_log(self.row,self.root)['available'])
        self.path.unlink();os.mkfifo(self.path)
        start=time.monotonic();self.assertFalse(job_log(self.row,self.root)['available'])
        self.assertLess(time.monotonic()-start,.5)

    def test_terminal_controls_are_removed_but_text_remains_inert_for_the_ui(self):
        self.path.write_text('\x1b[31mFailed\x1b[0m\r\x1b]0;title\x07<script>example</script>\x00\n')
        result=job_log(self.row,self.root)
        self.assertEqual(result['text'],'Failed\n<script>example</script>\n')


class JobLogAPITests(JobLogFixture):
    def setUp(self):
        super().setUp()
        import server
        from jobs import JobManager
        from unittest.mock import Mock
        self.manager=JobManager(Mock(),self.root,self.root/'gpu.lock',db_path=self.root/'queue.sqlite3')
        self.manager._ready()
        self.manager.store.admit_opaque('higgs',{'text':'Benign fixture'})
        self.row=self.manager.store.claim_global(['higgs'])
        self.path=self.root/'logs'/f"native-{self.row['id']}.log"
        self.client=TestClient(server.app)
        self.patch=patch('server.manager',self.manager);self.patch.start();self.addCleanup(self.patch.stop)
        self.addCleanup(self.manager.executor.shutdown)

    def test_api_available_unknown_jobs_bad_attempts_and_openapi(self):
        self.path.write_text('Generation started\n')
        endpoint='/api/jobs/'+self.row['id']+'/logs'
        response=self.client.get(endpoint);self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['available']);self.assertIn('Generation started',response.json()['text'])
        self.assertEqual(self.client.get('/api/jobs/'+'f'*32+'/logs').status_code,404)
        self.assertEqual(self.client.get('/api/jobs/invalid/logs').status_code,404)
        self.assertEqual(self.client.get(endpoint+'?attempt=0').status_code,422)
        self.assertEqual(self.client.get(endpoint+'?attempt=2').status_code,404)
        self.assertIn('/api/jobs/{identity}/logs',self.client.get('/openapi.json').json()['paths'])

    def test_racing_retry_clears_the_previous_attempt_output(self):
        self.path.write_text('Do not mislabel this output')
        with patch.object(self.manager.store,'read',side_effect=[self.row,{**self.row,'attempts':2}]):
            result=self.client.get('/api/jobs/'+self.row['id']+'/logs').json()
        self.assertFalse(result['available']);self.assertEqual(result['text'],'')
        self.assertIn('changed attempts',result['notice'])

    def test_storage_failure_remains_a_retryable_json_error(self):
        with patch.object(self.manager.store,'read',side_effect=sqlite3.OperationalError('private detail')):
            response=self.client.get('/api/jobs/'+self.row['id']+'/logs')
        self.assertEqual(response.status_code,503);self.assertEqual(response.headers['Retry-After'],'2')
        self.assertNotIn('private detail',response.text)


if __name__=='__main__':unittest.main()
