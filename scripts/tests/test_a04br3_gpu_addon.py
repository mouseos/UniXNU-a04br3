"""Exercise the hot-update archive and original trust-cache pin end to end."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'make-a04br3-gpu-addon.py'
HELPER = SCRIPT.with_name('build-a04br3-powervr-probe.py')


class AddonTest(unittest.TestCase):
    def test_archive_paths_trust_and_preserved_seed(self):
        spec = importlib.util.spec_from_file_location('probe', HELPER)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe, metal = root / 'probe', root / 'metal'
            bundle = metal / 'NativePowerVRMetal.bundle'
            (probe / 'lib').mkdir(parents=True)
            (bundle / '_CodeSignature').mkdir(parents=True)
            (probe / 'powervrprobed').write_bytes(b'probe')
            (probe / 'lib/libvulkan_powervr_mesa.dylib').write_bytes(b'vulkan')
            (bundle / 'NativePowerVRMetal').write_bytes(b'metal')
            (bundle / 'NativePowerVRMetal').chmod(0o755)
            (bundle / 'Info.plist').write_bytes(b'plist')
            (bundle / '_CodeSignature/CodeResources').write_bytes(b'seal')
            (bundle / 'libvulkan_powervr_mesa.dylib').write_bytes(b'vulkan')
            (probe / 'trust.json').write_text(json.dumps({'daemon_cdhash': '11'*20, 'vulkan_cdhash': '22'*20}))
            metal_trust = {'cdhash': '33'*20, 'driver_cdhash': '22'*20}
            (metal / 'trust.json').write_text(json.dumps(metal_trust))
            original = struct.pack('<III16sI', 1, 8, 2, bytes(16), 1) + bytes.fromhex('44'*20) + bytes([2, 0, 0, 0])
            base = root / 'base'
            base.write_bytes(original)
            board = root / 'board.toml'
            board.write_text('[payload]\ntrust_cache_sha256 = ["' + hashlib.sha256(original).hexdigest() + '"]\n')
            seed = root / 'data.dbr'
            seed.write_bytes(b'existing Data seed UUID and content')
            argv = [sys.executable, str(SCRIPT), '--probe-dir', str(probe), '--metal-plugin', str(metal),
                    '--trust-base', str(base), '--board', str(board), '--trust-out', str(root / 'trust'),
                    '--out', str(root / 'addon')]
            # Real addon subprocess, with a fake macOS codesign command enforcing
            # exact verify/display argv and returning CDHashes of the fixture bytes.
            codesign = root / 'codesign'
            codesign.write_text('#!' + sys.executable + '\n' + r"""
