#!/usr/bin/env python3
"""Writer, window format and decoder of the persistent boot log agree (package/A04BR3BootLog.c, scripts/read-a04br3-log.py)."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


class BootLogFormat(unittest.TestCase):
    def test_writer_window_decoder(self):
        compiler = shutil.which('cc') or shutil.which('clang')
        if not compiler:
            self.skipTest('no C compiler')
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                            '-fsanitize=address,undefined', '-I', str(HERE), str(HERE / 'bootlog_host_harness.c'),
                            '-o', str(work / 'harness')], check=True)
            subprocess.run([str(work / 'harness'), str(work / 'window.bin')], check=True)
            result = subprocess.run(['python3', str(ROOT / 'scripts/read-a04br3-log.py'), '--raw', str(work / 'window.bin'),
                                     '--out', str(work / 'out')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            names = sorted(path.name for path in (work / 'out').iterdir())
            # boot 1: sequences 1 and 2 (slots 3 and 2); boot 2: sequence 1 only (its sequence 2 lost its header)
            self.assertEqual(names, ['boot000001-seq000001', 'boot000001-seq000002', 'boot000002-seq000001'])
            first = (work / 'out/boot000001-seq000001/boot.log').read_text()
            self.assertEqual(first, 'boot one line A\n')
            wrapped = (work / 'out/boot000001-seq000002/boot.log').read_text()
            self.assertTrue(wrapped.endswith('boot one tail\n'))
            self.assertEqual(len(wrapped), 4096)
            header = json.loads((work / 'out/boot000002-seq000001/header.json').read_text())
            self.assertEqual((header['slot'], header['kernel_uuid'], header['log_crc_ok']),
                             (5, '0123ABCD-4567-89EF-0123-456789ABCDEF', True))   # pair 4/5, odd sequence -> slot 5
            self.assertEqual((work / 'out/boot000002-seq000001/boot.log').read_text(), 'boot two first\n')


if __name__ == '__main__':
    unittest.main()

