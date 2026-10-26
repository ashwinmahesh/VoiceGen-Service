"""Exercise the real make entry point without environments, weights, or listeners."""
import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from config import PROJECT_ROOT


class ServeSetupGateTests(unittest.TestCase):
    def setUp(self):
        parent=PROJECT_ROOT/'state/validation';parent.mkdir(parents=True,exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(prefix='serve-setup-',dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        # A source-only checkout: copy tracked files, never local settings, venvs or data.
        tracked=subprocess.check_output(['git','ls-files','-z'],cwd=PROJECT_ROOT).decode().split('\0')
        for name in filter(None,tracked):
            source=PROJECT_ROOT/name
            if not source.is_file() or source.is_symlink():continue
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
        (self.root/'server.py').write_text("from pathlib import Path\nPath('generator-started').touch()\nraise SystemExit('Unexpected generator launch')\n")
        self.env=os.environ.copy()
        for name in ['VENV','MODEL','MODEL_BACKEND','VOICEGEN_OVERRIDE_BACKEND','QUEUE_DB','DATA_DIR','WEIGHTS_DIR',
                     'EXTENSIONS_DIR','PROMPT_LLM_DIR','PYTHONPATH','MAKEFLAGS','MFLAGS','MAKEOVERRIDES','MAKELEVEL']:
            self.env.pop(name,None)
        self.env['BOOTSTRAP_PYTHON']=sys.executable

    def assert_setup_failure(self,result):
        message=result.stdout+result.stderr
        self.assertNotEqual(result.returncode,0,message)
        self.assertIn('make setup',message)
        self.assertNotIn('Traceback',message)
        self.assertNotIn('Enable local prompt expansion?',message)
        self.assertNotIn('Choose the default voice model',message)
        self.assertFalse((self.root/'generator-started').exists())
        self.assertFalse((self.root/'state').exists())
        self.assertFalse((self.root/'.bootstrap').exists())
        self.assertFalse((self.root/'.python').exists())

    def test_make_serve_rejects_a_clean_checkout_in_every_startup_mode(self):
        for options in [[],['NONINTERACTIVE=1'],['MODEL=higgs'],['MODEL=voxcpm2','NO_LLM=1']]:
            with self.subTest(options=options):
                result=subprocess.run(['make','serve',*options],cwd=self.root,env=self.env,input='',
                                      capture_output=True,text=True,timeout=15)
                self.assert_setup_failure(result)

    def test_interactive_make_serve_fails_before_asking_any_questions(self):
        master,slave=pty.openpty()
        try:
            process=subprocess.Popen(['make','serve'],cwd=self.root,env=self.env,stdin=slave,
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                out,err=process.communicate(timeout=15)
            finally:
                if process.poll() is None:process.kill();process.wait()
            self.assert_setup_failure(subprocess.CompletedProcess(process.args,process.returncode,out,err))
            self.assertIn('at least one model',out+err)
        finally:os.close(master);os.close(slave)

    def test_python_environment_without_model_download_is_not_a_complete_setup(self):
        # Reuse only the test interpreter; real readiness checks must reject unfinished setup.
        python=self.root/'.venv/bin/python';python.parent.mkdir(parents=True);python.symlink_to(sys.executable)
        result=subprocess.run(['make','serve','MODEL=voxcpm2','NO_LLM=1'],cwd=self.root,env=self.env,
                              input='',capture_output=True,text=True,timeout=15)
        self.assert_setup_failure(result)
        self.assertIn('make setup MODEL=voxcpm2',result.stdout+result.stderr)

    def test_malformed_readiness_results_cannot_count_as_completed_setup(self):
        import startup_select
        python=self.root/'.venv/bin/python';python.parent.mkdir(parents=True);python.symlink_to(sys.executable)
        for value in [None,{},False,'ready',[None],['']]:
            response=subprocess.CompletedProcess([],0,json.dumps(value),'')
            with self.subTest(value=value),patch('startup_select.subprocess.run',return_value=response):
                self.assertIn('invalid readiness response',startup_select.setup_errors(self.root,'voxcpm2','voxcpm2')[0])
        with patch('startup_select.subprocess.run',return_value=subprocess.CompletedProcess([],0,'[]','')):
            self.assertEqual(startup_select.setup_errors(self.root,'voxcpm2','voxcpm2'),[])


if __name__=='__main__':unittest.main()