import json, os
from pathlib import Path
import sys
args = sys.argv[1:]
with open(os.environ['MOCK_CODESIGN_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\n')
path = Path(args[-1])
if args[:2] == ['--verify', '--strict'] and len(args) == 3:
    if str(path) == os.environ.get('MOCK_VERIFY_FAIL'):
        sys.exit(1)
    if path.is_dir():
        assert path.name == 'NativePowerVRMetal.bundle'
        assert (path / '_CodeSignature/CodeResources').read_bytes() == b'seal'
    else:
        assert path.read_bytes() in (b'probe', b'vulkan', b'metal')
elif args[:2] == ['--display', '--verbose=4'] and len(args) == 3:
    value = {b'probe': '11', b'vulkan': '22', b'metal': '33'}[path.read_bytes()] * 20
    print('CDHash=' + value, file=sys.stderr)
else:
    sys.exit('unexpected codesign contract: ' + repr(args))
""")
            codesign.chmod(0o755)
            log = root / 'codesign.log'
            environment = mock.patch.dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                                          MOCK_CODESIGN_LOG=str(log))
            environment.start()
            self.addCleanup(environment.stop)
            subprocess.run(argv, check=True, capture_output=True)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            signed = [probe / 'powervrprobed', probe / 'lib/libvulkan_powervr_mesa.dylib',
                      bundle / 'NativePowerVRMetal', bundle / 'libvulkan_powervr_mesa.dylib']
            self.assertEqual(commands, [command for path in signed for command in (
                ['--verify', '--strict', str(path)], ['--display', '--verbose=4', str(path)])]
                + [['--verify', '--strict', str(bundle)]])
            entries = {name: (mode, data) for name, mode, data in helper.unpack_addon((root / 'addon').read_bytes())}
            self.assertEqual(len(entries), 6)
            self.assertEqual(entries['bin/powervrprobed'], (0o755, b'probe'))
            self.assertEqual(entries['addons/NativePowerVRVulkan.bundle/libvulkan_powervr_mesa.dylib'][1], b'vulkan')
            self.assertEqual(entries['addons/NativePowerVRMetal.bundle/_CodeSignature/CodeResources'][1], b'seal')
            trust = (root / 'trust').read_bytes()
            self.assertTrue(helper.valid_like_the_loader(trust))
            self.assertEqual(struct.unpack_from('<I', trust, 32)[0], 3)
            self.assertTrue(trust.endswith(original[8:]))
            self.assertEqual(seed.read_bytes(), b'existing Data seed UUID and content')
            self.assertNotEqual(subprocess.run(argv, capture_output=True).returncode, 0)
            (root / 'trust').unlink()
            (root / 'addon').unlink()
            alias = list(argv)
            alias[alias.index('--trust-out') + 1] = str(root / 'addon')
            self.assertNotEqual(subprocess.run(alias, capture_output=True).returncode, 0)
            self.assertFalse((root / 'addon').exists())
            for key in metal_trust:
                bad = dict(metal_trust)
                del bad[key]
                (metal / 'trust.json').write_text(json.dumps(bad))
                self.assertNotEqual(subprocess.run(argv, capture_output=True).returncode, 0)
                self.assertFalse((root / 'addon').exists())
                self.assertFalse((root / 'trust').exists())
            (metal / 'trust.json').write_text(json.dumps(metal_trust))
            # Syntactically valid metadata from another build must never be trusted.
            for directory, key in ((probe, 'daemon_cdhash'), (probe, 'vulkan_cdhash'),
                                   (metal, 'cdhash'), (metal, 'driver_cdhash')):
                metadata_file = directory / 'trust.json'
                saved = metadata_file.read_bytes()
                metadata = json.loads(saved)
                metadata[key] = '99' * 20
                metadata_file.write_text(json.dumps(metadata))
                result = subprocess.run(argv, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'Signed CDHash does not match', result.stderr)
                self.assertFalse((root / 'addon').exists())
                self.assertFalse((root / 'trust').exists())
                metadata_file.write_bytes(saved)
            # Every individual signature and the whole bundle seal are required.
            for path in signed + [bundle]:
                with mock.patch.dict(os.environ, MOCK_VERIFY_FAIL=str(path)):
                    self.assertNotEqual(subprocess.run(argv, capture_output=True).returncode, 0)
                self.assertFalse((root / 'addon').exists())
                self.assertFalse((root / 'trust').exists())
            driver = bundle / 'libvulkan_powervr_mesa.dylib'
            driver.write_bytes(b'other signed build')
            log_before = log.read_bytes()
            result = subprocess.run(argv, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Vulkan copies differ', result.stderr)
            self.assertEqual(log.read_bytes(), log_before)
            self.assertFalse((root / 'addon').exists())
            self.assertFalse((root / 'trust').exists())
            driver.write_bytes(b'vulkan')
            board.write_text('[payload]\ntrust_cache_sha256 = []\n')
            self.assertNotEqual(subprocess.run(argv, capture_output=True).returncode, 0)
            self.assertFalse((root / 'addon').exists())
            self.assertFalse((root / 'trust').exists())


if __name__ == '__main__':
    unittest.main()
