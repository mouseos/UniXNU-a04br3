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
                     len(ramdisk), 0x80000000, 0, 0, 0x4E000000, 2048)
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
