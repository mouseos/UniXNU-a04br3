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
import stat

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
            mode = stat.S_IMODE(path.stat().st_mode)
            files.append(('addons/' + bundle.name + '/' + path.relative_to(bundle).as_posix(), mode, path.read_bytes()))
    return files


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
    files = collect(args.probe_dir, args.metal_plugin)
    blob = helper.pack_addon(files)
    helper.unpack_addon(blob)
    hashes = set()
    for directory, keys in ((args.probe_dir, ('daemon_cdhash', 'vulkan_cdhash')),
                            (args.metal_plugin, ('cdhash', 'driver_cdhash'))):
        metadata = json.loads((directory / 'trust.json').read_text())
        for key in keys:
            value = metadata.get(key)
            if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{40}', value):
                parser.error('Missing or invalid %s in %s/trust.json' % (key, directory))
            hashes.add(value.lower())
    helper.write_trust(args, sorted(hashes))
    args.out.write_bytes(blob)
    print('PASS: GPU addon %s: %d files, %d compressed bytes; Data seed unchanged' % (args.out, len(files), len(blob)))


if __name__ == '__main__':
    main()
