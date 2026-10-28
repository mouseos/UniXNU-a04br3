#!/usr/bin/env python3
"""Construct a comparison addon/trust while preserving the iostat4 seed and GPU files."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    'scratch-data.img': '1013af9803111b6898e47090c483e5b5a84bf5f451827c21fc257d7a2cbd258f',
    'kernel.macho.gpuaddon': '35a77e01114467f0f9d4c16fbfa24e76903968ff68e8d82812acd2980ee00fa3',
    'kernel.macho.trustcache': '7123f78d892a993771586eebf27a76abdcd2bf68afe60bc97b7002c49df8dc1c',
}
PATH = 'bin/a04br3-memory-copy-bench'


def sha(data): return hashlib.sha256(data).hexdigest()


def build(baseline, executable, cdhash, destination, helper):
    if not re.fullmatch('[0-9a-f]{40}', cdhash):
        raise ValueError('invalid benchmark CDHash')
    originals = {name: (baseline/name).read_bytes() for name in EXPECTED}
    for name, expected in EXPECTED.items():
        if sha(originals[name]) != expected:
            raise ValueError('baseline hash mismatch: ' + name)
    old = helper['unpack_addon'](originals['kernel.macho.gpuaddon'])
    if len({name for name, _, _ in old}) != len(old) or any(name == PATH for name, _, _ in old):
        raise ValueError('baseline addon has duplicate or benchmark path')
    new = old + [(PATH, 0o755, executable.read_bytes())]
    addon = helper['pack_addon'](new)
    if helper['unpack_addon'](addon) != new:
        raise ValueError('addon roundtrip mismatch')
    trust = helper['owner_bundle'](originals['kernel.macho.trustcache'], [cdhash])
    destination.mkdir(parents=True, exist_ok=False)
    (destination/'gpuaddon').write_bytes(addon)
    (destination/'trustcache').write_bytes(trust)
    report = {'baseline_sha256': EXPECTED, 'baseline_entries': [
        {'path': name, 'mode': mode, 'bytes': len(data), 'sha256': sha(data)} for name, mode, data in old],
        'benchmark_sha256': sha(executable.read_bytes()), 'benchmark_cdhash': cdhash,
        'gpuaddon_sha256': sha(addon), 'trust_sha256': sha(trust),
        'seed_unchanged': True, 'gpu_entries_unchanged': True}
    (destination/'matched-inputs.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def verify(baseline, executable, destination, helper):
    report = json.loads((destination/'matched-inputs.json').read_text())
    if report['baseline_sha256'] != EXPECTED:
        raise ValueError('baseline report differs from required input pins')
    for name, expected in EXPECTED.items():
        if sha((baseline/name).read_bytes()) != expected:
            raise ValueError('baseline input changed: ' + name)
    old = helper['unpack_addon']((baseline/'kernel.macho.gpuaddon').read_bytes())
    addon = (destination/'gpuaddon').read_bytes()
    new = helper['unpack_addon'](addon)
    if new != old + [(PATH, 0o755, executable.read_bytes())] or sha(addon) != report['gpuaddon_sha256']:
        raise ValueError('addon no longer preserves the baseline and benchmark')
    trust = (destination/'trustcache').read_bytes()
    if trust != helper['owner_bundle']((baseline/'kernel.macho.trustcache').read_bytes(), [report['benchmark_cdhash']]) or sha(trust) != report['trust_sha256']:
        raise ValueError('trust no longer preserves baseline modules')
    if sha(executable.read_bytes()) != report['benchmark_sha256']:
        raise ValueError('benchmark changed')
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--bench-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    a = parser.parse_args()
    executable = a.bench_dir/'a04br3-memory-copy-bench'
    cdhash = json.loads((a.bench_dir/'trust.json').read_text())['memory_bench_cdhash'].lower()
    subprocess.run(['codesign', '--verify', '--strict', str(executable)], check=True)
    detail = subprocess.run(['codesign', '--display', '--verbose=4', str(executable)], capture_output=True, text=True, check=True).stderr
    actual = re.findall(r'^CDHash=([a-fA-F0-9]{40})$', detail, re.M)
    if len(actual) != 1 or actual[0].lower() != cdhash:
        raise SystemExit('benchmark CDHash differs from signature')
    helper = runpy.run_path(str(ROOT/'scripts/build-a04br3-powervr-probe.py'))
    if a.verify:
        report = verify(a.baseline, executable, a.out, helper)
        if report['benchmark_cdhash'] != cdhash:
            raise SystemExit('benchmark CDHash changed')
    else:
        report = build(a.baseline, executable, cdhash, a.out, helper)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
