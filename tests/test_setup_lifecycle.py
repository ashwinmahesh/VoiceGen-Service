"""Setup cancellation and download reuse, with isolated fake installers/assets."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from config import PROJECT_ROOT
from setup_lifecycle import SetupCancelled, run_setup


class SetupLifecycleTests(unittest.TestCase):
    def setUp(self):
        parent=PROJECT_ROOT/'state/validation'
        parent.mkdir(parents=True,exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(prefix='setup-cancel-',dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)

    def test_cancel_menu_does_not_select_or_launch_model(self):
        from startup_select import choose_model
        for answer in ('q','Q','quit','cancel','exit'):
            output=io.StringIO()
            with self.subTest(answer=answer),self.assertRaises(SetupCancelled):
                choose_model('higgs',interactive=True,input_fn=lambda:answer,stream=output)
            self.assertIn('q. Cancel setup (or press Ctrl+C)',output.getvalue())
        # Serving retains its existing numeric/default choices; q is not setup cancellation.
        answers=iter(['q','1'])
        output=io.StringIO()
        self.assertEqual(choose_model('higgs',interactive=True,input_fn=lambda:next(answers),
                                     stream=output,serving=True),'higgs')
        self.assertNotIn('Cancel setup',output.getvalue())

    def test_setup_cli_cancels_cleanly_but_does_not_hide_errors(self):
        import startup_select
        for error in (SetupCancelled(),KeyboardInterrupt(),EOFError()):
            output=io.StringIO()
            with patch.object(sys,'argv',['startup_select.py','setup']), \
                 patch('startup_select.main',side_effect=error),contextlib.redirect_stderr(output):
                self.assertEqual(startup_select.cli(),0)
            self.assertIn('Setup cancelled',output.getvalue())
            self.assertIn('downloads and saved data are kept',output.getvalue())
        with patch.object(sys,'argv',['startup_select.py','setup']), \
             patch('startup_select.main',side_effect=RuntimeError('installer failed')):
            with self.assertRaisesRegex(RuntimeError,'installer failed'):startup_select.cli()
        with patch.object(sys,'argv',['startup_select.py','serve']), \
             patch('startup_select.main',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):startup_select.cli()

    def test_setup_failure_exit_status_reaches_make(self):
        import startup_select
        with patch.object(sys,'argv',['startup_select.py','setup','--model','higgs','--non-interactive']), \
             patch('startup_select.os.chdir'),patch('startup_select.run_setup',return_value=23), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:startup_select.cli()
        self.assertEqual(error.exception.code,23)
        self.assertEqual(run_setup([sys.executable,'-c','raise SystemExit(23)'],dict(os.environ),self.root),23)

    def test_child_inherits_input_for_storage_prompt(self):
        driver=self.root/'driver.py'
        driver.write_text('import os,sys\nfrom setup_lifecycle import run_setup\n'
                          'raise SystemExit(run_setup([sys.executable,"-c",'
                          '\'print("STORAGE="+input())\'],dict(os.environ),sys.argv[1]))\n')
        result=subprocess.run([sys.executable,str(driver),str(self.root)],input='my-ssd-folder\n',
            env={**os.environ,'PYTHONPATH':str(PROJECT_ROOT)},capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('STORAGE=my-ssd-folder',result.stdout)

    def wait_for_output(self,process,expected,timeout=8):
        deadline=time.monotonic()+timeout
        received=b''
        while expected not in received:
            remaining=deadline-time.monotonic()
            if remaining<=0:self.fail(f'Waiting for {expected!r}: {received!r}')
            readable,_,_=select.select([process.stdout],[],[],remaining)
            if readable:
                block=os.read(process.stdout.fileno(),4096)
                if not block:self.fail(f'Process exited before {expected!r}: {received!r}')
                received+=block
        return received

    def test_interrupt_stops_only_setup_group_and_preserves_downloads(self):
        verified=self.root/'verified-weights.bin';verified.write_bytes(b'verified-model-fixture')
        installer=self.root/'installer.py'
        installer.write_text('''import os,signal,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
def stop(*args):
    child.terminate()
    child.wait(timeout=3)
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
Path('installer-pids.json').write_text(__import__('json').dumps([os.getpid(),child.pid]))
Path('weights.partial').write_bytes(b'resumable-fixture')
print('INSTALLER_READY',flush=True)
while True:time.sleep(.1)
''')
        driver=self.root/'driver.py'
        driver.write_text('''import os,sys
from setup_lifecycle import run_setup,announce_cancelled
try:
    raise SystemExit(run_setup([sys.executable,sys.argv[1]],dict(os.environ),sys.argv[2]))
except KeyboardInterrupt:
    announce_cancelled()
''')
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
        process=subprocess.Popen([sys.executable,str(driver),str(installer),str(self.root)],
            env={**os.environ,'PYTHONPATH':str(PROJECT_ROOT)},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            start_new_session=True)
        pids=[]
        try:
            prefix=self.wait_for_output(process,b'INSTALLER_READY')
            pids=json.loads((self.root/'installer-pids.json').read_text())
            os.kill(process.pid,signal.SIGINT)
            output=prefix+process.communicate(timeout=10)[0]
            self.assertEqual(process.returncode,0,output.decode())
            self.assertIn(b'Setup cancelled',output)
            self.assertNotIn(b'Traceback',output)
            for pid in pids:
                with self.assertRaises(ProcessLookupError):os.kill(pid,0)
            self.assertIsNone(unrelated.poll())
            self.assertEqual(verified.read_bytes(),b'verified-model-fixture')
            self.assertEqual((self.root/'weights.partial').read_bytes(),b'resumable-fixture')
        finally:
            if process.poll() is None:process.kill()
            process.communicate(timeout=5)
            unrelated.terminate();unrelated.wait(timeout=5)
            for pid in pids:
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass

    def test_verified_download_is_reused_without_downloader(self):
        import assets,download
        content=b'pinned-model-fixture'
        folder=self.root/'fixture';folder.mkdir()
        model=folder/'weights.bin';model.write_bytes(content)
        specs={'fixture':{'repo':'fixture/model','revision':'pinned','subdir':'fixture',
                'files':[{'path':'weights.bin','size':len(content),'sha256':hashlib.sha256(content).hexdigest()}]}}
        with patch.multiple(assets,MODEL_DIR=self.root,MODEL_SPECS=specs), \
             patch.multiple(download,MODEL_DIR=self.root,MODEL_SPECS=specs), \
             patch('download.prepare_storage'),patch('download.os.environ',dict(os.environ)):
            self.assertEqual(assets.verify_assets('fixture',full=True),[])
            before=model.stat().st_mtime_ns
            output=io.StringIO()
            with patch.dict(sys.modules,{'huggingface_hub':None}),contextlib.redirect_stdout(output):
                download.download_models()
            self.assertIn('already downloaded and verified; reusing in place (no-op download)',output.getvalue())
            self.assertEqual(model.read_bytes(),content)
            self.assertEqual(model.stat().st_mtime_ns,before)

    def test_repeated_interrupt_still_cleans_up_stubborn_installer(self):
        installer=self.root/'stubborn.py'
        installer.write_text('import os,signal,time\n'
            'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
            'print("READY="+str(os.getpid()),flush=True)\ntime.sleep(60)\n')
        code=('import os,sys\nfrom setup_lifecycle import run_setup,announce_cancelled\n'
              'try:run_setup([sys.executable,sys.argv[1]],dict(os.environ),sys.argv[2])\n'
              'except KeyboardInterrupt:announce_cancelled()\n')
        process=subprocess.Popen([sys.executable,'-c',code,str(installer),str(self.root)],
            env={**os.environ,'PYTHONPATH':str(PROJECT_ROOT)},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            start_new_session=True)
        pid=None
        try:
            prefix=self.wait_for_output(process,b'READY=')
            pid=int(prefix.decode().split('READY=')[1].strip())
            os.kill(process.pid,signal.SIGINT)
            time.sleep(.15)
            os.kill(process.pid,signal.SIGINT)
            output=prefix+process.communicate(timeout=7)[0]
            self.assertEqual(process.returncode,0,output.decode())
            self.assertNotIn(b'Traceback',output)
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
        finally:
            if process.poll() is None:process.kill()
            process.communicate(timeout=5)
            if pid:
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass

    def test_incomplete_download_retries_pinned_files_in_place(self):
        import assets,download
        content=b'complete-model-fixture'
        specs={'fixture':{'repo':'fixture/model','revision':'pinned','subdir':'fixture',
                'files':[{'path':'weights.bin','size':len(content),'sha256':hashlib.sha256(content).hexdigest()}]}}
        folder=self.root/'fixture';folder.mkdir()
        model=folder/'weights.bin';model.write_bytes(b'incomplete')
        calls=[]
        def finish(**kwargs):
            calls.append(kwargs)
            model.write_bytes(content)
        with patch.multiple(assets,MODEL_DIR=self.root,MODEL_SPECS=specs), \
             patch.multiple(download,MODEL_DIR=self.root,MODEL_SPECS=specs), \
             patch('download.prepare_storage'),contextlib.redirect_stdout(io.StringIO()):
            download.download_models(downloader=finish)
            download.download_models(downloader=finish)
            self.assertEqual(assets.verify_assets('fixture'),[])
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['revision'],'pinned')
        self.assertEqual(calls[0]['local_dir'],str(folder))


if __name__=='__main__':unittest.main()
