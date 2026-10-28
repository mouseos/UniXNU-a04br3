#!/usr/bin/env python3
"""Fail closed if the generated payload differs from the iostat4 comparison contract."""
import hashlib
import json
import os
from pathlib import Path
import sys

BASE_ARGS = 'f7a62ab6e768d33141681fed399508a49cf0924780370282c57d73c40fad85d0'
SEED = '1013af9803111b6898e47090c483e5b5a84bf5f451827c21fc257d7a2cbd258f'
UUID = b'CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36'

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

runner = Path(os.environ['RUNNER_TEMP'])
out = Path(os.path.expandvars(os.path.expanduser(os.environ.get('UNIXNU_OUT') or 'out'))).resolve()
work = runner/'a04br3-memory-matched'
report = json.loads((out/'a04br3/payload/payload.json').read_text())
matched = json.loads((work/'matched-inputs.json').read_text())
kernel = out/'a04br3/kernel/kernel.macho'
line = report['kernel_args']
checks = {
    'baseline_arguments': line.endswith(' mb=1') and hashlib.sha256(line[:-5].encode()).hexdigest() == BASE_ARGS,
    'command_line_limit': len(line.encode()) == 1023,
    'seed_unchanged': report['data_ramdisk_sha256'] == SEED and sha(runner/'a04br3-iostat4-baseline/scratch-data.img') == SEED,
    'gpu_addon': report['gpu_addon_sha256'] == matched['gpuaddon_sha256'] == sha(work/'gpuaddon'),
    'owner_trust': report['trust_cache_sha256'] == matched['trust_sha256'] == sha(work/'trustcache'),
    'kernel': report['kernel_sha256'] == sha(kernel) and UUID in kernel.read_bytes(),
    'payload': report['payload_sha256'] == sha(out/'a04br3/payload/payload.img'),
    'setup_unchanged': report['skip_setup'] is False,
}
if not all(checks.values()):
    sys.exit('matched payload verification failed: '+', '.join(k for k,v in checks.items() if not v))
(work/'payload-verification.json').write_text(json.dumps({'checks':checks, 'payload_sha256':report['payload_sha256'],
    'kernel_sha256':report['kernel_sha256'], 'data_uuid':UUID.decode(), 'hardware_tested':False},indent=2)+'\n')
print('PASS: matched payload inputs, UUID, GPU addon, trust, args and hashes')
