#!/usr/bin/env python3
"""Run the actual FB/classify functions with host-only command/log fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1] / 'run-a04br3-unattended.sh'
text = SOURCE.read_text()
fb = next(line for line in text.splitlines() if line.startswith('FB() {'))
classify = text.split('  classify() {\n', 1)[1].split('\n  }', 1)[0]
assert 'MODE=${MODE:-flash}' in text
with tempfile.TemporaryDirectory(prefix='a04br3-runner-') as temporary:
    root = Path(temporary)
    commands = root / 'commands with spaces'
    commands.mkdir()
    fastboot = commands / 'fastboot'
    fastboot.write_text('#!' + sys.executable + '\n' + '''import json, os, sys
with open(os.environ['ARGV_LOG'], 'w') as stream:
    json.dump(sys.argv[1:], stream)
sys.exit(int(os.environ['MOCK_FB_EXIT']))
''')
    fastboot.chmod(0o755)
    timeout = commands / 'timeout'
    wrapper = '#!' + sys.executable + '\n' + '''import json, os, sys
with open(os.environ['TIMEOUT_LOG'], 'w') as stream:
    json.dump(sys.argv[1:], stream)
os.execvp(sys.argv[2], sys.argv[2:])
'''
    for timed in (False, True):
        if timed:
            timeout.write_text(wrapper)
            timeout.chmod(0o755)
        for status in (0, 23):
            argv = ['-s', 'serial with spaces', 'flash', 'boot', str(root / 'image with spaces.img')]
            env = dict(os.environ, PATH=str(commands), ARGV_LOG=str(root / 'argv.json'),
                       TIMEOUT_LOG=str(root / 'timeout.json'), MOCK_FB_EXIT=str(status), FB_TIMEOUT='17')
            result = subprocess.run(['/bin/bash', '-c', fb + '\nFB "$@"', 'fixture', *argv], env=env)
            assert result.returncode == status
            assert json.loads((root / 'argv.json').read_text()) == argv
            if timed:
                assert json.loads((root / 'timeout.json').read_text()) == ['17', 'fastboot', *argv]
            else:
                assert not (root / 'timeout.json').exists()
    run = root / 'round with spaces'
    run.mkdir()
    fixtures = [
        ('A04BR3 SEC: test cycle: fastboot flag set, watchdog armed for 31 s\n', 'sec'),
        ('A04BR3 SEC: RTC fastboot verified; WDT 31 s armed before memory map\n', 'sec'),
        ('A04BR3 recovery: RTC verified; WDT 31 s, feeding deadline 120 s\n', 'dxe'),
        ('A04BR3TestCycleDxe: fastboot flag set\n', 'dxe'),
        ('A04BR3 recovery: 120 s firmware deadline; WDT feeding stopped\n', 'firmware-timeout'),
        ('A04BR3TestCycleDxe: no ExitBootServices within 120 s\n', 'firmware-timeout'),
        ('A04BR3 recovery: EBS, RTC fastboot FAILED, WDT armed 31 s; feeding stopped\n', 'exit-boot-services'),
        ('A04BR3TestCycleDxe: ExitBootServices: fastboot flag set\n', 'exit-boot-services'),
        ('a04br3-autoreset: fastboot flag NOT set, reset after 7200 s\n', 'bsd_init'),
        ('A04BR3 recovery: DXE RTC unverified; smoke refused, normal boot continues without feeding\n', 'dxe'),
        ('A04BR3 SEC: test cycle: fastboot flag set\nA04BR3 RECOVERY SMOKE: SEC armed/RTC verified; deadloop without feeding\n', 'sec'),
        ('A04BR3 recovery: RTC verified; WDT 31 s, feeding deadline 120 s\nA04BR3 RECOVERY SMOKE: DXE deadloop with timer live\npanic(first)\npanic(second)\nBOOT_REASON=2\n', 'dxe'),
    ]
    for log, expected in fixtures:
        (run / 'uart.log').write_text(log)
        subprocess.run(['/bin/bash', '-c', 'classify() {\n' + classify + '\n}\nclassify'],
                       env=dict(os.environ, RUN=str(run)), check=True)
        report = (run / 'stage.txt').read_text()
        assert report.splitlines()[0] == 'stage: ' + expected, report
        assert ('smoke observed:' in report) == ('A04BR3 RECOVERY SMOKE:' in log)
        assert 'success' not in report.lower() and 'returned to fastboot' not in report.lower()
        if 'panic(first)' in log:
            assert 'first panic: panic(first)' in report and 'panic(second)' not in report
    # Execute the source assignments; do not launch the runner or any device tool.
    assignments = '\n'.join(line for line in text.splitlines()
                            if line.startswith(('RUNS_ROOT=', 'RUNS=')))
    for override in ('', str(root / 'external logs with spaces')):
        env = dict(os.environ, ROOT=str(root))
        env.pop('RUNS_ROOT', None)
        if override:
            env['RUNS_ROOT'] = override
        actual = subprocess.check_output(['/bin/bash', '-c', assignments + '\nprintf "%s" "$RUNS"'], env=env, text=True)
        assert Path(actual).parent == Path(override or root / 'out/a04br3/unattended')
print('PASS: actual FB argv/status with and without timeout, serial/space paths; actual recovery/SMOKE classifier; RUNS_ROOT; default flash')
