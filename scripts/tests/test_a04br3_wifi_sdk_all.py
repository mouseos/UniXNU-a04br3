#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Exercise the production-rule diagnostic with an executable synthetic compiler."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('wifi_sdk_all', ROOT / 'scripts/check-a04br3-wifi-sdk-all.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

class AllUnits(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.device, self.tree, self.output = (self.root / x for x in ('device', 'tree', 'audit'))
        self.device.mkdir()
        (self.device / 'scripts').mkdir()
        for name in ('build-a04br3-board.py', 'a04br3_wifi_build.py'):
            shutil.copyfile(ROOT / 'scripts' / name, self.device / 'scripts' / name)
        self.tree.mkdir()
        self.compiler = self.root / 'compiler.py'
        self.compiler.write_text('''import pathlib, sys
args = sys.argv[1:]
source = pathlib.Path(args[args.index('-c') + 1])
if source.name.startswith('Fail'):
    sys.stderr.write('first error: synthetic failure\\n' + 'x' * 5000 + '\\nlast error\\n')
    sys.exit(1)
pathlib.Path(args[args.index('-o') + 1]).write_bytes(b'object')
sys.stderr.write('warning: retained [-Wsynthetic]\\n')
''')
        original = self.tree / 'original.cpp'
        original.write_text('source')
        record = {'directory': str(self.tree), 'file': str(original),
                  'arguments': [sys.executable, str(self.compiler), '-c', str(original),
                                '-o', str(self.tree / 'never-overwrite.o'), '-MMD',
                                '-MF', str(self.tree / 'never-overwrite.d'), '-Werror']}
        for relative in ('iokit/DEVELOPMENT/IOPlatformExpert.cpo.json', 'bsd/DEVELOPMENT/kern_exec.o.json'):
            p = self.tree / audit.OBJECTS / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(record))
        with patch.dict(os.environ, {'A04BR3_WIFI': '1'}):
            rules, c_sources = audit.load_build_rules(self.device, self.tree)
        wifi = self.device / 'package/wifi'
        (wifi / 'network/apple').mkdir(parents=True)
        (wifi / 'network/apple/FailApple.cpp').write_text('failure')
        (wifi / 'network/apple/PassApple.cpp').write_text('success')
        (wifi / 'network/FailFamily.cpp').write_text('failure')
        (wifi / 'network/apple/Header.h').write_text('header')
        (wifi / 'sdio').mkdir()
        (wifi / 'sdio/A04BR3SDIO.cpp').write_text('source')
        (wifi / 'WifiBoard.cpp').write_text('source')
        for relative in c_sources:
            p = wifi / 'bearssl/src' / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('source')
        self.expected = len(rules['source_files'](wifi)) + len(c_sources)

    def test_all_units_attempted_and_full_stderr_retained(self):
        with patch.dict(os.environ, {'A04BR3_WIFI': '1'}):
            self.assertEqual(audit.audit(self.tree, self.device, self.output), 1)
        report = json.loads((self.output / 'report.json').read_text())
        self.assertEqual(len(report['results']), self.expected)
        self.assertEqual(report['failed'], 2)
        self.assertFalse(report['linked'])
        self.assertFalse(report['firmware_downloaded'])
        for row in report['results']:
            command = json.loads(Path(row['command']).read_text())
            self.assertIn('-Werror', command)
            self.assertIn('-ferror-limit=0', command)
            self.assertNotIn('-MF', command)
            self.assertNotIn('-MMD', command)
            self.assertTrue(Path(command[command.index('-o') + 1]).is_relative_to(self.output))
            if row['source'].endswith('.cpp'):
                self.assertIn('-D__PRIVATE_SPI__=1', command)
            if row['returncode']:
                error = Path(row['stderr']).read_text()
                self.assertTrue(error.startswith('first error:'))
                self.assertIn('last error', error)
                self.assertGreater(len(error), 5000)
        aggregate = (self.output / 'diagnostics.log').read_text()
        self.assertEqual(aggregate.count('first error:'), 2)
        self.assertIn('WifiBoard.cpp', aggregate)
        self.assertFalse((self.tree / 'never-overwrite.o').exists())
        self.assertFalse((self.tree / 'never-overwrite.d').exists())

    def test_default_gate_and_existing_output_preserved(self):
        with patch.dict(os.environ, {'A04BR3_WIFI': '0'}), self.assertRaisesRegex(ValueError, 'explicit'):
            audit.audit(self.tree, self.device, self.output)
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        keep = self.output / 'keep'
        keep.write_text('unchanged')
        with patch.dict(os.environ, {'A04BR3_WIFI': '1'}), self.assertRaisesRegex(ValueError, 'absent'):
            audit.audit(self.tree, self.device, self.output)
        self.assertEqual(keep.read_text(), 'unchanged')

    def test_dotdot_cannot_escape_output_boundary(self):
        (self.root / 'alias-base').mkdir()
        for name in ('device', 'tree'):
            disguised = self.root / 'alias-base' / '..' / name / 'must-not-create'
            with patch.dict(os.environ, {'A04BR3_WIFI': '1'}), self.assertRaisesRegex(ValueError, 'overlaps'):
                audit.audit(self.tree, self.device, disguised)
            self.assertFalse((self.root / name / 'must-not-create').exists())
        alias = self.root / 'alias'
        alias.symlink_to(self.device, target_is_directory=True)
        with patch.dict(os.environ, {'A04BR3_WIFI': '1'}), self.assertRaisesRegex(ValueError, 'symlink'):
            audit.audit(self.tree, self.device, alias / 'must-not-create')
        self.assertFalse((self.device / 'must-not-create').exists())

    def test_stale_record_rejected_before_any_output(self):
        path = self.tree / audit.OBJECTS / 'iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
        record = json.loads(path.read_text())
        record['directory'] = str(self.root)
        path.write_text(json.dumps(record))
        with patch.dict(os.environ, {'A04BR3_WIFI': '1'}), self.assertRaisesRegex(ValueError, 'stale'):
            audit.audit(self.tree, self.device, self.output)
        self.assertFalse(self.output.exists())

if __name__ == '__main__':
    unittest.main()
