"""Pinned helper assets are checked offline and safe setup reruns reuse their weights."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from config import PROJECT_ROOT
import transcription_assets as assets
import setup_transcription as setup


class TranscriptionAssetsTests(unittest.TestCase):
    def setUp(self):
        parent=PROJECT_ROOT/'state/validation';parent.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=parent,prefix='asr-assets-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.model=self.root/assets.SPEC['filename'];self.model.write_bytes(b'pin')
        self.binary=self.root/'whisper-cli';self.binary.write_text('fixture');self.binary.chmod(0o700)
        self.record=self.root/'build.json';self.record.write_text(json.dumps({'revision':assets.SPEC['source_revision'],'cpu_only':True}))
        (self.root/'verified.json').write_text(json.dumps({'revision':assets.SPEC['revision'],'sha256':hashlib.sha256(b'pin').hexdigest()}))
    def test_offline_checks_verify_cpu_build_size_and_checksum(self):
        with patch.object(assets,'BINARY',self.binary),patch.object(assets,'BUILD_RECORD',self.record),patch.object(assets,'model_directory',return_value=self.root),patch.dict(assets.SPEC,bytes=3,sha256=hashlib.sha256(b'pin').hexdigest()):
            self.assertEqual(assets.check_ready(full=True),(self.binary,self.model))
            self.model.write_bytes(b'bad')
            with self.assertRaises(RuntimeError):assets.check_ready(full=True)
            self.model.write_bytes(b'pin');self.record.write_text(json.dumps({'revision':assets.SPEC['source_revision'],'cpu_only':False}))
            with self.assertRaises(RuntimeError):assets.check_ready()
    def test_ready_setup_is_a_noop_for_downloads_and_builds(self):
        source=self.root/'source';source.mkdir()
        def git(args,**kwargs):return setup.SPEC['source_revision']+'\n' if args[-1]=='HEAD' else ''
        with patch.object(setup,'SOURCE',source),patch.object(setup,'model_directory',return_value=self.root),patch.dict(setup.SPEC,bytes=3,sha256=hashlib.sha256(b'pin').hexdigest()),patch.object(setup,'check_ready',return_value=(self.binary,self.model)),patch.object(setup.subprocess,'check_output',side_effect=git),patch.object(setup.subprocess,'run') as command,patch.object(setup.urllib.request,'urlopen') as download:
            setup.setup();command.assert_not_called();download.assert_not_called()
    def test_unexpected_source_is_preserved_and_not_checked_out_or_built(self):
        source=self.root/'source';source.mkdir();marker=source/'keep';marker.write_text('local changes')
        with patch.object(setup,'SOURCE',source),patch.object(setup,'model_directory',return_value=self.root),patch.dict(setup.SPEC,bytes=3,sha256=hashlib.sha256(b'pin').hexdigest()),patch.object(setup.subprocess,'check_output',return_value='unexpected\n'),patch.object(setup.subprocess,'run') as command:
            with self.assertRaises(RuntimeError):setup.setup()
            command.assert_not_called();self.assertEqual(marker.read_text(),'local changes')
