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
    def test_android_boot_ramdisk_uses_header_offset_and_validates_rootfs(self):
        kernel = b'K' * 2050
        fat = b'FAT fixture'
        ramdisk = struct.pack('<II', BUILD.MTK_MAGIC, len(fat)) + b'ROOTFS' + bytes(512 - 14) + fat
        header = bytearray(BUILD.BOOT_PAGE)
        header[:8] = BUILD.BOOT_MAGIC
        struct.pack_into('<8I', header, 8, len(kernel), 0x40080000, len(ramdisk), BUILD.RAMDISK_ADDR,
                         0, 0, 0x4E000000, BUILD.BOOT_PAGE)
        image = bytes(header) + kernel + bytes(BUILD.BOOT_PAGE * 2 - len(kernel)) + ramdisk
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            boot, output = temporary / 'boot.img', temporary / 'payload.img'
            boot.write_bytes(image)
            layout = BUILD.boot_ramdisk(boot, output)
            self.assertEqual(output.read_bytes(), fat)
            self.assertEqual(layout['ramdisk_offset'], BUILD.BOOT_PAGE * 3)
            bad = bytearray(image)
            struct.pack_into('<I', bad, 8 + 3 * 4, 0)
            boot.write_bytes(bad)
            with self.assertRaises(ValueError):
                BUILD.boot_ramdisk(boot, output)

    def test_pair_hashes_and_fat_diff_reject_undeclared_changes(self):
        digest = 'a' * 64
        pair = {'files': {name: {'sha256': digest} for name in ('kernel', 'data', 'trust', 'gpu', 'fd', 'dtb')}}
        hashes = BUILD.pair_hashes(pair)
        self.assertEqual(set(hashes), set(BUILD.FIXED) | {'fd', 'dtb'})
        original = {'kernel.macho': '1', 'kernel.macho.adt': '2'}
        changed_adt = {'kernel.macho': '1', 'kernel.macho.adt': '3'}
        self.assertEqual(BUILD.compare_payload_files(original, changed_adt), ['kernel.macho.adt'])
        with self.assertRaises(ValueError):
            BUILD.compare_payload_files(original, {'kernel.macho': '4', 'kernel.macho.adt': '3'})
        with self.assertRaises(ValueError):
            BUILD.pair_hashes({'files': {'kernel': {'sha256': digest}}})

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
