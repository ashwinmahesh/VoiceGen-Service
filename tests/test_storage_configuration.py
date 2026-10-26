"""Portable setup and preservation of external data are part of the setup contract."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from configure_storage import configure


class StorageConfigurationTest(unittest.TestCase):
    def test_plain_checkout_defaults_work_without_external_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            result = configure(project, project, project/'gpu.lock')
            self.assertEqual(result['data_dir'], str(project))
            self.assertFalse((project/'weights').is_symlink())

    def test_external_setup_links_data_and_is_safe_to_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            project, data = root/'project', root/'external'
            project.mkdir(); data.mkdir()
            (data/'voices').mkdir(); (data/'voices/keep.txt').write_text('saved voice')
            configure(project, data, root/'shared/gpu.lock')
            configure(project, data, root/'shared/gpu.lock')
            self.assertTrue((project/'weights').is_symlink())
            self.assertEqual((project/'voices/keep.txt').read_text(), 'saved voice')
            self.assertEqual(json.loads((project/'storage.json').read_text())['data_dir'], str(data))

    def test_existing_data_collision_is_preserved_before_settings_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); project = root/'project'; project.mkdir()
            (project/'voices').mkdir(); (project/'voices/keep.txt').write_text('do not overwrite')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                configure(project, root/'external', root/'gpu.lock')
            self.assertEqual((project/'voices/keep.txt').read_text(), 'do not overwrite')
            self.assertFalse((project/'storage.json').exists())
            self.assertFalse((project/'weights').is_symlink())

    def test_setup_retry_preserves_explicit_backend_asset_mappings(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp).resolve()
            mappings={'backend_weights':{'qwen3':'/absolute/other/weights'},
                      'backend_extensions':{'qwen3':'/absolute/other/extensions'}}
            (project/'storage.json').write_text(json.dumps(mappings))
            configure(project,project,project/'gpu.lock')
            settings=json.loads((project/'storage.json').read_text())
            for key,value in mappings.items():self.assertEqual(settings[key],value)

    def test_unavailable_volume_is_reported_before_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('configure_storage.validate_mount', side_effect=RuntimeError('Storage volume is not mounted')):
                with self.assertRaisesRegex(RuntimeError, 'not mounted'):
                    configure(Path(tmp), '/Volumes/unavailable/models/voice', Path(tmp)/'gpu.lock')
            self.assertFalse((Path(tmp)/'storage.json').exists())
