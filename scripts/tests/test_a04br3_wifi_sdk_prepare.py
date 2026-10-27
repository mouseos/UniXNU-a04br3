#!/usr/bin/env python3
import importlib.util
import argparse
import shlex
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import shutil
import sys
import os
import json
import io
import contextlib
import subprocess

BASE_SOURCE = None

spec = importlib.util.spec_from_file_location('prepare', Path(__file__).parents[1] / 'prepare-a04br3-wifi-sdk.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sdk = self.root / 'sdk'
        self.sdk.mkdir()
        for name in m.REQUIRED:
            p = self.sdk / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'fixture')
        (self.sdk / 'usr/local/libexec/availability.pl').chmod(0o755)
        self.cache = self.root / 'cache'
        self.cache.write_bytes(b'qualified')
        self.dest = self.root / 'new'
        self.sha = patch.object(m, 'CACHE_SHA', m.digest(self.cache))
        self.sha.start()
        self.addCleanup(self.sha.stop)

    def test_check_does_not_create_destination(self):
        m.validate(self.dest, self.sdk, self.cache)
        self.assertFalse(self.dest.exists())

    def test_existing_destination_preserved(self):
        self.dest.mkdir()
        sentinel = self.dest / 'keep'
        sentinel.write_bytes(b'keep')
        with self.assertRaises(ValueError): m.validate(self.dest, self.sdk, self.cache)
        self.assertEqual(sentinel.read_bytes(), b'keep')

    def test_overlap_and_symlink_parent(self):
        for dest in (self.sdk / 'new', self.root):
            with self.assertRaises(ValueError): m.validate(dest, self.sdk, self.cache)
        alias = self.root / 'alias'
        alias.symlink_to(self.sdk, target_is_directory=True)
        with self.assertRaises(ValueError): m.validate(alias / 'new', self.sdk, self.cache)

    def test_cache_hash_and_missing_sdk(self):
        self.cache.write_bytes(b'wrong')
        with self.assertRaises(ValueError): m.validate(self.dest, self.sdk, self.cache)
        (self.sdk / m.REQUIRED[0]).unlink()
        with self.assertRaises(ValueError): m.validate(self.dest, self.sdk, self.cache)

    def test_internal_links_independent_copy(self):
        (self.sdk / 'alias').symlink_to(m.REQUIRED[0])
        m.validate(self.dest, self.sdk, self.cache)
        before = m.sdk_manifest(self.sdk)
        shutil.copytree(self.sdk, self.dest, symlinks=True)
        self.assertEqual(before, m.sdk_manifest(self.dest))
        (self.dest / 'alias').write_bytes(b'new')
        self.assertEqual((self.sdk / m.REQUIRED[0]).read_bytes(), b'fixture')
        self.assertNotEqual(before, m.sdk_manifest(self.dest))

    def test_nonexecutable_sdk_helper_rejected(self):
        (self.sdk / 'usr/local/libexec/availability.pl').chmod(0o644)
        with self.assertRaises(ValueError): m.validate(self.dest, self.sdk, self.cache)

    def test_external_link_rejected(self):
        (self.sdk / 'escape').symlink_to(self.cache)
        with self.assertRaises(ValueError): m.validate(self.dest, self.sdk, self.cache)

    def repo(self, path, content='original'):
        path.mkdir(parents=True)
        m.git(path, 'init', '-q')
        m.git(path, 'config', 'user.name', 'Fixture')
        m.git(path, 'config', 'user.email', '46545607+mouseos@users.noreply.github.com')
        (path / 'tracked').write_text(content)
        m.git(path, 'add', 'tracked')
        m.git(path, 'commit', '-qm', 'fixture')
        m.git(path, 'branch', 'wifi-sdk')
        return path

    def test_copy_dirty_checkout_independent_objects_and_clean_target(self):
        source = self.repo(self.root / 'source')
        original = (source / '.git/index').read_bytes()
        (source / 'tracked').write_text('dirty')
        (source / 'untracked').write_text('do not copy')
        state = m.repository_state(source)
        target = self.root / 'copy'
        m.check_repository(source, target)
        m.copy_repository(source, target)
        self.assertEqual((target / 'tracked').read_text(), 'original')
        self.assertFalse((target / 'untracked').exists())
        self.assertEqual(m.git(target, 'status', '--porcelain'), '')
        self.assertEqual(m.repository_state(source), state)
        self.assertEqual((source / '.git/index').read_bytes(), original)
        for obj in (source / '.git/objects').rglob('*'):
            if obj.is_file():
                other = target / '.git/objects' / obj.relative_to(source / '.git/objects')
                self.assertNotEqual(obj.stat().st_ino, other.stat().st_ino)

    def test_copy_linked_worktree_materializes_private_git_directory(self):
        source = self.repo(self.root / 'source')
        work = self.root / 'linked'
        m.git(source, 'worktree', 'add', '--detach', str(work), 'HEAD')
        target = self.root / 'copy'
        m.copy_repository(work, target)
        self.assertTrue((target / '.git').is_dir())
        self.assertEqual(m.git(target, 'rev-parse', '--show-toplevel'), str(target))
        self.assertEqual(m.git(target, 'rev-parse', 'HEAD'), m.git(source, 'rev-parse', 'HEAD'))

    def test_inherited_git_environment_cannot_redirect_writes(self):
        source = self.repo(self.root / 'source')
        target = self.root / 'copy'
        before = m.repository_state(source)
        with patch.dict(os.environ, {'GIT_DIR': str(source / '.git'),
                                    'GIT_WORK_TREE': str(source),
                                    'GIT_INDEX_FILE': str(source / '.git/index')}):
            m.copy_repository(source, target)
        self.assertEqual(before, m.repository_state(source))
        self.assertEqual(m.git(target, 'rev-parse', '--show-toplevel'), str(target))

    def test_alternates_and_uninitialized_submodule_rejected(self):
        source = self.repo(self.root / 'source')
        alternates = source / '.git/objects/info/alternates'
        alternates.write_text('/some/external/objects')
        with self.assertRaises(ValueError): m.check_repository(source, self.dest)
        alternates.unlink()
        sha = m.git(source, 'rev-parse', 'HEAD')
        m.git(source, 'update-index', '--add', '--cacheinfo', '160000,' + sha + ',child')
        m.git(source, 'commit', '-qm', 'gitlink')
        with self.assertRaises(ValueError): m.check_repository(source, self.dest)

    def test_fetch_pin_mismatch_stops_without_source_mutation(self):
        source = self.repo(self.root / 'source')
        before = m.repository_state(source)
        with self.assertRaisesRegex(ValueError, 'public branch changed'):
            m.apply_pin(source, self.dest, str(source), '0' * 40, {})
        self.assertEqual(before, m.repository_state(source))
        self.assertFalse((self.dest / 'build-wifi-sdk.sh').exists())

    def test_actual_prepare_with_gitdir_submodule_and_public_fetch(self):
        base = self.repo(self.root / 'base')
        device = self.repo(self.root / 'device')
        donor = self.repo(self.root / 'donor')
        # This fixture deliberately creates the normal submodule gitdir FILE.
        m.git(base, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q',
              str(donor), 'drivers/NativeMsdc')
        m.git(base, 'config', '-f', '.gitmodules', 'submodule.drivers/NativeMsdc.url',
              'https://github.com/mouseos/NativeMsdc.git')
        m.git(base, 'add', '.gitmodules', 'drivers/NativeMsdc')
        m.git(base, 'commit', '-qm', 'submodule')
        m.git(base, 'branch', '-f', 'wifi-sdk', 'HEAD')
        pins = {'UniXNU': m.git(base, 'rev-parse', 'HEAD'),
                'UniXNU-a04br3': m.git(device, 'rev-parse', 'HEAD'),
                'NativeMsdc': m.git(donor, 'rev-parse', 'HEAD')}
        sources = {'UniXNU': base, 'UniXNU-a04br3': device, 'NativeMsdc': donor}
        (base / 'tracked').write_text('dirty protected base')
        (device / 'untracked').write_text('protected untracked')
        states = {str(p): m.repository_state(p) for p in sources.values()}
        tree = self.root / 'old-tree'
        sdk = tree / '.cache/fakeroot'
        sdk.parent.mkdir(parents=True)
        shutil.copytree(self.sdk, sdk)
        before = m.sdk_manifest(sdk)
        calls = []
        real_run = m.run
        def fake_run(*args):
            calls.append(args)
            if args[0] == 'xcrun': return '/Applications/Xcode.app/SDK'
            if args[0] == 'xcodebuild': return 'Xcode fixture'
            if args[0] == 'git' and args[1] == 'ls-remote':
                name = args[2].rsplit('/', 1)[1][:-4]
                return pins[name] + '\trefs/heads/wifi-sdk'
            if args[0] == 'git' and 'remote' in args and 'add' in args:
                name = args[-1].rsplit('/', 1)[1][:-4]
                return real_run(*args[:-1], str(sources[name]))
            if args[0] == 'git' and 'config' in args and '--get' in args:
                return 'https://github.com/mouseos/NativeMsdc.git'
            return real_run(*args)
        argv = ['prepare', '--existing-base', str(base), '--existing-device', str(device),
                '--existing-tree', str(tree), '--kernelcache', str(self.cache),
                '--destination', str(self.dest), '--data-volume-uuid',
                'CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36', '--prepare']
        with patch.object(sys, 'argv', argv), patch.object(m.platform, 'system', return_value='Darwin'), \
             patch.object(m.shutil, 'which', return_value='/usr/bin/tool'), \
             patch.object(m, 'PINS', pins), patch.object(m, 'run', side_effect=fake_run):
            m.main()
        self.assertEqual(before, m.sdk_manifest(sdk))
        self.assertEqual(before, m.sdk_manifest(self.dest / 'out/a04br3/tree/.cache/fakeroot'))
        for path, state in states.items(): self.assertEqual(m.repository_state(Path(path)), state)
        for name in ('UniXNU', 'UniXNU-a04br3'):
            self.assertEqual(m.git(self.dest / name, 'status', '--porcelain'), '')
        child = self.dest / 'UniXNU/drivers/NativeMsdc'
        self.assertTrue((child / '.git').is_dir())
        self.assertEqual(m.git(child, 'rev-parse', 'HEAD'), pins['NativeMsdc'])
        # The official later build updates submodules: already-copied child
        # repositories must satisfy that path without new clones or source writes.
        m.git(self.dest / 'UniXNU', 'submodule', 'update', '--init', '--recursive')
        for path, state in states.items(): self.assertEqual(m.repository_state(Path(path)), state)
        self.assertFalse(any('clone' in call for call in calls))
        self.assertFalse(any('build' in call for call in calls))
        script = (self.dest / 'build-wifi-sdk.sh').read_text()
        self.assertIn('exec python3 -I ', script)
        generated = (self.dest / 'build-wifi-sdk.py').read_text()
        self.assertIn('XNU_OWNER_DATA_VOLUME_UUID', generated)
        self.assertIn('GITHUB_TOKEN', generated)
        subprocess.run(['sh', '-n', str(self.dest / 'build-wifi-sdk.sh')], check=True)
        retained = self.dest / 'out/retained.o'
        retained.write_bytes(b'preserve incremental object')
        copied_sdk = m.sdk_manifest(self.dest / 'out/a04br3/tree/.cache/fakeroot')
        with patch.object(m, 'PINS', pins):
            m.refresh_launchers(self.dest, 'CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36',
                                '8846FE78-2910-4297-AF97-D59BB6F8FC16')
            self.assertEqual(retained.read_bytes(), b'preserve incremental object')
            self.assertEqual(copied_sdk, m.sdk_manifest(self.dest / 'out/a04br3/tree/.cache/fakeroot'))
            old_launcher = (self.dest / 'build-wifi-sdk.py').read_bytes()
            alternates = self.dest / 'UniXNU/.git/objects/info/alternates'
            alternates.write_text(str(base / '.git/objects') + '\n')
            with self.assertRaisesRegex(ValueError, 'alternates'):
                m.refresh_launchers(self.dest, 'CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36',
                                    '8846FE78-2910-4297-AF97-D59BB6F8FC16')
            self.assertEqual((self.dest / 'build-wifi-sdk.py').read_bytes(), old_launcher)
            alternates.unlink()
            m.git(self.dest / 'UniXNU', 'config', 'core.worktree', str(base))
            with self.assertRaisesRegex(ValueError, 'external worktree'):
                m.refresh_launchers(self.dest, 'CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36',
                                    '8846FE78-2910-4297-AF97-D59BB6F8FC16')
            self.assertEqual((self.dest / 'build-wifi-sdk.py').read_bytes(), old_launcher)
            m.git(self.dest / 'UniXNU', 'config', '--unset', 'core.worktree')

            (self.dest / 'UniXNU/tracked').write_text('reject dirty prepared source')
            with self.assertRaisesRegex(ValueError, 'not clean'):
                m.refresh_launchers(self.dest, 'CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36',
                                    '8846FE78-2910-4297-AF97-D59BB6F8FC16')
            self.assertEqual((self.dest / 'build-wifi-sdk.py').read_bytes(), old_launcher)


    def test_explicit_uuid_cli_validation(self):
        expected = 'CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36'
        self.assertEqual(m.canonical_uuid(expected.lower()), expected)
        for bad in ('', 'not-uuid', expected.replace('-', ''), '{' + expected + '}', expected + '\n'):
            with self.assertRaises(Exception): m.canonical_uuid(bad)
        # Even a valid inherited value is insufficient: the CLI input is required.
        with patch.dict(os.environ, {'XNU_OWNER_DATA_VOLUME_UUID': expected}), \
             patch.object(sys, 'argv', ['prepare', '--destination', str(self.dest)]), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            m.main()
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.dest.exists())

    def test_actual_launcher_env_and_no_secret_serialization(self):
        self.dest.mkdir()
        binary = self.dest / 'fake-unixnu'
        binary.write_text("""#!/usr/bin/env python3
import os
import sys
assert os.environ['A04BR3_WIFI'] == '1'
assert os.environ['XNU_OWNER_DATA_VOLUME_UUID'] == 'CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36'
assert os.environ['XNU_OWNER_CACHE_VOLUME_UUID'] == '11111111-2222-3333-4444-555555555555'
for name in ('GIT_DIR', 'XNU_BOOTSTRAP', 'UNIXNU_OUT', 'OTHER_SECRET'):
    assert name not in os.environ
assert ('GITHUB_TOKEN' in os.environ) == (sys.argv[1] == 'token')
if sys.argv[1] == 'token':
    assert os.environ['GITHUB_TOKEN'] == 'synthetic-runtime-token'
print('child environment PASS')
""")
        binary.chmod(0o755)
        manifest = {'commands': [[str(binary), 'token'], [str(binary), 'token']]}
        uuid = 'CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36'
        cache = '11111111-2222-3333-4444-555555555555'
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'synthetic-prepare-token',
                                    'OTHER_SECRET': 'synthetic-other-secret'}):
            script = m.write_launchers(self.dest, manifest, uuid, cache)
        for name in ('build-wifi-sdk.sh', 'build-wifi-sdk.py', 'wifi-sdk-manifest.json'):
            data = (self.dest / name).read_text()
            for secret in ('synthetic-prepare-token', 'synthetic-runtime-token', 'synthetic-other-secret'):
                self.assertNotIn(secret, data)
        record = json.loads((self.dest / 'wifi-sdk-manifest.json').read_text())
        self.assertNotIn('GITHUB_TOKEN', record['build_environment'])
        environment = dict(os.environ, GITHUB_TOKEN='synthetic-runtime-token',
                           GIT_DIR='/incorrect', XNU_BOOTSTRAP='1',
                           UNIXNU_OUT='/incorrect', OTHER_SECRET='synthetic-other-secret')
        result = subprocess.run(['sh', str(script)], env=environment, text=True,
                                capture_output=True, check=True)
        self.assertEqual(result.stdout.count('child environment PASS'), 2)
        self.assertNotIn('synthetic-runtime-token', result.stdout + result.stderr)
        # Public anonymous operation still works when no runtime token is supplied.
        manifest['commands'] = [[str(binary), 'anonymous']]
        m.write_launchers(self.dest, manifest, uuid, cache)
        environment.pop('GITHUB_TOKEN')
        subprocess.run(['sh', str(script)], env=environment, capture_output=True, check=True)

    def test_fixed_base_real_owner_header_generator(self):
        if BASE_SOURCE is None:
            self.skipTest('pass --base-source for the pinned production header generator')
        source = m.run('git', '-C', str(BASE_SOURCE), 'show',
                       m.PINS['UniXNU'] + ':scripts/build-xnu-4k-baseline.sh')
        start = source.index('python3 - "$OWNER_DATA_HEADER"')
        end = source.index('\nPY_DATA_UUID', start) + len('\nPY_DATA_UUID')
        self.dest.mkdir()
        header = self.dest / 'owner-data-volume.h'
        production = self.dest / 'actual-header-step.sh'
        production.write_text('set -eu\nOWNER_DATA_HEADER=' + shlex.quote(str(header)) +
                              '\nOWNER_DATA_MOUNT=1\nNATIVE_RAM_BLOCK=1\n' + source[start:end] + '\n')
        manifest = {'commands': [['bash', str(production)]]}
        data = 'CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36'
        cache = '8846FE78-2910-4297-AF97-D59BB6F8FC16'
        script = m.write_launchers(self.dest, manifest, data, cache)
        subprocess.run(['sh', str(script)], check=True)
        self.assertEqual(header.read_text(), '#define XNU_OWNER_DATA_VOLUME_UUID "' + data +
                         '"\n#define XNU_OWNER_CACHE_VOLUME_UUID "' + cache + '"\n')

    def test_launcher_symlink_rejected_before_any_output_write(self):
        self.dest.mkdir()
        external = self.root / 'protected'
        external.write_bytes(b'preserve source')
        (self.dest / 'build-wifi-sdk.py').symlink_to(external)
        with self.assertRaisesRegex(ValueError, 'not a regular file'):
            m.write_launchers(self.dest, {'commands': []},
                              'CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36',
                              '8846FE78-2910-4297-AF97-D59BB6F8FC16')
        self.assertEqual(external.read_bytes(), b'preserve source')
        self.assertFalse((self.dest / 'wifi-sdk-manifest.json').exists())
        self.assertFalse((self.dest / 'build-wifi-sdk.sh').exists())

    def test_only_official_kernel_commands(self):
        commands = m.commands(self.dest, 4)
        self.assertEqual(commands[0][1:3], ['defconfig', 'a04br3_defconfig'])
        self.assertEqual(commands[1][1:3], ['build', 'kernel'])
        for command in commands:
            self.assertIn(str(self.dest / 'out'), command)
        self.assertIn(str(self.dest / 'kernelcache.decompressed'), commands[1])

if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--base-source', type=Path)
    options, remaining = parser.parse_known_args()
    BASE_SOURCE = options.base_source
    unittest.main(argv=[sys.argv[0], *remaining])
