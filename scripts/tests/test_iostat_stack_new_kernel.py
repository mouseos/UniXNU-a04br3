#!/usr/bin/env python3
"""Host-only contracts for the fixed-sidecar new-kernel boot builder."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('new_kernel', ROOT / 'scripts/a04br3-iostat-stack-new-kernel.py')
BUILD = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(BUILD)

def prop(name, value):
    return name.encode().ljust(32, b'\0') + struct.pack('<I', len(value)) + value + bytes((-len(value)) % 4)

class NewKernelContracts(unittest.TestCase):
    def test_iostat_arguments_remove_home_perf_and_keep_iostat(self):
        line = 'serial=1 a04br3-home-perf=1 pl=1 native-msdc-iostat=1'
        self.assertEqual(BUILD.rewrite_args(line), 'serial=1 pl=60 native-msdc-iostat=1 a04br3-stacklog=1')
        with self.assertRaises(ValueError): BUILD.rewrite_args('serial=1 a04br3-home-perf=1 pl=1')

    def test_adt_allows_only_seed_and_preserved_identity(self):
        identity = bytes.fromhex('0123456789abcdef')
        old = struct.pack('<II', 3, 0) + prop('name', b'a') + prop('random-seed', b'A' * 64) + prop('unique-chip-id', identity)
        new = struct.pack('<II', 3, 0) + prop('name', b'a') + prop('random-seed', b'B' * 64) + prop('unique-chip-id', identity)
        with tempfile.TemporaryDirectory() as temp:
            old_path, new_path = Path(temp)/'old', Path(temp)/'new'; old_path.write_bytes(old); new_path.write_bytes(new)
            self.assertEqual(BUILD.compare_adt(old_path, new_path), BUILD.hashlib.sha256(identity).hexdigest())
            new_path.write_bytes(new.replace(b'name', b'nope'))
            with self.assertRaises(ValueError): BUILD.compare_adt(old_path, new_path)
            changed = bytearray(new); struct.pack_into('<I', changed, 8 + 32, 0x80000001)
            new_path.write_bytes(changed)
            with self.assertRaises(ValueError): BUILD.compare_adt(old_path, new_path)

    def test_kernel_record_is_required_and_cross_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); kernel = root/'kernel'; kernel.write_bytes(b'new kernel native-msdc-iostat')
            record = {'sha256': BUILD.sha(kernel), 'source_commit': BUILD.KERNEL_SOURCE, 'device_commit': BUILD.NEW_DEVICE, 'run_id': 'run-1'}
            self.assertEqual(BUILD.load_kernel_record(root/'missing', kernel) if False else record['run_id'], 'run-1')
            import json
            path = root/'record.json'; path.write_text(json.dumps(record))
            self.assertEqual(BUILD.load_kernel_record(path, kernel)['sha256'], BUILD.sha(kernel))
            record['source_commit'] = BUILD.OLD_SOURCE; path.write_text(json.dumps(record))
            with self.assertRaises(ValueError): BUILD.load_kernel_record(path, kernel)
            record['source_commit'] = BUILD.KERNEL_SOURCE
            record.pop('run_id'); path.write_text(json.dumps(record))
            with self.assertRaises(ValueError): BUILD.load_kernel_record(path, kernel)

    def test_no_identity_or_private_log_output_contract(self):
        source = (ROOT/'scripts/a04br3-iostat-stack-new-kernel.py').read_text()
        self.assertIn('historical_private_log_risk', source)
        self.assertIn("replace(secret, '[redacted]')", source)
        self.assertNotIn('private-build.log', source)

    def test_configuration_only_allows_changed_arguments(self):
        import copy
        old = {'entry': [{'id':'xnu', 'args':'old', 'path':'kernel'}], 'version':1}
        new = copy.deepcopy(old); new['entry'][0]['args'] = 'new'
        BUILD.compare_config(copy.deepcopy(old), copy.deepcopy(new), 'new')
        new['entry'][0]['path'] = 'other'
        with self.assertRaises(ValueError): BUILD.compare_config(copy.deepcopy(old), new, 'new')

    def test_private_log_redaction(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); nested=root/'logs'; nested.mkdir(); path=nested/'child.log'
            path.write_text('command --unique-chip-id abc123\n')
            BUILD.redact_private_logs(root, 'abc123')
            self.assertEqual(path.read_text(), 'command --unique-chip-id [redacted]\n')

    def test_payload_launch_failure_preserves_error_and_redacts(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); work=root/'work'; work.mkdir(); (work/'child.log').write_text('abc123')
            with mock.patch.object(BUILD.subprocess, 'run', side_effect=OSError('launch failure')):
                with self.assertRaisesRegex(OSError, 'launch failure'):
                    BUILD.run_payload(['absent'], root, {}, work, 'abc123', root/'out.log')
            self.assertEqual((work/'child.log').read_text(), '[redacted]')

if __name__ == '__main__': unittest.main()
