#!/usr/bin/env python3
"""Host-only contract tests for the separately staged iostat ROA candidate."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'iostat_roa_candidate', ROOT / 'scripts/a04br3-iostat-roa-rows-candidate-build.py')
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def owner_module(hashes):
    """Produce the smallest valid first owner module accepted by the decoder."""
    body = bytearray(24 + 24 * len(hashes))
    struct.pack_into('<I', body, 0, 2)
    struct.pack_into('<I', body, 20, len(hashes))
    for index, value in enumerate(hashes):
        body[24 + 24 * index:44 + 24 * index] = bytes.fromhex(value)
    return struct.pack('<II', 1, 8) + body


class CandidateContract(unittest.TestCase):
    hashes = ('0123456789abcdef0123456789abcdef01234567',
              '89abcdef0123456789abcdef0123456789abcdef')
    uuid = '00112233-4455-6677-8899-AABBCCDDEEFF'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.device = self.root / 'device'
        self.inputs_dir = self.root / 'inputs'
        (self.source / 'scripts').mkdir(parents=True)
        (self.device / 'scripts').mkdir(parents=True)
        self._write(self.source / 'scripts/seed-data-grow.py', b'grow')
        self._write(self.device / 'scripts/a04br3-seed-powervr-probe.py', b'stager')
        self._write(self.device / 'scripts/a04br3-pre-build.sh', b'pre-build')
        self._write(self.device / 'scripts/make-a04br3-gpu-addon.py', b'addon-builder')
        self.inputs_dir.mkdir()
        self.manifest, self.kp, self.fp, self.seed, self.official_dp, self.official_records = self._fixture()
        self.manifest_path = self.root / 'inputs.json'
        self.manifest_path.write_text(json.dumps(self.manifest))

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _entry(self, path):
        return {'path': str(path), 'sha256': digest(path)}

    def _json(self, name, value):
        path = self.inputs_dir / name
        path.write_text(json.dumps(value))
        return path

    def _fixture(self):
        original = {}
        for name in ('data', 'trust', 'gpu'):
            original[name] = self._write(self.inputs_dir / ('original-' + name), ('old-' + name).encode())
        data = self._write(self.inputs_dir / 'data', b'new-data')
        trust = self._write(self.inputs_dir / 'trust', owner_module(self.hashes))
        gpu = self._write(self.inputs_dir / 'gpu', b'gpu')
        base = self._write(self.inputs_dir / 'original-trustcache', b'base-trust')
        source_data = self._write(self.inputs_dir / 'seed-source.dbr', b'seed')
        grown_data = self._write(self.inputs_dir / 'grown.dbr', b'grown')
        plugin = self.inputs_dir / 'metal-plugin'
        executable = self._write(plugin / 'NativePowerVRMetal.bundle/NativePowerVRMetal', b'plugin')
        plugin_trust = self._json('plugin-trust.json', {'plugin_cdhash': self.hashes[0].upper()})
        probe_trust = self._json('probe-trust.json', {'probe_cdhash': self.hashes[1]})
        report = self._json('seed-report.json', {'metal_mode': 'publish', 'staged': [
            '/private/var/ngpu/bin/powervrprobed',
            '/private/var/ngpu/addons/NativePowerVRMetal.bundle/NativePowerVRMetal',
            '/private/var/ngpu/addons/NativePowerVRMetal.bundle/libvulkan_powervr_mesa.dylib',
            '/private/var/ngpu/addons/NativePowerVRMetal.publish']})
        kp = {'data_uuid': self.uuid}
        fp = {}
        seed = {'volume_uuid': self.uuid}
        official_records = {
            'kernel_provenance': b'kernel record', 'firmware_provenance': b'firmware record',
            'seed_provenance': json.dumps(seed).encode(), 'data_provenance': b'official provenance',
            'pre_build_log': b'pre-build log'}
        official_dp = {
            'method': 'official-pre-build-from-ipsw', 'source_commit': BUILD.UNIXNU,
            'device_commit': BUILD.DEVICE,
            'pre_build_sha256': digest(self.device / 'scripts/a04br3-pre-build.sh'),
            'seed_record_sha256': hashlib.sha256(official_records['seed_provenance']).hexdigest(),
            'pre_build_log_sha256': hashlib.sha256(official_records['pre_build_log']).hexdigest(),
            **{name + '_sha256': digest(path) for name, path in original.items()}}
        expected = list(sorted(self.hashes))
        candidate = {
            'schema': 4, 'kind': 'iostat-roa-rows-candidate',
            'fixed_sources': {'unixnu': BUILD.UNIXNU, 'device': BUILD.DEVICE, 'uefi': BUILD.UEFI},
            'original_pre_build': {name: hashlib.sha256(value).hexdigest() for name, value in official_records.items()},
            'original_inputs': {name: self._entry(path) for name, path in original.items()},
            'staged_seed': {
                'expected_volume_uuid': self.uuid, 'source_data': self._entry(source_data),
                'source_data_sha256': digest(source_data), 'grown_data': self._entry(grown_data),
                'grown_data_sha256': digest(grown_data), 'data': self._entry(data),
                'data_sha256': digest(data), 'report': self._entry(report),
                'grow_script_sha256': digest(self.source / 'scripts/seed-data-grow.py'),
                'seed_stager_sha256': digest(self.device / 'scripts/a04br3-seed-powervr-probe.py'),
                'metal_mode': 'publish', 'cli': ['--metal-mode', 'publish', '--expected-volume-uuid', self.uuid]},
            'plugin': {'path': str(plugin), 'executable_sha256': digest(executable),
                       'trust_record': self._entry(plugin_trust), 'probe_trust_record': self._entry(probe_trust),
                       'verified_hashes': expected},
            'derived_trust': {'base': self._entry(base), 'base_sha256': digest(base),
                              'output': self._entry(trust), 'sha256': digest(trust),
                              'decoded_owner_hashes': expected},
            'gpu_addon': {**self._entry(gpu), 'builder_sha256': digest(self.device / 'scripts/make-a04br3-gpu-addon.py')}}
        record = self._json('candidate-provenance.json', candidate)
        manifest = {'candidate_provenance': str(record)}
        return manifest, kp, fp, seed, official_dp, official_records

    def _check(self, **overrides):
        values = {'manifest': self.manifest, 'kp': self.kp, 'seed': self.seed,
                  'official_dp': self.official_dp, 'official_records': self.official_records}
        values.update(overrides)
        return BUILD.check_candidate_contract(
            self.manifest_path, values['manifest'], self.source, self.device,
            {'data': self.inputs_dir / 'data', 'trust': self.inputs_dir / 'trust', 'gpu': self.inputs_dir / 'gpu'},
            values['kp'], self.fp, values['seed'], values['official_dp'], values['official_records'])

    def test_contract_accepts_fixture_and_returns_seed_uuid(self):
        record, seed_uuid = self._check()
        self.assertEqual(record.name, 'candidate-provenance.json')
        self.assertEqual(seed_uuid, self.uuid)

    def test_cdhash_helpers_reject_unsigned_trust_and_malformed_owner_module(self):
        unsigned = self._json('unsigned-trust.json', {'note': 'no signing hash'})
        with self.assertRaisesRegex(ValueError, 'no valid CDHashes'):
            BUILD._cdhashes_from_trust(unsigned, 'unsigned trust')
        malformed = self._write(self.inputs_dir / 'malformed-trust', b'\x01\x00\x00\x00\x08\x00\x00\x00')
        with self.assertRaisesRegex(ValueError, 'owner module'):
            BUILD._decoded_owner_cdhashes(malformed)

    def test_contract_rejects_unsigned_trust_record(self):
        provenance = json.loads((self.inputs_dir / 'candidate-provenance.json').read_text())
        plugin_trust = Path(provenance['plugin']['trust_record']['path'])
        plugin_trust.write_text(json.dumps({'note': 'unsigned'}))
        provenance['plugin']['trust_record']['sha256'] = digest(plugin_trust)
        (self.inputs_dir / 'candidate-provenance.json').write_text(json.dumps(provenance))
        with self.assertRaisesRegex(ValueError, 'no valid CDHashes'):
            self._check()

    def test_contract_rejects_missing_decoded_hash(self):
        provenance = json.loads((self.inputs_dir / 'candidate-provenance.json').read_text())
        provenance['derived_trust']['decoded_owner_hashes'] = [self.hashes[0]]
        (self.inputs_dir / 'candidate-provenance.json').write_text(json.dumps(provenance))
        with self.assertRaisesRegex(ValueError, 'does not actually encode'):
            self._check()

    def test_contract_rejects_independent_seed_uuid_mismatch(self):
        seed = copy.deepcopy(self.seed)
        seed['volume_uuid'] = 'FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF'
        with self.assertRaisesRegex(ValueError, 'seed UUID'):
            self._check(seed=seed)

    def test_contract_rejects_tampered_old_record(self):
        records = dict(self.official_records)
        records['pre_build_log'] = b'tampered old record'
        with self.assertRaisesRegex(ValueError, 'original pre-build record hash mismatch'):
            self._check(official_records=records)

    def test_contract_rejects_changed_staged_file_before_consuming_it(self):
        staged = self.inputs_dir / 'data'
        staged.write_bytes(b'changed after staging')
        with mock.patch.object(BUILD, '_decoded_owner_cdhashes') as decode:
            with self.assertRaisesRegex(ValueError, 'candidate seed staging provenance mismatch'):
                self._check()
        decode.assert_not_called()


if __name__ == '__main__':
    unittest.main()

# The runner embeds Python in a shell heredoc; bash -n does not parse it.
runner = (Path(__file__).resolve().parents[1] / 'a04br3-iostat-roa-rows-candidate-runner.sh').read_text()
start = runner.index("<<'PY'\n") + len("<<'PY'\n")
end = runner.index('\nPY\n', start)
compile(runner[start:end], 'candidate-runner-embedded', 'exec')
