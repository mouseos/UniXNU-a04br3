#!/usr/bin/env python3
"""Verify pinned lifecycle inputs and safe unittest discovery."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

script = Path(__file__).with_name('test_powervr_gpu_lifecycle.py')
pins = ('A04BR3_DEVICE_ROOT', 'A04BR3_DEVICE_COMMIT', 'UNIXNU_BASE_ROOT',
        'UNIXNU_BASE_COMMIT', 'NATIVE_MOBILE_DISPLAY_REPO', 'NATIVE_MOBILE_DISPLAY_COMMIT')

# The temporary repositories must never inherit a caller's Git identity or
# Git configuration overrides. These are synthetic, fixed public identities.
FIXTURE_NAME = 'yuma shimizu'
FIXTURE_EMAIL = '46545607+mouseos@users.noreply.github.com'


def fixture_git_env():
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_AUTHOR_NAME=FIXTURE_NAME, GIT_AUTHOR_EMAIL=FIXTURE_EMAIL,
               GIT_COMMITTER_NAME=FIXTURE_NAME, GIT_COMMITTER_EMAIL=FIXTURE_EMAIL,
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    return env


def fixture_git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], env=fixture_git_env(),
                                   stderr=subprocess.PIPE, text=True).strip()


def fixture_commit(path, message):
    for role in ('AUTHOR', 'COMMITTER'):
        identity = fixture_git(path, 'var', 'GIT_' + role + '_IDENT')
        if not identity.startswith(FIXTURE_NAME + ' <' + FIXTURE_EMAIL + '> '):
            raise ValueError('unsafe temporary Git identity')
    fixture_git(path, 'commit', '-q', '-m', message)
    for field in ('%an', '%ae', '%cn', '%ce'):
        expected = FIXTURE_NAME if field.endswith('n') else FIXTURE_EMAIL
        if fixture_git(path, 'log', '-1', '--format=' + field) != expected:
            raise ValueError('unexpected temporary commit identity')

class LifecyclePinsTest(unittest.TestCase):
    def test_discovery_without_pins_skips(self):
        env = dict(os.environ)
        for name in pins:
            env.pop(name, None)
        result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(script.parent),
                                 '-p', script.name, '-v'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('skipped', result.stderr)
        self.assertIn('six explicit fixed-source inputs', result.stderr)

    def test_each_empty_pin_is_an_error(self):
        env = dict(os.environ)
        for name in pins:
            env.pop(name, None)
        for name in pins:
            with self.subTest(pin=name):
                case = dict(env)
                case[name] = ''
                result = subprocess.run([sys.executable, str(script)], env=case, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('is required for pinned lifecycle tests', result.stderr)
                self.assertNotIn('skipped=', result.stderr)

    def test_gitlink_validation_in_fresh_repositories(self):
        with tempfile.TemporaryDirectory(prefix='pvr-pin-git-') as temporary:
            root = Path(temporary)
            git = fixture_git
            def repo(name):
                path = root / name
                path.mkdir()
                git(path, 'init', '-q', '--object-format=sha1', '--template=')
                return path
            def commit(path, filename, contents):
                file = path / filename
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(contents)
                git(path, 'add', filename)
                fixture_commit(path, 'fixture')
                return git(path, 'rev-parse', 'HEAD')
            presenter = repo('presenter')
            first = commit(presenter, 'NativeGOPPresenter.cpp', 'first')
            second = commit(presenter, 'NativeGOPPresenter.cpp', 'second')
            self.assertNotEqual(first, second)
            base = repo('base')
            git(base, 'update-index', '--add', '--cacheinfo', f'160000,{first},drivers/NativeMobileDisplay')
            fixture_commit(base, 'pin presenter')
            base_commit = git(base, 'rev-parse', 'HEAD')
            device = repo('device')
            for name in ('NativePowerVRGpu.cpp', 'NativePowerVRJobs.cpp', 'native_powervr_bo_flags.h'):
                file = device / 'package/powervr' / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(name)
            git(device, 'add', 'package')
            fixture_commit(device, 'device fixture')
            device_commit = git(device, 'rev-parse', 'HEAD')
            env = dict(os.environ, A04BR3_DEVICE_ROOT=str(device), A04BR3_DEVICE_COMMIT=device_commit,
                       UNIXNU_BASE_ROOT=str(base), UNIXNU_BASE_COMMIT=base_commit,
                       NATIVE_MOBILE_DISPLAY_REPO=str(presenter), NATIVE_MOBILE_DISPLAY_COMMIT=first)
            load = ('import importlib.util,sys;'
                    'spec=importlib.util.spec_from_file_location("fixture",sys.argv[1]);'
                    'module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);'
                    'module.load_pinned_inputs();assert module.GOP=="first"')
            good = subprocess.run([sys.executable, '-c', load, str(script)], env=env, capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertIn('native_mobile_display_commit=' + first, good.stdout)
            env['NATIVE_MOBILE_DISPLAY_COMMIT'] = second
            bad = subprocess.run([sys.executable, '-c', load, str(script)], env=env, capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn('does not match the pinned base gitlink', bad.stderr)

    def test_git_identity_ignores_inherited_overrides(self):
        poisoned = {'GIT_AUTHOR_NAME': 'Injected Author', 'GIT_AUTHOR_EMAIL': 'author@example.invalid',
                    'GIT_COMMITTER_NAME': 'Injected Committer', 'GIT_COMMITTER_EMAIL': 'committer@example.invalid',
                    'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'user.email',
                    'GIT_CONFIG_VALUE_0': 'config@example.invalid'}
        with mock.patch.dict(os.environ, poisoned), tempfile.TemporaryDirectory(prefix='pvr-safe-git-') as temp:
            path = Path(temp)
            fixture_git(path, 'init', '-q', '--object-format=sha1', '--template=')
            (path / 'input').write_text('synthetic')
            fixture_git(path, 'add', 'input')
            fixture_commit(path, 'synthetic fixture')
            self.assertEqual(fixture_git(path, 'log', '-1', '--format=%ae'), FIXTURE_EMAIL)
            self.assertEqual(fixture_git(path, 'log', '-1', '--format=%ce'), FIXTURE_EMAIL)
            self.assertNotIn('GIT_CONFIG_COUNT', fixture_git_env())

    def test_bad_and_partial_pins_rejected(self):
        if not any(name in os.environ for name in pins):
            self.skipTest('pin rejection tests require six explicit fixed-source inputs')
        missing = [name for name in pins if not os.environ.get(name)]
        if missing:
            self.fail('partial pin configuration: ' + ', '.join(missing))
        def refused(name, value, text):
            env = dict(os.environ)
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
            result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, name)
            self.assertIn(text, result.stderr)
        for name in pins:
            with self.subTest(empty=name):
                refused(name, '', name + ' is required')
        refused('NATIVE_MOBILE_DISPLAY_COMMIT', None, 'is required')
        refused('NATIVE_MOBILE_DISPLAY_COMMIT', '279cf9b', 'full lowercase commit hash')
        refused('A04BR3_DEVICE_COMMIT', 'f2dee95', 'full lowercase commit hash')

if __name__ == '__main__':
    unittest.main()
