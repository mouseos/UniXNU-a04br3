#!/usr/bin/env python3
"""Exercise refusal paths without network, a runner or a device."""
import os
from pathlib import Path
import subprocess
import tempfile

SCRIPT = Path(__file__).resolve().parents[1] / 'a04br3-read-batch-iostat-runner.sh'
with tempfile.TemporaryDirectory(prefix='iostat-runner-test-') as directory:
    root = Path(directory)
    task = root / 'io-read-batch-iostat-test'
    task.mkdir()
    env = dict(os.environ, RUNNER_TEMP=str(root), BATCH_PREP_COMMIT='a' * 40)
    def run(target, **changes):
        return subprocess.run(['bash', str(SCRIPT), str(target)], env={**env, **changes},
                              capture_output=True, text=True)
    result = run(task)
    assert result.returncode == 3 and 'inputs unavailable' in result.stderr
    assert not (task / 'preparation').exists() and not (task / 'pair').exists()
    assert run(task, BATCH_PREP_COMMIT='bad').returncode == 2
    assert run(root / 'other').returncode == 2
    prior = root / 'io-read-batch-e8f-20261029b'
    (prior / 'input-records').mkdir(parents=True)
    (prior / 'input-records/inputs.json').write_text('{}')
    (task / 'pair').mkdir()
    result = run(task)
    assert result.returncode == 3 and 'already used' in result.stderr
    assert not (task / 'preparation').exists()
print('PASS isolated iostat runner refusal paths without network or device access')
