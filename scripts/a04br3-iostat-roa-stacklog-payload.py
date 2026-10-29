#!/usr/bin/env python3
"""Repack the fixed iostat ROA boot inputs with stack logging enabled.

This is deliberately a source-only, host-only preparation step.  It extracts
the payload sidecars from the specified stock candidate boot image, verifies
every fixed input, obtains the existing software identity from its ADT without
printing it, and uses the pinned UniXNU CLI for the new payload and boot image.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib

UNIXNU = 'e8f5385e4cee02bb15efa2fb97c20bb59b98d72f'
DEVICE = '8fde8ba87486338342f3be528823cb46394f2c5b'
UEFI = 'd3d4f28a89b29553ede7d7523c6af86d7e2459e8'
HERE = Path(__file__).resolve().parent
FIXED = ('kernel.macho', 'scratch-data.img', 'kernel.macho.trustcache', 'kernel.macho.gpuaddon')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()


def check_repo(path, expected, submodules=False):
    if git(path, 'rev-parse', 'HEAD') != expected:
        raise ValueError(f'{path}: unexpected revision')
    if git(path, 'status', '--porcelain', '--untracked-files=normal', '--ignore-submodules=all'):
        raise ValueError(f'{path}: dirty checkout')
    if any(line and line[0] != 'H' for line in git(path, 'ls-files', '-v').splitlines()):
        raise ValueError(f'{path}: hidden tracked file flag')
    if submodules and any(line.startswith(('-', '+', 'U')) for line in git(path, 'submodule', 'status', '--recursive').splitlines()):
        raise ValueError(f'{path}: submodules are not ready')


def existing_args(pair):
    variants = pair.get('variants')
    if not isinstance(variants, dict) or not isinstance(variants.get('baseline'), dict):
        raise ValueError('pair has no baseline kernel arguments')
    line = variants['baseline'].get('kernel_args')
    if not isinstance(line, str) or not line:
        raise ValueError('baseline kernel arguments are invalid')
    return line


def rewrite_args(line):
    words = line.split()
    keys = [word.partition('=')[0] for word in words]
    if len(keys) != len(set(keys)):
        raise ValueError('original kernel arguments contain duplicate keys')
    required = {'a04br3-home-perf': '1', 'pl': '1'}
    found = {word.partition('=')[0]: word.partition('=')[2] for word in words if '=' in word}
    if any(found.get(key) != value for key, value in required.items()) or 'a04br3-stacklog' in found:
        raise ValueError('original arguments do not match the fixed iostat profile')
    changed = []
    for word in words:
        key = word.partition('=')[0]
        if key == 'a04br3-home-perf':
            continue
        changed.append('pl=60' if key == 'pl' else word)
    changed.append('a04br3-stacklog=1')
    if len(' '.join(changed).encode('ascii')) > 1023:
        raise ValueError('rewritten command line exceeds 1023 bytes')
    return ' '.join(changed)


def adt_identity(adt):
    raw = Path(adt).read_bytes()
    found = []
    def node(offset, depth):
        if depth > 64 or offset + 8 > len(raw):
            raise ValueError('malformed ADT')
        properties, children = struct.unpack_from('<II', raw, offset)
        if properties > 4096 or children > 4096:
            raise ValueError('malformed ADT counts')
        offset += 8
        for _ in range(properties):
            if offset + 36 > len(raw):
                raise ValueError('truncated ADT property')
            name = raw[offset:offset + 32].split(b'\0', 1)[0]
            size = struct.unpack_from('<I', raw, offset + 32)[0] & 0x7fffffff
            offset += 36
            padded = (size + 3) & ~3
            if offset + padded > len(raw):
                raise ValueError('truncated ADT value')
            value = raw[offset:offset + size]
            offset += padded
            if name == b'unique-chip-id':
                if len(value) != 8 or value == bytes(8):
                    raise ValueError('invalid ADT unique-chip-id')
                found.append(value)
        for _ in range(children):
            offset = node(offset, depth + 1)
        return offset
    if node(0, 0) != len(raw) or len(found) != 1:
        raise ValueError('ADT must contain exactly one unique-chip-id')
    return found[0]


def extract_fat(payload, destination):
    destination.mkdir(mode=0o700)
    for name in FIXED:
        target = destination / name
        subprocess.run(['mcopy', '-i', str(payload), f'::/EFI/UniXNU/Payloads/{name}', str(target)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not target.is_file() or target.is_symlink() or not target.stat().st_size:
            raise ValueError('missing payload component: ' + name)
    adt = destination / 'kernel.macho.adt'
    subprocess.run(['mcopy', '-i', str(payload), '::/EFI/UniXNU/Payloads/kernel.macho.adt', str(adt)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return adt


def validate_cli(source, device, uefi, command, line):
    sys.path.insert(0, str(source / 'scripts'))
    try:
        from unixnu import cli, payload
        parsed = cli.parser().parse_args(command[1:])
        board = tomllib.loads((device / 'board.toml').read_text())
        actual = payload.kernel_arguments(board['payload']['kernel_args'], parsed.kernel_arg, parsed.omit_kernel_arg)
        if parsed.command != 'build' or parsed.steps != ['payload'] or ' '.join(actual) != line:
            raise ValueError('official CLI argument merge mismatch')
    finally:
        sys.path.pop(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--device', type=Path, required=True)
    parser.add_argument('--uefi', type=Path, required=True)
    parser.add_argument('--boot', type=Path, required=True)
    parser.add_argument('--pair', type=Path, required=True)
    parser.add_argument('--fd', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    source, device, uefi, boot, pair, fd, out = (value.resolve() for value in
        (args.source, args.device, args.uefi, args.boot, args.pair, args.fd, args.out))
    check_repo(source, UNIXNU, submodules=False)
    check_repo(device, DEVICE, submodules=False)
    check_repo(uefi, UEFI, submodules=False)
    if out.exists():
        raise ValueError('refuse to overwrite output')
    if not boot.is_file() or not pair.is_file() or not fd.is_file():
        raise ValueError('specified boot, pair, or firmware input is missing')
    pair_data = json.loads(pair.read_text())
    if pair_data.get('source_commit') != UNIXNU or pair_data.get('device_commit') != DEVICE or pair_data.get('uefi_commit') != UEFI:
        raise ValueError('pair source pins mismatch')
    original = existing_args(pair_data)
    rewritten = rewrite_args(original)
    dtb = uefi / 'Resources/DTBs/a04br3.dtb'
    if not dtb.is_file():
        raise ValueError('fixed UEFI DTB is missing')
    stage = Path(tempfile.mkdtemp(prefix='.' + out.name + '.', dir=out.parent))
    identity_file = stage / '.unique-chip-id'
    try:
        extracted = stage / 'extracted'
        old_adt = extract_fat(boot, extracted)
        identity = adt_identity(old_adt)
        fd_number = os.open(identity_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd_number, 'wb') as stream:
            stream.write(identity.hex().encode('ascii'))
        inputs = {name: extracted / name for name in FIXED}
        input_hashes = {name: sha(path) for name, path in inputs.items()}
        command = [str(source / 'unixnu'), 'build', 'payload', '-D', str(device), '--out', str(stage / 'work'),
                   '--uefi', str(uefi), '--kernel', str(inputs['kernel.macho']),
                   '--data-ramdisk', str(inputs['scratch-data.img']), '--owner-trust-cache', str(inputs['kernel.macho.trustcache']),
                   '--gpu-addon', str(inputs['kernel.macho.gpuaddon'])]
        original_keys = [word.partition('=')[0] for word in original.split()]
        new_words = rewritten.split()
        new_keys = {word.partition('=')[0] for word in new_words}
        for key in original_keys:
            if key not in new_keys:
                command.append('--omit-kernel-arg=' + key)
        command.extend('--kernel-arg=' + word for word in new_words)
        validate_cli(source, device, uefi, command, rewritten)
        secret = identity_file.read_text(encoding='ascii')
        env = dict(os.environ, UNIXNU_UNIQUE_CHIP_ID=secret, PYTHONDONTWRITEBYTECODE='1',
                   PYTHONPYCACHEPREFIX=str(stage / 'pycache'))
        log = stage / 'build.log'
        with log.open('w', encoding='utf-8') as stream:
            subprocess.run(command, cwd=source, env=env, check=True, stdout=stream, stderr=subprocess.STDOUT)
        secret = None
        identity_file.unlink()
        payload = stage / 'work/a04br3/payload'
        meta = json.loads((payload / 'payload.json').read_text())
        expected_meta = {'kernel_sha256': input_hashes['kernel.macho'], 'data_ramdisk_sha256': input_hashes['scratch-data.img'],
                         'trust_cache_sha256': input_hashes['kernel.macho.trustcache'], 'gpu_addon_sha256': input_hashes['kernel.macho.gpuaddon']}
        if meta.get('kernel_args') != rewritten or any(meta.get(key) != value for key, value in expected_meta.items()):
            raise ValueError('official payload metadata mismatch')
        new_payload = payload / 'payload.img'
        if meta.get('payload_sha256') != sha(new_payload) or meta.get('payload_bytes') != new_payload.stat().st_size:
            raise ValueError('official payload hash mismatch')
        output_boot = stage / 'boot.img'
        boot_command = [str(source / 'unixnu'), 'bootimg', '-D', str(device), '--out', str(stage / 'boot-work'), '--uefi', str(uefi),
                        '--fd', str(fd), '--dtb', str(dtb), '--ramdisk', str(new_payload), '-o', str(output_boot)]
        with log.open('a', encoding='utf-8') as stream:
            subprocess.run(boot_command, cwd=source, check=True, stdout=stream, stderr=subprocess.STDOUT)
            subprocess.run([sys.executable, '-B', str(device / 'scripts/check-a04br3-bootimg.py'), str(output_boot)], check=True,
                           stdout=stream, stderr=subprocess.STDOUT)
        report = {'schema': 1, 'kind': 'a04br3-iostat-roa-stacklog-payload', 'source_commit': UNIXNU, 'device_commit': DEVICE,
                  'uefi_commit': UEFI, 'hardware_tested': False, 'original_boot_sha256': sha(boot), 'original_pair_sha256': sha(pair),
                  'inputs_sha256': input_hashes, 'fd_sha256': sha(fd), 'dtb_sha256': sha(dtb), 'original_kernel_args': original,
                  'kernel_args': rewritten, 'identity_origin': 'existing unique-chip-id extracted from source boot ADT; value withheld',
                  'identity_sha256': hashlib.sha256(identity).hexdigest(), 'payload_sha256': sha(new_payload), 'boot_sha256': sha(output_boot),
                  'validator_sha256': sha(device / 'scripts/check-a04br3-bootimg.py'), 'build_log_sha256': sha(log)}
        shutil.copyfile(new_payload, stage / 'payload.img')
        (stage / 'manifest.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        shutil.rmtree(stage / 'work')
        shutil.rmtree(stage / 'boot-work')
        shutil.rmtree(extracted)
        shutil.rmtree(stage / 'pycache', ignore_errors=True)
        stage.rename(out)
        print('Prepared stacklog payload and validated boot image:', out)
    finally:
        identity_file.unlink(missing_ok=True)
        if stage.exists():
            shutil.rmtree(stage)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
