#!/usr/bin/env python3
"""Verify the lifecycle harness rejects missing, abbreviated and wrong gitlink pins."""
from pathlib import Path
import os
import subprocess
import sys

script = Path(__file__).with_name('test_powervr_gpu_lifecycle.py')
base = dict(os.environ)
def refused(name, value, text):
    env = dict(base)
    if value is None:
        env.pop(name, None)
    else:
        env[name] = value
    result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and text in result.stderr, (name, result.returncode, result.stderr)

refused('NATIVE_MOBILE_DISPLAY_COMMIT', None, 'is required')
refused('NATIVE_MOBILE_DISPLAY_COMMIT', '279cf9b', 'full lowercase commit hash')
refused('A04BR3_DEVICE_COMMIT', 'f2dee95', 'full lowercase commit hash')
refused('NATIVE_MOBILE_DISPLAY_COMMIT', '5c75f253a01d3d428b9e60d10c3dda3e6ed47ead',
        'does not match the pinned base gitlink')
print('PASS lifecycle pins: missing, abbreviated, wrong gitlink rejected')
