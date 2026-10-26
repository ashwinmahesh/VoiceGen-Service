"""Interpreter selection and bootstrap regression tests; no real downloads/inference."""
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import python_runtime as runtime

PROJECT = Path(__file__).resolve().parents[1]


def info(minor=12, **overrides):
    value = dict(version=[3, minor, 0], implementation='cpython', system='Darwin', machine='arm64',
                 free_threaded=False, path='/installed/python', base='/installed/python')
    value.update(overrides)
    return value


class PythonRuntimeTests(unittest.TestCase):
    def setUp(self):
        parent = PROJECT / 'state/validation'; parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='python-runtime-', dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / '.venv-test'
        self.enterContext(patch('python_runtime.platform.system', return_value='Darwin'))
        self.enterContext(patch('python_runtime.platform.machine', return_value='arm64'))
        self.enterContext(redirect_stderr(io.StringIO()))

    def test_dependency_bounds_differ_by_runtime(self):
        for role, supported in [('mlx', {12, 13, 14}), ('enhancer', {12, 13, 14}),
                                ('native', {11, 12}), ('chatterbox', {11, 12}),
                                ('indextts', {11}), ('queue', {11, 12, 13, 14})]:
            for minor in range(9, 16):
                self.assertEqual(runtime.compatible(role, info(minor)), minor in supported, (role, minor))
        for key, value in [('implementation', 'pypy'), ('machine', 'x86_64'),
                           ('system', 'Linux'), ('free_threaded', True)]:
            value_info = info(); value_info[key] = value
            self.assertFalse(runtime.compatible('mlx', value_info))
        self.assertFalse(runtime.compatible('mlx', None))

    def test_reuses_existing_environment_without_installing_or_probing_other_candidates(self):
        self.target.mkdir(); (self.target / 'keep').write_text('preserve')
        with patch.object(runtime, 'probe_python', return_value=info()), \
             patch.object(runtime, 'installed_pythons') as candidates, \
             patch.object(runtime, 'managed_python') as download, \
             patch.object(runtime.subprocess, 'run') as run:
            self.assertEqual(runtime.ensure_environment(self.root, 'native', self.target), self.target / 'bin/python')
            candidates.assert_not_called(); download.assert_not_called(); run.assert_not_called()
        self.assertEqual((self.target / 'keep').read_text(), 'preserve')

    def test_incompatible_or_broken_existing_environment_is_preserved(self):
        self.target.mkdir(); (self.target / 'keep').write_text('preserve')
        for detected in [None, info(14)]:
            with patch.object(runtime, 'probe_python', return_value=detected), \
                 patch.object(runtime, 'managed_python') as download, patch.object(runtime.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'preserved'):
                    runtime.ensure_environment(self.root, 'native', self.target)
                download.assert_not_called(); run.assert_not_called()
        self.assertEqual((self.target / 'keep').read_text(), 'preserve')

    def test_selects_compatible_installed_python_and_skips_incompatible_default(self):
        def detect(path):
            return info(14, base='/default') if str(path) == '/default' else info(12)
        with patch.object(runtime, 'installed_pythons', return_value=['/default', '/python312']), \
             patch.object(runtime, 'probe_python', side_effect=detect), \
             patch.object(runtime, 'managed_python') as download, patch.object(runtime.subprocess, 'run') as run:
            runtime.ensure_environment(self.root, 'native', self.target)
            run.assert_called_once_with(['/installed/python', '-m', 'venv', '--copies', str(self.target)], check=True)
            download.assert_not_called()

    def test_valid_explicit_override_is_used_without_search_or_download(self):
        with patch.object(runtime, 'probe_python', return_value=info(12)), \
             patch.object(runtime, 'installed_pythons') as candidates, \
             patch.object(runtime, 'managed_python') as download, patch.object(runtime.subprocess, 'run'):
            runtime.ensure_environment(self.root, 'mlx', self.target, '/chosen/python')
            candidates.assert_not_called(); download.assert_not_called()

    def test_invalid_override_fails_without_fallback_and_different_existing_version_is_preserved(self):
        with patch.object(runtime, 'probe_python', return_value=info(14)), \
             patch.object(runtime, 'managed_python') as download:
            with self.assertRaisesRegex(RuntimeError, 'Explicit interpreter'):
                runtime.ensure_environment(self.root, 'indextts', self.target, '/wrong/python')
            download.assert_not_called()
        self.target.mkdir()
        with patch.object(runtime, 'probe_python', side_effect=[info(12), info(11)]):
            with self.assertRaisesRegex(RuntimeError, 'different Python'):
                runtime.ensure_environment(self.root, 'native', self.target, '/python312')

    def test_download_only_when_no_installed_interpreter_is_compatible(self):
        with patch.object(runtime, 'installed_pythons', return_value=[]), \
             patch.object(runtime, 'managed_python', return_value='/downloaded/python') as download, \
             patch.object(runtime, 'probe_python', return_value=info(14)), patch.object(runtime.subprocess, 'run'):
            runtime.ensure_environment(self.root, 'mlx', self.target)
            download.assert_called_once_with(self.root, 'mlx')

    def test_downloaded_interpreter_must_validate_before_environment_creation(self):
        with patch.object(runtime, 'installed_pythons', return_value=[]), \
             patch.object(runtime, 'managed_python', return_value='/bad/python'), \
             patch.object(runtime, 'probe_python', return_value=None), patch.object(runtime.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Downloaded interpreter failed'):
                runtime.ensure_environment(self.root, 'mlx', self.target)
            run.assert_not_called()

    def test_download_is_project_local_and_does_not_publish_python_to_path(self):
        managed = str(self.root / '.python/cpython/bin/python3')
        with patch.object(runtime.subprocess, 'check_output', side_effect=['/project/uv\n', managed + '\n']), \
             patch.object(runtime.subprocess, 'run') as run:
            self.assertEqual(runtime.managed_python(self.root, 'mlx'), managed)
        env = run.call_args.kwargs['env']
        for key, folder in [('UV_PYTHON_INSTALL_DIR', '.python'), ('UV_CACHE_DIR', '.bootstrap/cache'),
                            ('UV_PYTHON_BIN_DIR', '.bootstrap/bin')]:
            self.assertEqual(env[key], str(self.root / folder))
        self.assertEqual(env['UV_PYTHON_INSTALL_BIN'], '0')
        self.assertEqual(env['UV_NO_MODIFY_PATH'], '1')

    def test_download_refuses_external_python_or_bootstrap_symlink(self):
        with tempfile.TemporaryDirectory() as outside:
            for name in ['.python', '.bootstrap']:
                link = self.root / name; link.symlink_to(outside)
                try:
                    with patch.object(runtime.subprocess, 'check_output') as execute:
                        with self.assertRaisesRegex(RuntimeError, 'inside the serving project'):
                            runtime.managed_python(self.root, 'mlx')
                        execute.assert_not_called()
                finally:link.unlink()

    def test_failed_python_download_does_not_create_environment(self):
        with patch.object(runtime, 'installed_pythons', return_value=[]), \
             patch.object(runtime, 'managed_python', side_effect=subprocess.CalledProcessError(1, 'uv')):
            with self.assertRaises(subprocess.CalledProcessError):
                runtime.ensure_environment(self.root, 'mlx', self.target)
        self.assertFalse(self.target.exists())

    def test_probe_inspects_actual_python_without_importing_inference(self):
        actual = runtime.probe_python(sys.executable)
        self.assertEqual(actual['version'], list(sys.version_info[:3]))
        self.assertEqual(actual['implementation'], 'cpython')
        self.assertTrue(Path(actual['base']).is_file())

    def test_llm_setup_uses_independent_override_and_selected_environment(self):
        import setup_prompt_llm as setup
        python = self.root / '.venv-enhancer/bin/python'
        with patch.dict(os.environ, {'BOOTSTRAP_PYTHON': '/speech/python', 'LLM_PYTHON': '/llm/python'}), \
             patch.object(setup, 'PROJECT_ROOT', self.root), patch.object(setup, 'prepare_storage'), \
             patch.object(setup, 'ensure_environment', return_value=python) as ensure, \
             patch.object(setup.subprocess, 'run') as run:
            setup.main()
            ensure.assert_called_once_with(self.root, 'enhancer', self.root / '.venv-enhancer', '/llm/python')
            self.assertTrue(all(call.args[0][0] == str(python) for call in run.call_args_list))

    def shell_fixture(self, with_python=False):
        shutil.copyfile(PROJECT / 'bootstrap_python.sh', self.root / 'bootstrap_python.sh')
        commands = self.root / 'commands'; commands.mkdir()
        (commands / 'dirname').symlink_to(shutil.which('dirname'))
        (commands / 'sh').symlink_to('/bin/sh')
        if with_python:
            python = self.root / '.venv-native/bin/python'; python.parent.mkdir(parents=True)
            python.symlink_to(sys.executable)
        return {**os.environ, 'PATH': str(commands), 'BOOTSTRAP_PYTHON': '', 'VENV': ''}

    def test_offline_launcher_works_with_only_existing_project_environment(self):
        env = self.shell_fixture(with_python=True)
        result = subprocess.run(['/bin/sh', str(self.root / 'bootstrap_python.sh'), 'run', '-c',
                                 'import sys;print(sys.executable)'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.root / '.venv-native/bin/python'))
        self.assertFalse((self.root / '.bootstrap').exists())

    def test_offline_launcher_without_python_never_downloads(self):
        env = self.shell_fixture()
        result = subprocess.run(['/bin/sh', str(self.root / 'bootstrap_python.sh'), 'run', '-c', 'pass'],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('make setup', result.stderr)
        self.assertFalse((self.root / '.bootstrap').exists())

    def test_python_free_setup_bootstraps_locally_and_keeps_stdin(self):
        env = self.shell_fixture()
        uv = self.root / 'fake-uv'
        managed = self.root / '.python/cpython/bin/python3'
        uv.write_text('#!/bin/sh\n'
                      'if [ "$3" = install ]; then\n'
                      f' /bin/mkdir -p "{managed.parent}"\n'
                      f' /bin/cp "{sys.executable}" "{managed}"\n'
                      ' printf "%s\\n" "$UV_PYTHON_INSTALL_DIR|$UV_PYTHON_BIN_DIR|$UV_CACHE_DIR|$UV_PYTHON_INSTALL_BIN|$UV_NO_MODIFY_PATH" > "'
                      + str(self.root / 'uv-env') + '"\n'
                      'else\n'
                      f' printf "%s\\n" "{managed}"\nfi\n')
        # A symlink keeps CPython's stdlib discoverable; create it during fake install instead of before discovery.
        uv.write_text(uv.read_text().replace('/bin/cp ', '/bin/ln -s '))
        uv.chmod(0o755)
        (self.root / 'bootstrap_uv.sh').write_text('#!/bin/sh\nprintf "%s\\n" "' + str(uv) + '"\n')
        result = subprocess.run(['/bin/sh', str(self.root / 'bootstrap_python.sh'), 'setup', '-c',
                                 'print(input())'], env=env, input='menu input survives\n', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'menu input survives')
        self.assertEqual((self.root / 'uv-env').read_text().strip(),
                         f'{self.root}/.python|{self.root}/.bootstrap/bin|{self.root}/.bootstrap/cache|0|1')

    def test_make_serve_bootstraps_from_project_without_version_specific_commands(self):
        env = self.shell_fixture(with_python=True)
        env.pop('VENV')
        env.pop('PORT', None)
        shutil.copyfile(PROJECT / 'Makefile', self.root / 'Makefile')
        for name in ['service.json', 'service_defaults.json', 'queue_service.json']:
            shutil.copyfile(PROJECT / name, self.root / name)
        profile = self.root / 'backends/higgs'; profile.mkdir(parents=True)
        shutil.copyfile(PROJECT / 'backends/higgs/service.json', profile / 'service.json')
        (self.root / 'startup_select.py').write_text('import json,sys;print(json.dumps(sys.argv[1:]))')
        result = subprocess.run([shutil.which('make', path=os.defpath), 'serve', 'MODEL=higgs', 'NO_LLM=1'],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['serve', '--model', 'higgs', '--no-llm'])

    def test_shell_override_with_spaces_is_honored_and_invalid_override_never_falls_back(self):
        env = self.shell_fixture(with_python=True)
        python = self.root / 'my python'; python.symlink_to(sys.executable)
        env['BOOTSTRAP_PYTHON'] = str(python)
        args = ['/bin/sh', str(self.root / 'bootstrap_python.sh'), 'run', '-c', 'print("chosen")']
        result = subprocess.run(args, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr); self.assertEqual(result.stdout.strip(), 'chosen')
        env['BOOTSTRAP_PYTHON'] = '/missing/python'
        result = subprocess.run(args, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0); self.assertIn('BOOTSTRAP_PYTHON', result.stderr)

    def test_uv_checksum_mismatch_never_installs_binary(self):
        shutil.copyfile(PROJECT / 'bootstrap_uv.sh', self.root / 'bootstrap_uv.sh')
        commands = self.root / 'commands'; commands.mkdir()
        curl = commands / 'curl'
        curl.write_text('#!/bin/sh\nwhile [ "$1" != -o ]; do shift; done\nshift\nprintf invalid > "$1"\n')
        curl.chmod(0o755)
        result = subprocess.run(['/bin/sh', str(self.root / 'bootstrap_uv.sh')],
                                env={**os.environ, 'PATH': str(commands) + ':/usr/bin:/bin'}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0); self.assertIn('checksum', result.stderr)
        self.assertEqual(list((self.root / '.bootstrap').iterdir()), [])


if __name__ == '__main__':unittest.main()
