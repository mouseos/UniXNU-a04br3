#!/usr/bin/env python3
"""Build UniXNU's compressed NGPUADD2 update without changing the prepared Data seed.

Build/sign Mesa, the probe and Metal plugin first. Pass the resulting directories
and the original pinned trust bundle. The normal clean build still creates its
own Data seed; this optional path preserves a running build's persisted Data.
Use the output with `unixnu build payload --gpu-addon FILE --owner-trust-cache
TRUST --data-ramdisk ORIGINAL_PREPARED_SEED` and its original Data volume UUID.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def collect(probe, metal):
    files = [('bin/powervrprobed', 0o755, (probe / 'powervrprobed').read_bytes())]
    driver = probe / 'lib/libvulkan_powervr_mesa.dylib'
    files.append(('addons/NativePowerVRVulkan.bundle/' + driver.name, 0o755, driver.read_bytes()))
    bundle = metal / 'NativePowerVRMetal.bundle'
    if not (bundle / 'NativePowerVRMetal').is_file():
        raise SystemExit('Missing signed NativePowerVRMetal.bundle')
    for path in sorted(bundle.rglob('*')):
        if path.is_symlink():
            raise SystemExit('Addon inputs must not contain symlinks: ' + str(path))
        if path.is_file():
            relative = path.relative_to(bundle).as_posix()
            # Kernel installs these root-owned. Build-host umask must not make
            # the Metal plugin unreadable to the unprivileged render server.
            mode = 0o755 if relative == 'NativePowerVRMetal' else 0o644
            files.append(('addons/' + bundle.name + '/' + relative, mode, path.read_bytes()))
    return files


def verified_hashes(probe, metal):
    """Trust current signed inputs, never an unchecked previous build's metadata."""
    bundle = metal / 'NativePowerVRMetal.bundle'
    drivers = (probe / 'lib/libvulkan_powervr_mesa.dylib', bundle / 'libvulkan_powervr_mesa.dylib')
    if drivers[0].read_bytes() != drivers[1].read_bytes():
        raise SystemExit('Probe and Metal Vulkan copies differ')
    hashes = set()
    for directory, entries in (
            (probe, (('daemon_cdhash', probe / 'powervrprobed'), ('vulkan_cdhash', drivers[0]))),
            (metal, (('cdhash', bundle / 'NativePowerVRMetal'), ('driver_cdhash', drivers[1])))):
        metadata = json.loads((directory / 'trust.json').read_text())
        for key, path in entries:
            expected = metadata.get(key)
            if not isinstance(expected, str) or not re.fullmatch('[0-9a-fA-F]{40}', expected):
                raise SystemExit('Missing or invalid %s in %s/trust.json' % (key, directory))
            subprocess.run(['codesign', '--verify', '--strict', str(path)], check=True)
            detail = subprocess.run(['codesign', '--display', '--verbose=4', str(path)],
                                    capture_output=True, text=True, check=True).stderr
            actual = re.findall(r'^CDHash=([0-9a-fA-F]{40})$', detail, re.M)
            if len(actual) != 1 or actual[0].lower() != expected.lower():
                raise SystemExit('Signed CDHash does not match %s in %s/trust.json' % (key, directory))
            hashes.add(actual[0].lower())
    # Verifying only the executable does not check the bundle resource seal.
    subprocess.run(['codesign', '--verify', '--strict', str(bundle)], check=True)
    return sorted(hashes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe-dir', type=Path, required=True)
    parser.add_argument('--metal-plugin', type=Path, required=True)
    parser.add_argument('--trust-base', type=Path, required=True)
    parser.add_argument('--trust-out', type=Path, required=True)
    parser.add_argument('--board', type=Path, default=ROOT / 'board.toml')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve() == args.trust_out.resolve():
        parser.error('Addon and trust bundle must use different output paths')
    if args.out.exists() or args.trust_out.exists():
        parser.error('Refuse to overwrite an addon or trust bundle')
    spec = importlib.util.spec_from_file_location('powervr_probe_build', ROOT / 'scripts/build-a04br3-powervr-probe.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    hashes = verified_hashes(args.probe_dir, args.metal_plugin)
    files = collect(args.probe_dir, args.metal_plugin)
    blob = helper.pack_addon(files)
    helper.unpack_addon(blob)
    helper.write_trust(args, sorted(hashes))
    args.out.write_bytes(blob)
    print('PASS: GPU addon %s: %d files, %d compressed bytes; Data seed unchanged' % (args.out, len(files), len(blob)))


if __name__ == '__main__':
    main()
