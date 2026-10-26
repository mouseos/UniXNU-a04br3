#!/usr/bin/env python3
"""Write an mtkclient script (mtk script DIR/write.mtk) that installs a complete a04br3 UniXNU image.

Inputs are the build outputs: the boot image (unixnu bootimg: BootShim, the uefi-a04br3 firmware volume and the payload)
and the userdata image (unixnu build userdata: the IPSW OS and SystemOS disk images behind the SHNGROOT manifest).
The partition offsets come from scripts/a04br3-gpt.json, the layout scripts/make-a04br3-gpt.py writes. The GPT goes
last, so an interrupted write leaves the previous table in place. Nothing here touches a device.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boot', type=Path, required=True)
    parser.add_argument('--userdata', type=Path, help='userdata-root.img; omitted: boot and GPT only')
    parser.add_argument('--root-policy', type=Path,
                        help='directory of scripts/make-a04br3-root-policy.py (rootpolicy.img, rootpolicy.json): written into '
                             'userdata right after the image')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    layout = json.loads((HERE / 'a04br3-gpt.json').read_text())
    sector = layout['sector_bytes']
    parts = {p['name']: p for p in layout['partitions']}
    args.out.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, HERE / 'make-a04br3-gpt.py', '--out', args.out], check=True, stdout=subprocess.DEVNULL)
    gpt = json.loads((args.out / 'gpt.json').read_text())
    lines, report = [], {'inputs': {}, 'writes': []}

    # The stock LK cannot boot a malformed image and nothing would set the fastboot flag: refuse it here.
    subprocess.run([sys.executable, HERE / 'check-a04br3-bootimg.py', args.boot], check=True)

    def write(name, path, offset, capacity):
        size = path.stat().st_size
        if ' ' in str(path.resolve()):
            # mtkclient script splits each line at spaces.
            raise SystemExit(f'{path.resolve()}: mtkclient scripts cannot take paths with spaces')
        if size > capacity:
            raise SystemExit(f'{path}: {size} bytes do not fit {name} ({capacity} bytes)')
        lines.append(f'wo {offset:#x} {size:#x} {path.resolve()}')
        report['writes'].append({'target': name, 'offset': offset, 'bytes': size, 'capacity': capacity})

    write('boot', args.boot, parts['boot']['first_lba'] * sector, parts['boot']['sectors'] * sector)
    report['inputs']['boot'] = {'path': str(args.boot.resolve()), 'sha256': sha256(args.boot)}
    if args.userdata:
        write('userdata', args.userdata, parts['userdata']['first_lba'] * sector, parts['userdata']['sectors'] * sector)
        report['inputs']['userdata'] = {'path': str(args.userdata.resolve()), 'sha256': sha256(args.userdata)}
    if args.root_policy:
        policy = json.loads((args.root_policy / 'rootpolicy.json').read_text())
        policy_offset = policy['userdata_offset']
        if type(policy_offset) is not int or policy_offset < 0 or policy_offset % sector:
            raise SystemExit('root policy offset must be a nonnegative sector-aligned integer')
        image = args.root_policy / 'rootpolicy.img'
        if sha256(image) != policy['sha256'] or image.stat().st_size != policy['bytes']:
            raise SystemExit(f'{image} does not match rootpolicy.json')
        if args.userdata and policy['userdata_offset'] < args.userdata.stat().st_size:
            raise SystemExit('the root policy would overlap the userdata image')
        first = parts['userdata']['first_lba'] * sector
        write('rootpolicy', image, first + policy['userdata_offset'],
              parts['userdata']['sectors'] * sector - policy['userdata_offset'])
        report['inputs']['rootpolicy'] = {'path': str(image.resolve()), 'sha256': policy['sha256'],
                                          'userdata_offset': policy['userdata_offset']}
    write('gpt-secondary', args.out / 'gpt-secondary.bin', gpt['secondary_byte_offset'], (args.out / 'gpt-secondary.bin').stat().st_size)
    write('gpt-primary', args.out / 'gpt-primary.bin', 0, (args.out / 'gpt-primary.bin').stat().st_size)
    # Reboot when the writes are done, so the first boot starts without touching the tablet.
    lines.append('reset')
    (args.out / 'write.mtk').write_text('\n'.join(lines) + '\n')
    (args.out / 'flash.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'mtk script {args.out / "write.mtk"}')


if __name__ == '__main__':
    main()
