"""Execute the host runner's reporting/failure path without an SDK or hardware."""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'check-schema2-host.py'
spec = importlib.util.spec_from_file_location('host_runner', path)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class HostRunnerTests(unittest.TestCase):
    def test_inherited_optimization_cannot_disable_child_assertions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {'PYTHONOPTIMIZE': '2'}), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(runner.execute([('assertion', [sys.executable, '-c', 'assert False, "real assertion"'])], root, root / 'output', {}), 1)
                self.assertEqual(os.environ['PYTHONOPTIMIZE'], '2')
            self.assertIn('real assertion', (root / 'output/logs/assertion.log').read_text())

    def test_success_records_real_commands_and_logs(self):
        with tempfile.TemporaryDirectory(prefix='host runner ') as directory:
            root = Path(directory)
            checks = [('real-python', [sys.executable, '-c', 'print("actual child output")'])]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.execute(checks, root, root / 'output', {'qualification': 'host only'}), 0)
            report = json.loads((root / 'output/host-results.json').read_text())
            self.assertEqual(report['status'], 'passed')
            self.assertEqual(report['checks'][0]['command'], checks[0][1])
            self.assertEqual(report['checks'][0]['returncode'], 0)
            self.assertEqual((root / 'output/logs/real-python.log').read_text(), 'actual child output\n')

    def test_failure_is_persisted_and_next_command_never_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / 'must-not-exist'
            checks = [('refusal', [sys.executable, '-c', 'import sys;print("failure detail");sys.exit(7)']),
                      ('forbidden-next-step', [sys.executable, '-c', 'from pathlib import Path;Path(__import__("sys").argv[1]).touch()', str(sentinel)])]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(runner.execute(checks, root, root / 'output', {}), 1)
            report = json.loads((root / 'output/host-results.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(len(report['checks']), 1)
            self.assertEqual(report['checks'][0]['returncode'], 7)
            self.assertFalse(sentinel.exists())

    def test_missing_executable_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(runner.execute([('missing', [str(root / 'no-such-tool')])], root, root / 'output', {}), 1)
            report = json.loads((root / 'output/host-results.json').read_text())
            self.assertEqual(report['checks'][0]['returncode'], 127)
            self.assertEqual(report['status'], 'failed')

    def test_output_cannot_write_inside_any_source_root(self):
        with tempfile.TemporaryDirectory() as directory:
            tools, soc = Path(directory) / 'tools', Path(directory) / 'soc'
            for root in (tools, soc, runner.DEVICE):
                for out in (root, root / 'nested/output'):
                    with self.subTest(out=out), self.assertRaisesRegex(ValueError, 'outside'):
                        runner.check_paths(tools, soc, out)


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='source identity ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.roots = {name: self.base / name for name in ('tools', 'device', 'soc')}
        for root in self.roots.values():
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            # Only the disposable fixture repositories need a commit identity.
            # Fresh CI hosts have no global identity; never modify that scope.
            subprocess.run(['git', '-C', str(root), 'config', '--local', 'user.name', 'yuma shimizu'], check=True)
            subprocess.run(['git', '-C', str(root), 'config', '--local', 'user.email',
                            '46545607+mouseos@users.noreply.github.com'], check=True)
            email = subprocess.check_output(['git', '-C', str(root), 'config', 'user.email'], text=True).strip()
            self.assertTrue(email.endswith('@users.noreply.github.com'))
            (root / 'tracked.txt').write_text('original\n')
            subprocess.run(['git', '-C', str(root), 'add', 'tracked.txt'], check=True)
            subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'fixture'], check=True)
        self.pin = runner.revision(self.roots['tools'])

    def test_clean_sibling_checkouts_and_exact_pin(self):
        identities = runner.source_identities(self.roots)
        self.assertIsNone(runner.source_refusal(identities, self.pin))
        self.assertTrue(all(not item['dirty'] and item['status_porcelain'] == '' for item in identities.values()))

    def test_nested_device_directory_is_recorded_without_exclusion(self):
        nested = self.roots['tools'] / 'device-schema2'
        nested.mkdir()
        (nested / 'board.toml').write_text('schema_version = 2\n')
        identities = runner.source_identities(self.roots)
        self.assertIn('device-schema2/board.toml', identities['tools']['status_porcelain'])
        self.assertIn('tools', runner.source_refusal(identities, self.pin))

    def test_tracked_staged_and_untracked_changes_are_recorded(self):
        for name in self.roots:
            root = self.roots[name]
            (root / 'tracked.txt').write_text('changed\n')
            if name == 'device':
                subprocess.run(['git', '-C', str(root), 'add', 'tracked.txt'], check=True)
            if name == 'soc':
                (root / 'untracked.txt').write_text('new\n')
        identities = runner.source_identities(self.roots)
        for name in self.roots:
            self.assertTrue(identities[name]['dirty'])
            self.assertIn('tracked.txt', identities[name]['status_porcelain'])
        self.assertIn('?? untracked.txt', identities['soc']['status_porcelain'])
        self.assertIn('M  tracked.txt', identities['device']['status_porcelain'])
        self.assertIn('dirty', runner.source_refusal(identities, self.pin))

    def test_wrong_tools_revision_is_refused(self):
        identities = runner.source_identities(self.roots)
        self.assertIn('paired_tools_revision', runner.source_refusal(identities, '0' * 40))

    def test_source_change_during_a_successful_command_fails_the_run(self):
        out = self.base / 'output'
        report = {'sources': runner.source_identities(self.roots)}
        command = [sys.executable, '-c', 'from pathlib import Path;Path(__import__("sys").argv[1]).write_text("mutation")', str(self.roots['tools'] / 'tracked.txt')]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runner.execute([('mutation', command)], self.base, out, report, self.roots), 1)
        result = json.loads((out / 'host-results.json').read_text())
        self.assertEqual(result['checks'][0]['returncode'], 0)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('tracked.txt', result['sources_after']['tools']['status_porcelain'])


if __name__ == '__main__':
    unittest.main()
