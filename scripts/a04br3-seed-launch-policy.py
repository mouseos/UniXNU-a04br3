#!/usr/bin/env python3
"""Write launchd's disabled-jobs policy into the a04br3 Data seed, the build-time form of package/BootWatchdogPolicy.c.

  a04br3-seed-launch-policy.py --board board.toml OWNER-DATA.cdr

UniXNU calls BootWatchdogPolicy.c (sheng_boot_watchdog_policy) only from zzz-boot-progress.patch under XNU_BOOT_PROGRESS,
which build-xnu-4k-baseline.sh allows only with XNU_NATIVE_UFS; a04br3 has neither. Its Data volume is a RAM disk made
from the seed on every boot (packed_data_ramdisk, ephemeral-storage), so a policy written into the seed is what launchd
reads on every boot, as the kernel policy would leave it:
  /private/var/db/com.apple.xpc.launchd/disabled.plist     root:wheel 0644, the same bytes as BootWatchdogPolicy.c
  /private/var/db/com.apple.xpc.launchd/disabled.migrated  root:wheel 0644, empty (selects launchd's override loader)
The labels come from the [payload] kernel_args of board.toml, sheng-disable-watchdogd=1 and sheng-disable-jobs=<a,b,...>
(the boot arguments the kernel policy reads; the same label character set). Without them nothing is written.

OWNER-DATA.cdr is the raw APFS image of scripts/prepare-owner-data-fixture.py (UniXNU), before packed_ramdisk.py packs it.
It is attached with ownership honoured and written with sudo so the files are owned by root. Its seed.json beside it gets
the new image hash. The volume UUID is unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import tomllib

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>')
TAIL = '</dict></plist>\n'
LIMIT = 768 - 1        # BootWatchdogPolicy.c: policy[768]
JOBS_LIMIT = 200 - 1   # BootWatchdogPolicy.c: jobs[200]


def policy_from(arguments):
    watchdogd = jobs = None
    for item in arguments:
        name, _, value = item.partition('=')
        if name == 'sheng-disable-watchdogd':
            watchdogd = value == '1'
        elif name == 'sheng-disable-jobs':
            jobs = value
    jobs = jobs or ''
    if len(jobs) > JOBS_LIMIT:
        raise SystemExit(f'sheng-disable-jobs is {len(jobs)} characters; the kernel policy reads at most {JOBS_LIMIT}')
    if not re.fullmatch(r'[A-Za-z0-9._,-]*', jobs):
        raise SystemExit('sheng-disable-jobs has a character the kernel policy refuses')
    labels = [label for label in jobs.split(',') if label]
    if not watchdogd and not labels:
        return None
    text = HEAD + ('<key>com.apple.watchdogd</key><true/>' if watchdogd else '')
    text += ''.join(f'<key>{label}</key><true/>' for label in labels) + TAIL
    if len(text) > LIMIT:
        raise SystemExit(f'The policy is {len(text)} bytes; the kernel policy holds at most {LIMIT}')
    expected = {'com.apple.watchdogd': True} if watchdogd else {}
    expected.update({label: True for label in labels})
    if plistlib.loads(text.encode()) != expected:
        raise SystemExit('The policy does not parse back to the requested labels')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--board', type=Path, required=True)
    parser.add_argument('image', type=Path)
    args = parser.parse_args()
    board = tomllib.loads(args.board.read_text())
    policy = policy_from(board.get('payload', {}).get('kernel_args', []))
    if policy is None:
        print('launch policy: no sheng-disable-watchdogd / sheng-disable-jobs in board.toml; seed unchanged')
        return
    image = args.image.resolve()
    with tempfile.TemporaryDirectory(prefix='a04br3-launch-policy-') as work:
        mount = Path(work) / 'mount'
        mount.mkdir()
        source = Path(work) / 'disabled.plist'
        source.write_text(policy)
        attached = plistlib.loads(subprocess.check_output(
            ['hdiutil', 'attach', '-nobrowse', '-owners', 'on', '-mountpoint', str(mount), '-plist', str(image)]))
        device = attached['system-entities'][0]['dev-entry']
        try:
            if not (mount / 'mobile').is_dir():
                raise SystemExit('The image has no /mobile: not the Data seed')
            directory = mount / 'db/com.apple.xpc.launchd'
            if not directory.is_dir():
                subprocess.run(['sudo', 'install', '-d', '-o', 'root', '-g', 'wheel', '-m', '0755', str(directory)], check=True)
            subprocess.run(['sudo', 'install', '-o', 'root', '-g', 'wheel', '-m', '0644', str(source),
                            str(directory / 'disabled.plist')], check=True)
            marker = directory / 'disabled.migrated'
            if not marker.exists():
                empty = Path(work) / 'empty'
                empty.write_bytes(b'')
                subprocess.run(['sudo', 'install', '-o', 'root', '-g', 'wheel', '-m', '0644', str(empty), str(marker)], check=True)
            written = (directory / 'disabled.plist')
            info = written.stat()
            if written.read_text() != policy or info.st_uid != 0 or info.st_gid != 0 or (info.st_mode & 0o777) != 0o644:
                raise SystemExit('disabled.plist was not written as root:wheel 0644 with the policy bytes')
        finally:
            subprocess.run(['hdiutil', 'detach', device], check=True)
    report = image.parent / 'seed.json'
    if report.is_file():
        data = json.loads(report.read_text())
        data['sha256'] = hashlib.sha256(image.read_bytes()).hexdigest()
        data['launchd_disabled_policy_sha256'] = hashlib.sha256(policy.encode()).hexdigest()
        report.write_text(json.dumps(data, indent=2) + '\n')
    print(f'launch policy: {len(policy)} bytes written to {image.name}: {policy}')


if __name__ == '__main__':
    main()
