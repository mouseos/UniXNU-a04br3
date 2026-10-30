#!/usr/bin/env python3
"""Verify pinned lifecycle inputs and safe unittest discovery."""
from pathlib import Path
import os
import subprocess
import sys
import unittest

script = Path(__file__).with_name('test_powervr_gpu_lifecycle.py')
pins = ('A04BR3_DEVICE_ROOT', 'A04BR3_DEVICE_COMMIT', 'UNIXNU_BASE_ROOT',
        'UNIXNU_BASE_COMMIT', 'NATIVE_MOBILE_DISPLAY_REPO', 'NATIVE_MOBILE_DISPLAY_COMMIT')

class LifecyclePinsTest(unittest.TestCase):
    def test_discovery_without_pins_skips(self):
        env = dict(os.environ)
        for name in pins:
            env.pop(name, None)
        result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(script.parent),
                                 '-p', script.name, '-v'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('skipped', result.stderr)
        self.assertIn('six explicit fixed-source inputs', result.stderr)

    def test_bad_and_partial_pins_rejected(self):
        if not any(os.environ.get(name) for name in pins):
            self.skipTest('pin rejection tests require six explicit fixed-source inputs')
        missing = [name for name in pins if not os.environ.get(name)]
        if missing:
            self.fail('partial pin configuration: ' + ', '.join(missing))
        def refused(name, value, text):
            env = dict(os.environ)
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
            result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, name)
            self.assertIn(text, result.stderr)
        refused('NATIVE_MOBILE_DISPLAY_COMMIT', None, 'is required')
        refused('NATIVE_MOBILE_DISPLAY_COMMIT', '279cf9b', 'full lowercase commit hash')
        refused('A04BR3_DEVICE_COMMIT', 'f2dee95', 'full lowercase commit hash')
        refused('NATIVE_MOBILE_DISPLAY_COMMIT', '5c75f253a01d3d428b9e60d10c3dda3e6ed47ead',
                'does not match the pinned base gitlink')

if __name__ == '__main__':
    unittest.main()
