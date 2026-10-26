#!/usr/bin/env bash
# Incremental GPU update over an exact, source-built prepared Data seed. No flash.
set -euo pipefail
DEVICE_SOURCE=$(cd "$(dirname "$0")/.." && pwd)
python3 - "$DEVICE_SOURCE" "$@" <<'PY_GPU_UPDATE'
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    device = Path(sys.argv[1]).resolve()
    parser = argparse.ArgumentParser(description='Build an official GPU addon/payload without changing prepared Data or rebuilding the kernel.')
    for name in ('prepared-data', 'kernel', 'probe-dir', 'metal-plugin', 'trust-base', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--data-volume-uuid', type=uuid.UUID, required=True)
    parser.add_argument('--unique-chip-id', required=True)
    parser.add_argument('--unixnu-dir', type=Path, default=Path.cwd(), help='UniXNU checkout (default: working directory)')
    parser.add_argument('--kernel-arg', action='append', default=[], help='Explicit boot argument; overrides board default (repeatable)')
    parser.add_argument('--omit-kernel-arg', action='append', default=[], help='Omit a board default by name (repeatable)')
    args = parser.parse_args(sys.argv[2:])
    base = args.unixnu_dir.resolve()
    out = args.out.resolve()
    if out.exists():
        parser.error('Refuse to overwrite update output: ' + str(out))
    if not (base / 'unixnu').is_file():
        parser.error('--unixnu-dir must name the UniXNU checkout')
    for name in ('prepared_data', 'kernel', 'trust_base'):
        path = getattr(args, name).resolve()
        if not path.is_file():
            parser.error('Missing input: ' + str(path))
        setattr(args, name, path)
    data_hash, kernel_hash = sha(args.prepared_data), sha(args.kernel)
    board = tomllib.loads((device / 'board.toml').read_text())
    # Use the official argument validation/override rules, including its 1000-byte limit.
    sys.path.insert(0, str(base / 'scripts'))
    from unixnu.payload import kernel_arguments, check_args
    arguments = kernel_arguments(board['payload'].get('kernel_args', []),
                                 args.kernel_arg, args.omit_kernel_arg)
    argument_line = check_args(arguments)
    if 'sheng-gpu-addon=1' not in arguments:
        parser.error('Effective boot arguments must enable sheng-gpu-addon=1')
    work = Path(os.environ.get('UNIXNU_OUT', base / 'out')).expanduser()
    if not work.is_absolute():
        work = base / work
    payload_dir = (work / board['board']['name'] / 'payload').resolve()
    # The official command replaces payload/stage and its payload image/report.
    for path in (args.prepared_data, args.kernel, args.trust_base,
                 args.probe_dir.resolve(), args.metal_plugin.resolve(), out):
        if path == payload_dir or payload_dir in path.parents:
            parser.error('Inputs/update output must be outside official payload output: ' + str(path))
    # Reuse the shipped DBRDISK1 parser and the stager's actual-volume verifier.
    packed = load('gpu_update_packed', base / 'drivers/DarwinBoot/Test/packed_ramdisk.py')
    stager = load('gpu_update_stager', device / 'scripts/a04br3-seed-powervr-probe.py')
    blob = args.prepared_data.read_bytes()
    logical, extents = packed.regions(blob)
    seed_identity = packed.logical_hash(logical, extents)
    with tempfile.TemporaryDirectory(prefix='a04br3-update-uuid-') as temporary:
        raw, mount = Path(temporary) / 'seed.raw', Path(temporary) / 'mount'
        mount.mkdir()
        with raw.open('wb') as stream:
            stream.truncate(logical)
            for offset, data in extents:
                stream.seek(offset)
                stream.write(data)
        attached = plistlib.loads(subprocess.check_output([
            'hdiutil', 'attach', '-readonly', '-imagekey', 'diskimage-class=CRawDiskImage',
            '-nobrowse', '-mountpoint', str(mount), '-plist', str(raw)]))
        mounted_device = attached['system-entities'][0]['dev-entry']
        try:
            stager.verify_volume_uuid(mount, args.data_volume_uuid)
        finally:
            subprocess.run(['hdiutil', 'detach', mounted_device], check=True)
    out.mkdir(parents=True)
    addon, trust = out / 'gpu-update.addon', out / 'trustcache-owner'
    subprocess.run([sys.executable, str(device / 'scripts/make-a04br3-gpu-addon.py'),
                    '--probe-dir', str(args.probe_dir.resolve()), '--metal-plugin', str(args.metal_plugin.resolve()),
                    '--trust-base', str(args.trust_base), '--board', str(device / 'board.toml'),
                    '--trust-out', str(trust), '--out', str(addon)], check=True, cwd=base)
    command = [str(base / 'unixnu'), 'build', 'payload', '-D', str(device),
               '--kernel', str(args.kernel), '--owner-trust-cache', str(trust),
               '--gpu-addon', str(addon), '--data-ramdisk', str(args.prepared_data),
               '--unique-chip-id', args.unique_chip_id]
    for name in args.omit_kernel_arg:
        command += ['--omit-kernel-arg', name]
    for argument in args.kernel_arg:
        command += ['--kernel-arg=' + argument]
    # --skip-setup is deliberately absent: this input is the already prepared seed.
    subprocess.run(command, check=True, cwd=base)
    result = json.loads((payload_dir / 'payload.json').read_text())
    image = payload_dir / 'payload.img'
    if (result.get('skip_setup') is not False or result.get('data_ramdisk_sha256') != data_hash
            or result.get('kernel_sha256') != kernel_hash or result.get('gpu_addon_sha256') != sha(addon)
            or result.get('trust_cache_sha256') != sha(trust) or result.get('kernel_args') != argument_line
            or result.get('payload_sha256') != sha(image) or result.get('payload_bytes') != image.stat().st_size):
        raise SystemExit('Official payload metadata differs from requested update inputs')
    if image.stat().st_size > int(board['payload']['size_mib']) * 1024**2:
        raise SystemExit('Payload exceeds board capacity')
    if sha(args.prepared_data) != data_hash or sha(args.kernel) != kernel_hash:
        raise SystemExit('Prepared Data/kernel changed during update')
    shutil.copyfile(image, out / 'payload.img')
    (out / 'payload.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'update.json').write_text(json.dumps(dict(
        prepared_data_sha256=data_hash, seed_identity=seed_identity, data_volume_uuid=str(args.data_volume_uuid).upper(),
        kernel_sha256=kernel_hash, gpu_addon_sha256=sha(addon), trust_cache_sha256=sha(trust),
        payload_sha256=sha(out / 'payload.img'), kernel_rebuilt=False, hardware_tested=False), indent=2) + '\n')
    print('GPU update ready: ' + str(out / 'payload.img'))
    print('Prepared Data unchanged; no kernel rebuild. boot.img still requires the normal 128 MiB boot-image check.')


if __name__ == '__main__':
    main()
PY_GPU_UPDATE
