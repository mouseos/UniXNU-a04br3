#!/usr/bin/env python3
"""Host-only contract tests for the stacklog payload builder."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('stacklog_payload', ROOT / 'scripts/a04br3-iostat-roa-stacklog-payload.py')
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def prop(name, value):
    return name.encode().ljust(32, b'\0') + struct.pack('<I', len(value)) + value + bytes((-len(value)) % 4)


class StacklogPayload(unittest.TestCase):
    def test_only_requested_arguments_change(self):
        original = 'serial=1 a04br3-home-perf=1 pl=1 native-msdc-iostat=1'
        rewritten = BUILD.rewrite_args(original)
        self.assertEqual(rewritten, 'serial=1 pl=60 native-msdc-iostat=1 a04br3-stacklog=1')
        self.assertEqual([x for x in original.split() if not x.startswith(('a04br3-home-perf=', 'pl='))],
                         [x for x in rewritten.split() if not x.startswith(('pl=', 'a04br3-stacklog='))])
        for bad in ('serial=1 pl=1', 'serial=1 a04br3-home-perf=1 pl=10',
                    'serial=1 a04br3-home-perf=1 pl=1 a04br3-stacklog=1'):
            with self.assertRaises(ValueError):
                BUILD.rewrite_args(bad)

    def test_adt_identity_is_parsed_without_rendering(self):
        secret = bytes.fromhex('0123456789abcdef')
        tree = struct.pack('<II', 2, 0) + prop('random-seed', bytes(64)) + prop('unique-chip-id', secret)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'adt'
            path.write_bytes(tree)
            self.assertEqual(BUILD.adt_identity(path), secret)
            path.write_bytes(struct.pack('<II', 0, 0))
            with self.assertRaises(ValueError):
                BUILD.adt_identity(path)

    def test_manifest_never_contains_unique_chip_id_value(self):
        self.assertNotIn('unique_chip_id', BUILD.__doc__.lower())
        source = (ROOT / 'scripts/a04br3-iostat-roa-stacklog-payload.py').read_text()
        self.assertIn("'identity_sha256'", source)
        self.assertIn('value withheld', source)


if __name__ == '__main__':
    unittest.main()
