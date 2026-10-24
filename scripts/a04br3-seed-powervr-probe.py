#!/usr/bin/env python3
"""Stage the PowerVR user-space probe (scripts/build-a04br3-powervr-probe.py output) into a copy of a packed Data seed.

  a04br3-seed-powervr-probe.py --tree TREE --probe-dir DIR [--launchd-job] BASE.dbr OUTPUT.dbr
  a04br3-seed-powervr-probe.py --tree TREE --probe-dir DIR --metal-plugin PLUGIN_DIR [--metal-mode selftest|publish]
                               [--metal-clients NAME,...] BASE.dbr OUTPUT.dbr
  a04br3-seed-powervr-probe.py --probe-dir DIR --dry-run        (any host: list what would be staged)

Adds, owned by root:wheel:
  /private/var/ngpu/bin/powervrprobed                    0755  (DIR/powervrprobed)
  /private/var/ngpu/addons/NativePowerVRVulkan.bundle/libvulkan_powervr_mesa.dylib  0755  (DIR/lib/..., only with --vulkan;
      visible as /System/Library/Extensions/NativePowerVRVulkan.bundle/ through the GPU addon union mount of UniXNU
      (zzy-native-gpu-addon-mount, kernel argument sheng-gpu-addon=1): the platform sandbox refuses an executable
      mapping of a library below /private/var)
  /private/var/ngpu/daemons/<label>.plist                0644  (with --launchd-job; launchd does not run it, see build-a04br3-powervr-probe.py)
With --metal-plugin (scripts/build-a04br3-metal-plugin.py --out PLUGIN_DIR), also:
  /private/var/ngpu/addons/NativePowerVRMetal.bundle/...  the plugin bundle (executable and driver 0755, the rest 0644), visible as
      /System/Library/Extensions/NativePowerVRMetal.bundle, where the original Metal discovery looks for the accelerator's
      MetalPluginName (package/powervr/NativePowerVRMetal.cpp, kernel argument a04br3-gpu-metal=1)
  /private/var/ngpu/addons/NativePowerVRMetal.<mode>     0644  the plugin's mode (UniXNU NativeVulkanMetalPlugin.mm): selftest (default)
      runs the bridge self-test in backboardd and keeps the software renderer; publish returns the native device to backboardd
  /private/var/ngpu/addons/NativePowerVRMetal.clients    0644  with --metal-clients: other processes with their own device
  and with publish, CAEnableDeepFramebuffer, CAEnableFramebufferCompression and CAEnableIOSurfaceCompression false in
  mobile/ and root/ Library/Preferences (backboardd.plist and .GlobalPreferences.plist), as UniXNU add-gpu-plugin-data.py --publish
  writes them: the render server reads them at start, before the plugin runs.
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
METAL = 'NativePowerVRMetal'


def plan(probe, launchd_job, metal=None, mode='selftest', clients=None, work=None):
    files = [(probe / 'powervrprobed', 'bin/powervrprobed', '0755')]
    if (probe / 'lib' / LIB).is_file():
        files.append((probe / 'lib' / LIB, 'addons/NativePowerVRVulkan.bundle/' + LIB, '0755'))
    if launchd_job:
        files.append((probe / (LABEL + '.plist'), 'daemons/%s.plist' % LABEL, '0644'))
    if metal:
        bundle = metal / (METAL + '.bundle')
        if not (bundle / METAL).is_file():
            sys.exit('No %s in %s (run build-a04br3-metal-plugin.py first)' % (METAL, bundle))
        for source in sorted(p for p in bundle.rglob('*') if p.is_file()):
            relative = source.relative_to(bundle).as_posix()
            executable = relative in (METAL, LIB)
            files.append((source, 'addons/%s.bundle/%s' % (METAL, relative), '0755' if executable else '0644'))
        work.mkdir(parents=True, exist_ok=True)
        marker = work / (METAL + '.' + mode)
        marker.write_text('1\n')
        files.append((marker, 'addons/%s.%s' % (METAL, mode), '0644'))
        if clients:
            listed = work / (METAL + '.clients')
            listed.write_text('\n'.join(clients) + '\n')
            files.append((listed, 'addons/%s.clients' % METAL, '0644'))
    missing = [str(source) for source, _, _ in files if not source.is_file()]
    if missing:
        sys.exit('Missing probe output (run build-a04br3-powervr-probe.py first): ' + ', '.join(missing))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tree', type=Path, help='UniXNU build tree (for drivers/DarwinBoot/Test/packed_ramdisk.py)')
    parser.add_argument('--probe-dir', type=Path, required=True)
    parser.add_argument('--launchd-job', action='store_true')
    parser.add_argument('--metal-plugin', type=Path, help='output directory of build-a04br3-metal-plugin.py')
    parser.add_argument('--metal-mode', choices=('selftest', 'publish'), default='selftest')
    parser.add_argument('--metal-clients', help='processes besides backboardd with their own Metal device (comma separated)')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('base', type=Path, nargs='?')
    parser.add_argument('output', type=Path, nargs='?')
    args = parser.parse_args()
    clients = [c for c in (args.metal_clients or '').split(',') if c]
    if any(not all(ch.isalnum() or ch in '._-*' for ch in c) for c in clients):
        sys.exit('--metal-clients takes process names separated by commas')
    if (clients or args.metal_mode != 'selftest') and not args.metal_plugin:
        sys.exit('--metal-mode and --metal-clients go with --metal-plugin')
    marker_dir = Path(tempfile.mkdtemp(prefix='a04br3-metal-markers-'))
    files = plan(args.probe_dir.resolve(), args.launchd_job, args.metal_plugin.resolve() if args.metal_plugin else None,
                 args.metal_mode, clients, marker_dir)
    for source, target, mode in files:
        print('%s %10d /private/var/ngpu/%s' % (mode, source.stat().st_size, target))
    publish = bool(args.metal_plugin) and args.metal_mode == 'publish'
    if publish:
        print('prefs: CAEnableDeepFramebuffer/FramebufferCompression/IOSurfaceCompression false for mobile and root')
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
            if publish:
                write_render_server_prefs(mount, Path(work))
        finally:
            subprocess.run(['hdiutil', 'detach', device], check=True)
        if raw.read_bytes()[32:36] != b'NXSB':
            sys.exit('The edited image is no APFS container any more')
        metadata = packed_ramdisk.pack(raw, output)
    report = dict(base_sha256=packed_ramdisk.inspect(base)[0]['sha256'], output=metadata,
                  staged=['/private/var/ngpu/' + target for _, target, _ in files], metal_mode=args.metal_mode if args.metal_plugin else None,
                  metal_clients=clients)
    output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


def write_render_server_prefs(mount, work):
    """The render server (backboardd) picks a 10-bit deep and an Apple-compressed framebuffer unless told not to, and reads the
    preference at start, before any plugin code runs (UniXNU add-gpu-plugin-data.py --publish). Both its own domain and the global
    one, for the mobile user and root; each file keeps the owner of its Preferences directory."""
    values = {'CAEnableDeepFramebuffer': False, 'CAEnableFramebufferCompression': False, 'CAEnableIOSurfaceCompression': False}
    for home in ('mobile', 'root'):
        directory = mount / home / 'Library/Preferences'
        if not directory.is_dir():
            # iOS: root 0:0, mobile 501:501 (numeric: the build host has no user mobile).
            owner = '0' if home == 'root' else '501'
            subprocess.run(['sudo', 'install', '-d', '-o', owner, '-g', owner, '-m', '0755', str(directory)], check=True)
        info = directory.stat()
        for name in ('backboardd.plist', '.GlobalPreferences.plist'):
            target = directory / name
            current = {}
            if target.exists():
                current = plistlib.loads(subprocess.check_output(['sudo', 'cat', str(target)]))
            current.update(values)
            staged = work / ('%s-%s' % (home, name))
            staged.write_bytes(plistlib.dumps(current, fmt=plistlib.FMT_BINARY))
            subprocess.run(['sudo', 'install', '-o', str(info.st_uid), '-g', str(info.st_gid), '-m', '0644', str(staged), str(target)],
                           check=True)


if __name__ == '__main__':
    main()

