"""Execute the host runner's reporting/failure path without an SDK or hardware."""
import contextlib
import importlib.util
import io
import json
import os
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


if __name__ == '__main__':
    unittest.main()
