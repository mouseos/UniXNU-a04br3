"""書込みファイル生成の境界検査。SDK・実機・外部イメージは不要。"""
import gzip
import hashlib
import json
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

    def test_bundle_is_self_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            image = base / 'boot.img'
            image.write_bytes(boot_fixture())
            userdata = base / 'userdata-root.img'
            userdata.write_bytes(b'u' * 4096)
            out = base / 'package'
            result = self.run_script('make-a04br3-flash.py', '--boot', image, '--userdata', userdata,
                                     '--out', out, '--bundle')
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = (out / 'write.mtk').read_text().splitlines()
            # Inputs first, the backup GPT, the primary GPT last, then the reset.
            self.assertEqual([line.split()[3] for line in lines[:-1]],
                             ['boot.img', 'userdata-root.img', 'gpt-secondary.bin', 'gpt-primary.bin'])
            self.assertEqual(lines[-1], 'reset')
            for line in lines[:-1]:
                name = line.split()[3]
                self.assertNotIn('/', name)
                self.assertEqual(int(line.split()[2], 16), (out / name).stat().st_size)
            sums = dict(reversed(row.split('  ')) for row in (out / 'SHA256SUMS').read_text().splitlines())
            self.assertEqual(set(sums), {'boot.img', 'userdata-root.img', 'gpt-secondary.bin', 'gpt-primary.bin',
                                         'write.mtk', 'flash.json'})
            for name, digest in sums.items():
                self.assertEqual(hashlib.sha256((out / name).read_bytes()).hexdigest(), digest)
            self.assertIn('mtk script write.mtk', (out / 'FLASH.md').read_text())
            again = self.run_script('make-a04br3-flash.py', '--boot', image, '--out', base / 'other', '--bundle')
            self.assertEqual(again.returncode, 0, again.stderr)
            (base / 'third').mkdir()
            (base / 'third' / 'boot.img').write_bytes(b'stale')
            refused = self.run_script('make-a04br3-flash.py', '--boot', image, '--out', base / 'third', '--bundle')
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('refuse to overwrite', refused.stderr)


if __name__ == '__main__':
    unittest.main()
