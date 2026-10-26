"""書込みファイル生成の境界検査。SDK・実機・外部イメージは不要。"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]


def boot_fixture():
    def mtk(name, payload):
        header = bytearray(512)
        struct.pack_into('<II', header, 0, 0x58881688, len(payload))
        header[8:8 + len(name)] = name
        return bytes(header) + payload

    kernel = mtk(b'KERNEL', gzip.compress(b'firmware-fixture') + b'\xd0\x0d\xfe\xed')
    ramdisk = mtk(b'ROOTFS', b'payload-fixture' * 80)
    header = bytearray(2048)
    header[:8] = b'ANDROID!'
    struct.pack_into('<8I', header, 8, len(kernel), 0x40080000,
                     len(ramdisk), 0x60000000, 0, 0, 0x4E000000, 2048)
    command = b'bootopt=64S3,32N2,64N2'
    header[64:64 + len(command)] = command
    kernel += bytes((-len(kernel)) % 2048)
    return bytes(header) + kernel + ramdisk


class FlashValidationTest(unittest.TestCase):
    def run_script(self, name, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / name), *map(str, args)],
                              capture_output=True, text=True)

    def test_payload_boundaries(self):
        data = boot_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'boot.img'
            image.write_bytes(data)
            self.assertEqual(self.run_script('check-a04br3-bootimg.py', image).returncode, 0)
            for end in (0, 40, 2048, 2048 + 511, 4096 + 512, len(data) - 1):
                with self.subTest(end=end):
                    image.write_bytes(data[:end])
                    result = self.run_script('check-a04br3-bootimg.py', image)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('truncated', result.stderr)

    def test_old_or_misaligned_ramdisk_address_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'boot.img'
            for address in (0x80000000, 0x50000000, 0x60001000):
                with self.subTest(address=address):
                    data = bytearray(boot_fixture())
                    struct.pack_into('<I', data, 20, address)
                    image.write_bytes(data)
                    result = self.run_script('check-a04br3-bootimg.py', image)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('header page', result.stderr)

    def test_portable_complete_bundle_preserves_stock_partitions(self):
        with tempfile.TemporaryDirectory(prefix='flash fixture ') as tmp:
            base = Path(tmp)
            boot = base / 'input boot.img'
            boot.write_bytes(boot_fixture())
            userdata = base / 'userdata.img'
            userdata.write_bytes(bytes(1024))
            policy = base / 'rootpolicy.img'
            policy.write_bytes(b'p' * 512)
            (base / 'rootpolicy.json').write_text(json.dumps({
                'userdata_offset': 1024, 'bytes': 512,
                'sha256': hashlib.sha256(policy.read_bytes()).hexdigest()}))
            out = base / 'bundle'
            result = self.run_script('make-a04br3-flash.py', '--boot', boot,
                                     '--userdata', userdata, '--root-policy', base,
                                     '--bundle', '--out', out)
            self.assertEqual(result.returncode, 0, result.stderr)
            moved = base / 'relocated bundle'
            out.rename(moved)
            boot.unlink()
            userdata.unlink()
            policy.unlink()
            report = json.loads((moved / 'flash.json').read_text())
            self.assertIn('logo', report['preserved_partitions'])
            writes = report['writes']
            self.assertEqual([w['target'] for w in writes],
                             ['boot', 'userdata', 'rootpolicy', 'gpt-secondary', 'gpt-primary'])
            layout = json.loads((SCRIPTS / 'a04br3-gpt.json').read_text())
            for part in layout['partitions']:
                if part['name'] not in report['preserved_partitions']:
                    continue
                start = part['first_lba'] * 512
                end = start + part['sectors'] * 512
                for write in writes:
                    self.assertTrue(write['offset'] + write['bytes'] <= start or
                                    write['offset'] >= end, part['name'])
            for line in (moved / 'write.mtk').read_text().splitlines():
                if line == 'reset':
                    continue
                command, offset, size, name = line.split(' ')
                self.assertEqual(command, 'wo')
                self.assertFalse(Path(name).is_absolute())
                self.assertEqual((moved / name).stat().st_size, int(size, 16))
            self.assertTrue({'logo', 'nvram', 'proinfo', 'protect1', 'protect2',
                             'persist', 'lk', 'lk2'} <= set(report['preserved_partitions']))
            # Independent stock logo byte extent, not derived from generator output.
            for write in writes:
                self.assertTrue(write['offset'] + write['bytes'] <= 0x6e00000 or
                                write['offset'] >= 0x7600000)
            fake = base / 'fake-mtk'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@"\n')
            fake.chmod(0o755)
            env = dict(os.environ, MTKCLIENT='./fake-mtk')
            result = subprocess.run([str(moved / 'install.sh')], cwd=base, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), [str(moved), 'script', 'write.mtk'])
            (moved / 'boot.img').write_bytes(b'corrupt')
            result = subprocess.run([str(moved / 'install.sh')], cwd=base, env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('bundle integrity failure', result.stderr)
            self.assertEqual(result.stdout, '')
            (moved / 'boot.img').write_bytes(boot_fixture())
            manifest = json.loads((moved / 'bundle.json').read_text())
            for name, item in manifest.items():
                self.assertEqual(hashlib.sha256((moved / name).read_bytes()).hexdigest(),
                                 item['sha256'])

    def test_policy_offsets_without_userdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            image = base / 'boot.img'
            image.write_bytes(boot_fixture())
            policy_data = b'x' * 512
            (base / 'rootpolicy.img').write_bytes(policy_data)
            for offset in (-134217728, 1, True, 0):
                with self.subTest(offset=offset):
                    out = base / ('out-' + str(offset))
                    (base / 'rootpolicy.json').write_text(json.dumps({
                        'userdata_offset': offset, 'bytes': len(policy_data),
                        'sha256': hashlib.sha256(policy_data).hexdigest()}))
                    result = self.run_script('make-a04br3-flash.py', '--boot', image,
                                             '--root-policy', base, '--out', out)
                    if type(offset) is int and offset == 0:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertTrue((out / 'write.mtk').exists())
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('nonnegative sector-aligned', result.stderr)
                        self.assertFalse((out / 'write.mtk').exists())


if __name__ == '__main__':
    unittest.main()
