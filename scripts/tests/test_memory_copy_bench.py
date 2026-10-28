#!/usr/bin/env python3
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

class MemoryCopyBenchTest(unittest.TestCase):
    def test_explicit_run_and_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = pathlib.Path(tmp) / 'bench'
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(ROOT / 'tools/a04br3-memory-copy-bench.c'), '-o', str(binary)], check=True)
            for args in [[], ['--run', '0'], ['--run', '65'], ['--run', '-1'], ['--run', '1x'], ['1']]:
                self.assertEqual(subprocess.run([str(binary), *args], capture_output=True).returncode, 2)
            result = subprocess.run([str(binary), '--run', '1'], capture_output=True, text=True, check=True)
            row = dict(item.split('=', 1) for item in result.stdout.split()[1:])
            self.assertEqual(row['synthetic'], '1')
            self.assertEqual(row['verified'], '1')
            self.assertEqual(int(row['logical_bytes']), 1048576)
            self.assertEqual(int(row['elapsed_ns']), int(row['end_monotonic_ns']) - int(row['start_monotonic_ns']))
            self.assertGreater(int(row['elapsed_ns']), 0)

if __name__ == '__main__':
    unittest.main()
