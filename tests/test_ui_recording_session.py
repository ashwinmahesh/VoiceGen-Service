"""Browser recovery state and polling, without speech inference or browser dependencies."""
from pathlib import Path
import shutil
import subprocess
import unittest


class RecordingSessionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is optional; used only for browser logic tests')
    def test_recording_session_behavior(self):
        result = subprocess.run([shutil.which('node'), '--test', 'tests/recording_session.cjs'],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
