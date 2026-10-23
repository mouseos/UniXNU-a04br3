#!/usr/bin/env python3
"""Stage the PowerVR user-space probe (scripts/build-a04br3-powervr-probe.py output) into a copy of a packed Data seed.

  a04br3-seed-powervr-probe.py --tree TREE --probe-dir DIR [--launchd-job] BASE.dbr OUTPUT.dbr
  a04br3-seed-powervr-probe.py --probe-dir DIR --dry-run        (any host: list what would be staged)

Adds, owned by root:wheel:
  /private/var/ngpu/bin/powervrprobed                    0755  (DIR/powervrprobed)
  /private/var/ngpu/addons/NativePowerVRVulkan.bundle/libvulkan_powervr_mesa.dylib  0755  (DIR/lib/..., only with --vulkan;
      visible as /System/Library/Extensions/NativePowerVRVulkan.bundle/ through the GPU addon union mount of UniXNU
      (zzy-native-gpu-addon-mount, kernel argument sheng-gpu-addon=1): the platform sandbox refuses an executable
      mapping of a library below /private/var)
  /private/var/ngpu/daemons/<label>.plist                0644  (with --launchd-job; launchd does not run it, see build-a04br3-powervr-probe.py)
the same paths UniXNU's add-gpu-plugin-data.py and make-gpu-addon.py use for adrenoprobed. The kernel starts the probe from the board
launch hook with a04br3-powervrprobed=1 (package/A04BR3ProbeLaunch.c); the binaries run only with the owner trust bundle of
build-a04br3-powervr-probe.py --trust-out (payload step: --owner-trust-cache).

The packed seed (drivers/DarwinBoot/Test/packed_ramdisk.py of the UniXNU tree TREE) is unpacked into a sparse raw file, attached with
ownership honoured and written with sudo, as scripts/a04br3-seed-launch-policy.py writes the launchd policy, then packed again.
BASE is not modified and the volume UUID is unchanged (XNU_OWNER_DATA_VOLUME_UUID stays valid). A seed whose content changes starts a new
persistent store (native-msdc-persist=2); a04br3-pre-build.sh makes a new seed on every run anyway."""
import argparse
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

LIB = 'libvulkan_powervr_mesa.dylib'
LABEL = 'org.unixnu.a04br3.powervrprobed'


def plan(probe, launchd_job):
    files = [(probe / 'powervrprobed', 'bin/powervrprobed', '0755')]
    if (probe / 'lib' / LIB).is_file():
        files.append((probe / 'lib' / LIB, 'addons/NativePowerVRVulkan.bundle/' + LIB, '0755'))
    if launchd_job:
        files.append((probe / (LABEL + '.plist'), 'daemons/%s.plist' % LABEL, '0644'))
    missing = [str(source) for source, _, _ in files if not source.is_file()]
    if missing:
        sys.exit('Missing probe output (run build-a04br3-powervr-probe.py first): ' + ', '.join(missing))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tree', type=Path, help='UniXNU build tree (for drivers/DarwinBoot/Test/packed_ramdisk.py)')
    parser.add_argument('--probe-dir', type=Path, required=True)
    parser.add_argument('--launchd-job', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('base', type=Path, nargs='?')
    parser.add_argument('output', type=Path, nargs='?')
    args = parser.parse_args()
    files = plan(args.probe_dir.resolve(), args.launchd_job)
    for source, target, mode in files:
        print('%s %10d /private/var/ngpu/%s' % (mode, source.stat().st_size, target))
    if args.dry_run:
        return
    if not (args.tree and args.base and args.output):
        sys.exit('--tree, BASE and OUTPUT are needed (or --dry-run)')
    if sys.platform != 'darwin':
        sys.exit('Staging needs macOS (hdiutil)')
    base, output = args.base.resolve(), args.output.resolve()
    if output.exists():
        sys.exit('Refuse to overwrite ' + str(output))
    sys.path.insert(0, str(args.tree.resolve() / 'drivers/DarwinBoot/Test'))
    import packed_ramdisk
    blob = base.read_bytes()
    logical, extents = packed_ramdisk.regions(blob)
    with tempfile.TemporaryDirectory(prefix='a04br3-powervr-seed-') as work:
        raw = Path(work) / 'seed.raw'
        with raw.open('wb') as f:
            f.truncate(logical)
            for offset, data in extents:
                f.seek(offset)
                f.write(data)
        mount = Path(work) / 'mount'
        mount.mkdir()
        attached = plistlib.loads(subprocess.check_output(
            ['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-nobrowse', '-owners', 'on',
             '-mountpoint', str(mount), '-plist', str(raw)]))
        device = attached['system-entities'][0]['dev-entry']
        try:
            if not (mount / 'mobile').is_dir():
                sys.exit('The Data seed has no /mobile; wrong base image?')
            for source, target, mode in files:
                destination = mount / 'ngpu' / target
                subprocess.run(['sudo', 'install', '-d', '-o', 'root', '-g', 'wheel', '-m', '0755', str(destination.parent)], check=True)
                subprocess.run(['sudo', 'install', '-o', 'root', '-g', 'wheel', '-m', mode, str(source), str(destination)], check=True)
                info = destination.stat()
                if destination.read_bytes() != source.read_bytes() or info.st_uid or info.st_gid or oct(info.st_mode & 0o777)[2:] != mode.lstrip('0'):
                    sys.exit('%s was not written as root:wheel %s with the built bytes' % (destination, mode))
        finally:
            subprocess.run(['hdiutil', 'detach', device], check=True)
        if raw.read_bytes()[32:36] != b'NXSB':
            sys.exit('The edited image is no APFS container any more')
        metadata = packed_ramdisk.pack(raw, output)
    report = dict(base_sha256=packed_ramdisk.inspect(base)[0]['sha256'], output=metadata,
                  staged=['/private/var/ngpu/' + target for _, target, _ in files])
    output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

