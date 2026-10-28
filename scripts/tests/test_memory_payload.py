#!/usr/bin/env python3
"""The wrapper chooses generated inputs and refuses mismatched staged bytes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    runner = base / 'runner'
    runner.mkdir()
    (runner / 'data.dbr').write_bytes(b'data')
    (runner / 'trustcache').write_bytes(b'trust')
    sha = lambda x: hashlib.sha256(x).hexdigest()
    (runner / 'a04br3-memory-inputs.json').write_text(json.dumps({'data_sha256': sha(b'data'), 'trust_sha256': sha(b'trust')}))
    (base / 'unixnu').write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$RECORD"\n')
    (base / 'unixnu').chmod(0o755)
    env = dict(os.environ, DEVICE_DIR=str(root), RUNNER_TEMP=str(runner), RECORD=str(base/'args'))
    command = ['bash', str(root/'scripts/a04br3-memory-payload.sh')]
    subprocess.run(command, cwd=base, env=env, check=True)
    args = (base/'args').read_text().splitlines()
    for required in ('a04br3-membench=1', 'sheng-fb-gpu=1', '--owner-trust-cache', str(runner/'trustcache'), '--data-ramdisk', str(runner/'data.dbr')):
        assert required in args, required
    probe = base / 'probe'
    probe.mkdir()
    (probe / 'powervrprobed').write_bytes(b'probe')
    bench = base / 'bench'
    bench.write_bytes(b'signed benchmark')
    stage = subprocess.run(['python3', str(root/'scripts/a04br3-seed-powervr-probe.py'),
                            '--probe-dir', str(probe), '--memory-bench', str(bench), '--dry-run'],
                           capture_output=True, text=True, check=True)
    assert '/private/var/ngpu/bin/a04br3-memory-copy-bench' in stage.stdout
    bench.unlink()
    assert subprocess.run(['python3', str(root/'scripts/a04br3-seed-powervr-probe.py'),
                           '--probe-dir', str(probe), '--memory-bench', str(bench), '--dry-run'],
                          capture_output=True).returncode != 0
    (base/'args').unlink()
    (runner/'data.dbr').write_bytes(b'changed')
    assert subprocess.run(command, cwd=base, env=env, capture_output=True).returncode != 0
    assert not (base/'args').exists()
print('PASS: generated inputs, GPU argument and stale input rejection')
