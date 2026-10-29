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
BOOT_MAGIC = b'ANDROID!'
MTK_MAGIC = 0x58881688
BOOT_PAGE = 2048
RAMDISK_ADDR = 0x60000000


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


def boot_ramdisk(boot, destination):
    """Extract and strictly validate the Android-v0 ramdisk before FAT access."""
    data = Path(boot).read_bytes()
    if len(data) < BOOT_PAGE or data[:8] != BOOT_MAGIC:
        raise ValueError('source boot is not an Android boot image')
    kernel_size, _, ramdisk_size, ramdisk_addr, _, _, _, page = struct.unpack_from('<8I', data, 8)
    if page != BOOT_PAGE or ramdisk_addr != RAMDISK_ADDR or kernel_size < 512:
        raise ValueError('source boot header is not the expected Android-v0 layout')
    kernel_end = page + kernel_size
    ramdisk_offset = page + ((kernel_size + page - 1) // page) * page
    if kernel_end > len(data) or ramdisk_offset < kernel_end or ramdisk_size < 512 or ramdisk_offset + ramdisk_size > len(data):
        raise ValueError('source boot ramdisk bounds are invalid')
    ramdisk = data[ramdisk_offset:ramdisk_offset + ramdisk_size]
    magic, size = struct.unpack_from('<II', ramdisk, 0)
    if magic != MTK_MAGIC or ramdisk[8:14] != b'ROOTFS' or size + 512 != ramdisk_size:
        raise ValueError('source boot ramdisk is not a MediaTek ROOTFS payload')
    destination.write_bytes(ramdisk[512:])
    if not destination.is_file() or not destination.stat().st_size:
        raise ValueError('source boot ramdisk FAT payload is empty')
    return {'ramdisk_offset': ramdisk_offset, 'ramdisk_size': ramdisk_size, 'fat_sha256': sha(destination)}


def fat_files(payload, destination):
    destination.mkdir(mode=0o700)
    listing = subprocess.check_output(['mdir', '-b', '-i', str(payload), '::/EFI/UniXNU/Payloads'], text=True,
                                      stderr=subprocess.DEVNULL).splitlines()
    prefix = '::/EFI/UniXNU/Payloads/'
    if any(not entry.startswith(prefix) for entry in listing):
        raise ValueError('payload FAT file listing has an unexpected prefix')
    names = [entry[len(prefix):] for entry in listing]
    if not names or any(not name or '/' in name or '\\' in name for name in names):
        raise ValueError('payload FAT file listing is invalid')
    for name in names:
        target = destination / name
        subprocess.run(['mcopy', '-i', str(payload), f'::/EFI/UniXNU/Payloads/{name}', str(target)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not target.is_file() or target.is_symlink() or not target.stat().st_size:
            raise ValueError('missing payload component: ' + name)
    return {name: sha(destination / name) for name in names}


def compare_payload_files(original, output):
    original_names, output_names = set(original), set(output)
    if original_names != output_names:
        raise ValueError('output FAT file list differs from source payload')
    changed = sorted(name for name in original_names if original[name] != output[name])
    if changed != ['kernel.macho.adt']:
        raise ValueError('output FAT contents differ beyond ADT')
    return changed


def pair_hashes(pair_data):
    files = pair_data.get('inputs_sha256')
    if not isinstance(files, dict):
        raise ValueError('pair has no component hash declarations')
    aliases = {'kernel.macho': ('kernel', 'kernel.macho'), 'scratch-data.img': ('data', 'scratch-data.img'),
               'kernel.macho.trustcache': ('trust', 'kernel.macho.trustcache'),
               'kernel.macho.gpuaddon': ('gpu', 'kernel.macho.gpuaddon')}
    result = {}
    for component, keys in aliases.items():
        digest = next((files.get(key) for key in keys if isinstance(files.get(key), str)), None)
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError('pair component hash declaration is invalid: ' + component)
        result[component] = digest
    for name in ('fd', 'dtb'):
        digest = files.get(name)
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError('pair component hash declaration is invalid: ' + name)
        result[name] = digest
    return result


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
    if pair_data.get('variants', {}).get('baseline', {}).get('boot_sha256') != sha(boot):
        raise ValueError('pair baseline boot hash mismatch')
    declared = pair_hashes(pair_data)
    if declared['fd'] != sha(fd):
        raise ValueError('pair FD hash mismatch')
    original = existing_args(pair_data)
    rewritten = rewrite_args(original)
    dtb = uefi / 'Resources/DTBs/a04br3.dtb'
    if not dtb.is_file():
        raise ValueError('fixed UEFI DTB is missing')
    if declared['dtb'] != sha(dtb):
        raise ValueError('pair DTB hash mismatch')
    stage = Path(tempfile.mkdtemp(prefix='.' + out.name + '.', dir=out.parent))
    identity_file = stage / '.unique-chip-id'
    try:
        extracted = stage / 'extracted'
        source_fat = stage / 'source-payload.img'
        boot_layout = boot_ramdisk(boot, source_fat)
        original_files = fat_files(source_fat, extracted)
        if not set(FIXED).issubset(original_files) or 'kernel.macho.adt' not in original_files:
            raise ValueError('source payload misses a required component')
        for name in FIXED:
            if original_files[name] != declared[name]:
                raise ValueError('pair component hash mismatch: ' + name)
        old_adt = extracted / 'kernel.macho.adt'
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
        private_log = stage / '.private-build.log'
        try:
            with private_log.open('w', encoding='utf-8') as stream:
                result = subprocess.run(command, cwd=source, env=env, stdout=stream, stderr=subprocess.STDOUT)
            log.write_text(private_log.read_text(errors='replace').replace(secret, '[redacted]'), encoding='utf-8')
        finally:
            private_log.unlink(missing_ok=True)
        if result.returncode:
            raise ValueError('official payload generation failed')
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
        output_extracted = stage / 'output-extracted'
        output_files = fat_files(new_payload, output_extracted)
        changed_files = compare_payload_files(original_files, output_files)
        if adt_identity(output_extracted / 'kernel.macho.adt') != identity:
            raise ValueError('output ADT identity differs from source')
        output_boot = stage / 'boot.img'
        boot_command = [str(source / 'unixnu'), 'bootimg', '-D', str(device), '--out', str(stage / 'boot-work'), '--uefi', str(uefi),
                        '--fd', str(fd), '--dtb', str(dtb), '--ramdisk', str(new_payload), '-o', str(output_boot)]
        with log.open('a', encoding='utf-8') as stream:
            subprocess.run(boot_command, cwd=source, check=True, stdout=stream, stderr=subprocess.STDOUT)
            subprocess.run([sys.executable, '-B', str(device / 'scripts/check-a04br3-bootimg.py'), str(output_boot)], check=True,
                           stdout=stream, stderr=subprocess.STDOUT)
        boot_payload = stage / 'boot-payload.img'
        boot_ramdisk(output_boot, boot_payload)
        if sha(boot_payload) != sha(new_payload):
            raise ValueError('output boot ramdisk differs from generated payload')
        check_repo(source, UNIXNU, submodules=False)
        check_repo(device, DEVICE, submodules=False)
        check_repo(uefi, UEFI, submodules=False)
        report = {'schema': 1, 'kind': 'a04br3-iostat-roa-stacklog-payload', 'source_commit': UNIXNU, 'device_commit': DEVICE,
                  'uefi_commit': UEFI, 'hardware_tested': False, 'original_boot_sha256': sha(boot), 'original_pair_sha256': sha(pair),
                  'inputs_sha256': input_hashes, 'fd_sha256': sha(fd), 'dtb_sha256': sha(dtb), 'original_kernel_args': original,
                  'kernel_args': rewritten, 'identity_origin': 'existing unique-chip-id extracted from source boot ADT; value withheld',
                  'identity_sha256': hashlib.sha256(identity).hexdigest(), 'payload_sha256': sha(new_payload), 'boot_sha256': sha(output_boot),
                  'validator_sha256': sha(device / 'scripts/check-a04br3-bootimg.py'), 'build_log_sha256': sha(log),
                  'source_boot_ramdisk': boot_layout, 'source_fat_files_sha256': original_files,
                  'output_fat_files_sha256': output_files, 'output_fat_changed_files': changed_files}
        shutil.copyfile(new_payload, stage / 'payload.img')
        (stage / 'manifest.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        shutil.rmtree(stage / 'work')
        shutil.rmtree(stage / 'boot-work')
        shutil.rmtree(extracted)
        shutil.rmtree(output_extracted)
        source_fat.unlink()
        boot_payload.unlink()
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
