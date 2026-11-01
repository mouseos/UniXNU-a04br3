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
import shutil
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
    parser.add_argument('--bundle', action='store_true',
                        help='copy every input into DIR and write relative paths, SHA256SUMS and FLASH.md, so that DIR is '
                             'one self-contained package: run "mtk script write.mtk" inside DIR')
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
        if args.bundle:
            local = args.out / path.name
            if path.resolve() != local.resolve():
                if local.exists():
                    raise SystemExit(f'{local}: refuse to overwrite a bundled input')
                shutil.copyfile(path, local)
            shown = path.name
        else:
            shown = str(path.resolve())
        if ' ' in shown:
            # mtkclient script splits each line at spaces.
            raise SystemExit(f'{shown}: mtkclient scripts cannot take paths with spaces')
        if size > capacity:
            raise SystemExit(f'{path}: {size} bytes do not fit {name} ({capacity} bytes)')
        lines.append(f'wo {offset:#x} {size:#x} {shown}')
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
    if args.bundle:
        names = sorted({line.split()[3] for line in lines if line.startswith('wo ')} | {'write.mtk', 'flash.json'})
        (args.out / 'SHA256SUMS').write_text(''.join(f'{sha256(args.out / n)}  {n}\n' for n in names))
        rows = '\n'.join(f"| {w['target']} | {w['offset']:#x} | {w['bytes']} | {w['capacity']} |" for w in report['writes'])
        (args.out / 'FLASH.md').write_text(FLASH_TEXT.format(rows=rows))
    print(f'mtk script {args.out / "write.mtk"}')


FLASH_TEXT = '''# a04br3 first installation with mtkclient

Generated by scripts/make-a04br3-flash.py --bundle. Every file named in write.mtk is in this directory.

1. Check the files: sha256sum -c SHA256SUMS
2. The tablet must be bootloader-unlocked (LK shows "Orange State").
3. Power the tablet off, start mtkclient in this directory and connect the tablet in BROM or preloader mode
   (hold the volume key the stock procedure names while connecting USB):

       mtk script write.mtk

4. The script writes the partitions below, the backup GPT, then the primary GPT last (an interrupted write leaves the
   previous table in place), and resets the tablet. Later images go through LK fastboot without mtkclient
   (README "Unattended test cycle").

Only the partitions below change. preloader, lk, nvram, proinfo, protect1/2, persist, seccfg and the other stock
partitions keep their contents and their places; device-specific data (MAC address, calibration) is never part of
this package.

| target | byte offset | bytes | capacity |
| --- | --- | --- | --- |
{rows}
'''


if __name__ == '__main__':
    main()
