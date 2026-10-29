#!/usr/bin/env python3
"""Build a diagnostic boot using a recorded new kernel and fixed old sidecars.

The old c551 pair and boot are immutable baseline evidence.  This host-only
tool replaces only their kernel with a separately recorded new-kernel build;
Data, trust cache, and GPU addon remain byte-identical.  It intentionally
does not claim source/build bit reproduction or signing proof.  Historical
builders wrote raw command output to a temporary private log before redaction;
that handling is retained only as a documented risk and this builder captures
no private log or identity value.
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

OLD_SOURCE = 'e8f5385e4cee02bb15efa2fb97c20bb59b98d72f'
KERNEL_SOURCE = '22146ccf1721db40f53e35f1afb1d745819f4735'
SEED_PROVENANCE_SHA256 = '486b9a968e624145697966194c83de6bd782f6d6ce3c68257f0ec955f28e3756'
OLD_DEVICE = '8fde8ba87486338342f3be528823cb46394f2c5b'
NEW_DEVICE = '2066e1832a770a7c64cda79f9b03d953bcd04752'
ROUTE_DEVICE = 'c968ba1863b061d39f39ef882b8a0a6daa1ca2d8'
UEFI = 'd3d4f28a89b29553ede7d7523c6af86d7e2459e8'
FIXED = ('scratch-data.img', 'kernel.macho.trustcache', 'kernel.macho.gpuaddon')
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


def check_repo(path, expected):
    head = git(path, 'rev-parse', 'HEAD')
    if not head.startswith(expected):
        raise ValueError(f'{path}: unexpected revision')
    if git(path, 'status', '--porcelain', '--untracked-files=normal', '--ignore-submodules=all'):
        raise ValueError(f'{path}: dirty checkout')


def boot_ramdisk(boot, destination):
    data = Path(boot).read_bytes()
    if len(data) < BOOT_PAGE or data[:8] != BOOT_MAGIC:
        raise ValueError('source boot is not an Android boot image')
    kernel_size, _, ramdisk_size, ramdisk_addr, _, _, _, page = struct.unpack_from('<8I', data, 8)
    if page != BOOT_PAGE or ramdisk_addr != RAMDISK_ADDR or kernel_size < 512:
        raise ValueError('source boot header is not expected Android-v0')
    ramdisk_offset = page + ((kernel_size + page - 1) // page) * page
    if ramdisk_offset + ramdisk_size > len(data) or ramdisk_size < 512:
        raise ValueError('source boot ramdisk bounds are invalid')
    ramdisk = data[ramdisk_offset:ramdisk_offset + ramdisk_size]
    magic, size = struct.unpack_from('<II', ramdisk, 0)
    if magic != MTK_MAGIC or ramdisk[8:14] != b'ROOTFS' or size + 512 != ramdisk_size:
        raise ValueError('source boot ramdisk is not a MediaTek ROOTFS payload')
    destination.write_bytes(ramdisk[512:])


def fat_files(payload, destination):
    destination.mkdir(mode=0o700)
    listing = subprocess.check_output(['mdir', '-b', '-i', str(payload), '::/EFI/UniXNU/Payloads'], text=True,
                                      stderr=subprocess.DEVNULL).splitlines()
    prefix = '::/EFI/UniXNU/Payloads/'
    names = [entry[len(prefix):] for entry in listing if entry.startswith(prefix)]
    if not names or len(names) != len(listing) or any(not name or '/' in name or '\\' in name for name in names):
        raise ValueError('payload FAT file listing is invalid')
    for name in names:
        target = destination / name
        subprocess.run(['mcopy', '-i', str(payload), f'::/EFI/UniXNU/Payloads/{name}', str(target)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {name: sha(destination / name) for name in names}


def payload_config(payload, target):
    subprocess.run(['mcopy', '-i', str(payload), '::/EFI/UniXNU/unixnu.toml', str(target)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return tomllib.loads(target.read_text())


def fat_listing(payload):
    lines = subprocess.check_output(['mdir', '-b', '-s', '-i', str(payload), '::/'], text=True,
                                    stderr=subprocess.DEVNULL).splitlines()
    if not lines or len(lines) != len(set(lines)):
        raise ValueError('invalid complete FAT listing')
    return set(lines)


def redact_private_logs(root, secret):
    for path in root.rglob('*.log'):
        if path.is_symlink() or not path.is_file():
            raise ValueError('unsafe temporary log')
        raw = path.read_bytes()
        path.write_bytes(raw.replace(secret.encode('ascii'), b'[redacted]'))


def run_payload(command, source, env, work, secret, log):
    try:
        result = subprocess.run(command, cwd=source, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors='replace')
    finally:
        if work.exists(): redact_private_logs(work, secret)
    log.write_text(result.stdout.replace(secret, '[redacted]'), encoding='utf-8')
    if result.returncode: raise ValueError('official payload generation failed')


def compare_config(old, new, expected_args):
    old_entries, new_entries = old.get('entry'), new.get('entry')
    if not isinstance(old_entries, list) or not isinstance(new_entries, list):
        raise ValueError('missing boot entries')
    old_xnu = [x for x in old_entries if x.get('id') == 'xnu']
    new_xnu = [x for x in new_entries if x.get('id') == 'xnu']
    if len(old_xnu) != 1 or len(new_xnu) != 1 or new_xnu[0].get('args') != expected_args:
        raise ValueError('actual FAT boot arguments mismatch')
    old_xnu[0]['args'] = expected_args
    if old != new:
        raise ValueError('FAT boot configuration differs beyond requested arguments')


def adt_tree(path):
    raw = Path(path).read_bytes()
    nodes = []
    def node(offset, name, depth):
        if depth > 64 or offset + 8 > len(raw):
            raise ValueError('malformed ADT')
        properties, children = struct.unpack_from('<II', raw, offset); offset += 8
        if properties > 4096 or children > 4096:
            raise ValueError('malformed ADT counts')
        props = []
        for _ in range(properties):
            if offset + 36 > len(raw): raise ValueError('truncated ADT property')
            key = raw[offset:offset + 32].split(b'\0', 1)[0].decode('ascii', 'strict')
            size_field = struct.unpack_from('<I', raw, offset + 32)[0]
            size = size_field & 0x7fffffff; offset += 36
            padded = (size + 3) & ~3
            if offset + padded > len(raw): raise ValueError('truncated ADT value')
            props.append((key, size_field, raw[offset:offset + size])); offset += padded
        nodes.append((name, tuple(props)))
        for index in range(children): offset = node(offset, f'{name}/{index}', depth + 1)
        return offset
    if node(0, 'root', 0) != len(raw): raise ValueError('trailing ADT bytes')
    return tuple(nodes)


def compare_adt(old, new):
    old_tree, new_tree = adt_tree(old), adt_tree(new)
    def normalize(tree):
        result = []
        identities = []
        for path, props in tree:
            row = []
            for key, size_field, value in props:
                if key == 'unique-chip-id':
                    if len(value) != 8 or value == bytes(8): raise ValueError('invalid ADT identity')
                    identities.append(value); row.append((key, size_field, b'<identity>'))
                elif key == 'random-seed': row.append((key, size_field, b'<random-seed>'))
                else: row.append((key, size_field, value))
            result.append((path, tuple(row)))
        if len(identities) != 1: raise ValueError('ADT must contain exactly one identity')
        return tuple(result), identities[0]
    left, left_id = normalize(old_tree); right, right_id = normalize(new_tree)
    if left != right or left_id != right_id: raise ValueError('ADT differs beyond identity, structure, or random-seed')
    return hashlib.sha256(left_id).hexdigest()


def pair_hashes(pair):
    values = pair.get('inputs_sha256')
    aliases = {'kernel.macho': ('kernel', 'kernel.macho'), 'scratch-data.img': ('data', 'scratch-data.img'),
               'kernel.macho.trustcache': ('trust', 'kernel.macho.trustcache'), 'kernel.macho.gpuaddon': ('gpu', 'kernel.macho.gpuaddon')}
    result = {}
    for name, keys in aliases.items():
        value = next((values.get(key) for key in keys if isinstance(values, dict) and isinstance(values.get(key), str)), None)
        if not isinstance(value, str) or len(value) != 64: raise ValueError('invalid pair component hash: ' + name)
        result[name] = value
    for name in ('fd', 'dtb'):
        value = values.get(name) if isinstance(values, dict) else None
        if not isinstance(value, str) or len(value) != 64: raise ValueError('invalid pair component hash: ' + name)
        result[name] = value
    return result


def load_kernel_record(path, kernel, expected_device=NEW_DEVICE):
    record = json.loads(Path(path).read_text(encoding='utf-8'))
    required = ('sha256', 'device_commit', 'source_commit', 'run_id')
    if any(not isinstance(record.get(key), str) or not record[key] for key in required):
        raise ValueError('kernel build record lacks sha256, device_commit, source_commit, or run_id')
    digest = record['sha256']
    if digest != sha(kernel): raise ValueError('kernel build record hash mismatch')
    if record['source_commit'] != KERNEL_SOURCE or record['device_commit'] != expected_device:
        raise ValueError('kernel build record source/device mismatch')
    return record


def check_owner_seed(record, provenance, old_kernel, new_kernel, scratch_sha):
    import uuid
    stage = provenance.get('staged_seed', {})
    expected = stage.get('expected_volume_uuid')
    try:
        expected = str(uuid.UUID(expected)).upper()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('missing or invalid verified seed UUID') from None
    if stage.get('data_sha256') != scratch_sha:
        raise ValueError('seed provenance does not match preserved scratch data')
    if record.get('owner_data_volume_uuid', '').upper() != expected:
        raise ValueError('kernel build record and seed UUID mismatch')
    needle = expected.encode('ascii') + b'\0'
    for name, path in (('old', old_kernel), ('new', new_kernel)):
        if needle not in Path(path).read_bytes().upper():
            raise ValueError(name + ' kernel does not contain expected Data UUID')
    return expected


def rewrite_args(line, stream=False, meter=False, meter_route=False):
    if meter and meter_route: raise ValueError('select exactly one meter mode')
    meter_enabled = meter or meter_route
    words = line.split(); keys = [word.partition('=')[0] for word in words]
    if len(keys) != len(set(keys)): raise ValueError('duplicate kernel argument keys')
    values = {word.partition('=')[0]: word.partition('=')[2] for word in words if '=' in word}
    if values.get('pl') != '1' or values.get('a04br3-home-perf') != '1' or values.get('native-msdc-iostat') != '1':
        raise ValueError('baseline arguments are not the expected iostat stack profile')
    if stream and meter_enabled: raise ValueError('meter and stream trials must be separate')
    if meter_enabled and any(values.get(k, '0') != '0' for k in ('native-cs-stream', 'arc', 'armcpu-runtime-cycle')):
        raise ValueError('meter trial conflicts with existing stream or PMU arguments')
    if meter_enabled and 'cm' in keys: raise ValueError('unexpected existing meter argument')
    changed = [('pl=60' if word.startswith('pl=') else word) for word in words if meter_enabled or not word.startswith('a04br3-home-perf=')]
    if 'a04br3-stacklog' in keys:
        raise ValueError('unexpected existing stacklog argument')
    changed.append('cm=2' if meter_route else 'cm=1' if meter else 'a04br3-stacklog=1')
    if stream:
        if 'native-cs-stream' in keys: raise ValueError('unexpected existing stream argument')
        changed.append('native-cs-stream=1')
    line = ' '.join(changed)
    if len(line.encode('ascii')) > 1023: raise ValueError('rewritten command line exceeds 1023 bytes')
    return line


def validate_cli(source, device, command, line):
    sys.path.insert(0, str(source / 'scripts'))
    try:
        from unixnu import cli, payload
        parsed = cli.parser().parse_args(command[1:]); board = tomllib.loads((device / 'board.toml').read_text())
        actual = payload.kernel_arguments(board['payload']['kernel_args'], parsed.kernel_arg, parsed.omit_kernel_arg)
        if parsed.command != 'build' or parsed.steps != ['payload'] or ' '.join(actual) != line:
            raise ValueError('official CLI argument merge mismatch')
    finally: sys.path.pop(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'old-device', 'new-device', 'uefi', 'old-boot', 'old-pair', 'kernel', 'kernel-record', 'fd', 'out', 'seed-provenance'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--stream', action='store_true', help='enable the opt-in native4k streaming trial')
    parser.add_argument('--meter', action='store_true', help='enable the separate opt-in clock meter trial')
    parser.add_argument('--meter-route', action='store_true', help='enable the experimental vendor clock route trial')
    args = parser.parse_args(); source, old_device, new_device, uefi, old_boot, old_pair, kernel, record_path, fd, out, seed_provenance = (getattr(args, name.replace('-', '_')).resolve() for name in ('source','old_device','new_device','uefi','old_boot','old_pair','kernel','kernel_record','fd','out','seed_provenance'))
    expected_device = ROUTE_DEVICE if args.meter_route else NEW_DEVICE
    check_repo(source, OLD_SOURCE); check_repo(old_device, OLD_DEVICE); check_repo(new_device, expected_device); check_repo(uefi, UEFI)
    if out.exists(): raise ValueError('refuse to overwrite output')
    if not all(path.is_file() for path in (old_boot, old_pair, kernel, record_path, fd)): raise ValueError('a required input is missing')
    record = load_kernel_record(record_path, kernel, expected_device); pair = json.loads(old_pair.read_text()); declared = pair_hashes(pair)
    if (pair.get('source_commit'), pair.get('device_commit'), pair.get('uefi_commit')) != (OLD_SOURCE, OLD_DEVICE, UEFI): raise ValueError('old pair pins mismatch')
    if pair.get('variants', {}).get('baseline', {}).get('boot_sha256') != sha(old_boot): raise ValueError('old pair boot hash mismatch')
    dtb = uefi / 'Resources/DTBs/a04br3.dtb'
    if sha(fd) != declared['fd'] or not dtb.is_file() or sha(dtb) != declared['dtb']: raise ValueError('FD/DTB pin mismatch with old pair')
    original_args = pair.get('variants', {}).get('baseline', {}).get('kernel_args')
    if not isinstance(original_args, str): raise ValueError('old pair lacks baseline kernel args')
    rewritten = rewrite_args(original_args, stream=args.stream, meter=args.meter, meter_route=args.meter_route)
    stage = Path(tempfile.mkdtemp(prefix='.' + out.name + '.', dir=out.parent))
    try:
        source_payload, old_dir, new_dir = stage/'old.img', stage/'old', stage/'new'
        boot_ramdisk(old_boot, source_payload); old_files = fat_files(source_payload, old_dir)
        if not set(FIXED).issubset(old_files) or 'kernel.macho.adt' not in old_files: raise ValueError('old payload misses required files')
        for name in FIXED:
            if old_files[name] != declared[name]: raise ValueError('old pair hash mismatch: ' + name)
        if sha(seed_provenance) != SEED_PROVENANCE_SHA256: raise ValueError('seed provenance pin mismatch')
        data_uuid = check_owner_seed(record, json.loads(seed_provenance.read_text()), old_dir/'kernel.macho', kernel, old_files['scratch-data.img'])
        if old_files.get('kernel.macho') != declared['kernel.macho']:
            raise ValueError('old pair kernel hash mismatch')
        identities = [value for _, props in adt_tree(old_dir/'kernel.macho.adt') for key, _, value in props if key == 'unique-chip-id']
        if len(identities) != 1 or len(identities[0]) != 8 or identities[0] == bytes(8):
            raise ValueError('invalid old ADT identity')
        secret = format(int.from_bytes(identities[0], 'little'), 'x')
        board = tomllib.loads((new_device/'board.toml').read_text()); original_keys = [word.partition('=')[0] for word in board['payload']['kernel_args']]
        command = [str(source/'unixnu'), 'build', 'payload', '-D', str(new_device), '--out', str(stage/'work'), '--uefi', str(uefi), '--kernel', str(kernel), '--data-ramdisk', str(old_dir/'scratch-data.img'), '--owner-trust-cache', str(old_dir/'kernel.macho.trustcache'), '--gpu-addon', str(old_dir/'kernel.macho.gpuaddon')]
        new_keys = {word.partition('=')[0] for word in rewritten.split()}
        command.extend('--omit-kernel-arg=' + key for key in original_keys if key not in new_keys)
        command.extend('--kernel-arg=' + word for word in rewritten.split()); validate_cli(source, new_device, command, rewritten)
        log = stage/'build.log'
        env = dict(os.environ, UNIXNU_UNIQUE_CHIP_ID=secret, PYTHONDONTWRITEBYTECODE='1')
        run_payload(command, source, env, stage/'work', secret, log)
        payload = stage/'work/a04br3/payload'; image = payload/'payload.img'; meta = json.loads((payload/'payload.json').read_text())
        expected = {'kernel_sha256': sha(kernel), 'data_ramdisk_sha256': old_files['scratch-data.img'], 'trust_cache_sha256': old_files['kernel.macho.trustcache'], 'gpu_addon_sha256': old_files['kernel.macho.gpuaddon']}
        if meta.get('kernel_args') != rewritten or any(meta.get(key) != value for key, value in expected.items()): raise ValueError('payload metadata mismatch')
        if meta.get('payload_sha256') != sha(image): raise ValueError('payload hash mismatch')
        new_files = fat_files(image, new_dir)
        if fat_listing(source_payload) != fat_listing(image): raise ValueError('complete FAT listing mismatch')
        if new_files.get('kernel.macho') != sha(kernel): raise ValueError('new FAT kernel hash mismatch')
        compare_config(payload_config(source_payload, stage/'old.toml'), payload_config(image, stage/'new.toml'), rewritten)
        if set(old_files) != set(new_files) or any(old_files[name] != new_files[name] for name in FIXED): raise ValueError('fixed payload files changed')
        changed = sorted(name for name in old_files if old_files[name] != new_files[name])
        if changed != ['kernel.macho', 'kernel.macho.adt']: raise ValueError('payload differs beyond replacement kernel and ADT')
        identity_hash = compare_adt(old_dir/'kernel.macho.adt', new_dir/'kernel.macho.adt')
        output_boot = stage/'boot.img'; boot_command = [str(source/'unixnu'), 'bootimg', '-D', str(new_device), '--out', str(stage/'boot-work'), '--uefi', str(uefi), '--fd', str(fd), '--dtb', str(dtb), '--ramdisk', str(image), '-o', str(output_boot)]
        with log.open('a', encoding='utf-8') as stream:
            subprocess.run(boot_command, cwd=source, check=True, stdout=stream, stderr=subprocess.STDOUT)
            subprocess.run([sys.executable, '-B', str(new_device/'scripts/check-a04br3-bootimg.py'), str(output_boot)], check=True, stdout=stream, stderr=subprocess.STDOUT)
        boot_payload = stage/'boot-payload.img'; boot_ramdisk(output_boot, boot_payload)
        if sha(boot_payload) != sha(image): raise ValueError('output boot FAT differs from new payload')
        check_repo(source, OLD_SOURCE); check_repo(old_device, OLD_DEVICE); check_repo(new_device, expected_device); check_repo(uefi, UEFI)
        report = {'schema': 1, 'kind': 'a04br3-iostat-stack-new-kernel', 'source_commit': OLD_SOURCE, 'old_device_commit': OLD_DEVICE, 'new_device_commit': git(new_device, 'rev-parse', 'HEAD'), 'uefi_commit': UEFI, 'native_cs_stream': bool(args.stream), 'clock_meter': bool(args.meter or args.meter_route), 'clock_meter_route': bool(args.meter_route), 'owner_data_volume_uuid': data_uuid, 'seed_provenance_sha256': sha(seed_provenance), 'kernel_build_record': {'sha256': record['sha256'], 'device_commit': record['device_commit'], 'source_commit': record['source_commit'], 'run_id': record['run_id']}, 'old_boot_sha256': sha(old_boot), 'old_pair_sha256': sha(old_pair), 'fd_sha256': sha(fd), 'dtb_sha256': sha(dtb), 'kernel_args': rewritten, 'fixed_payload_sha256': {name: old_files[name] for name in FIXED}, 'output_fat_files_sha256': new_files, 'output_fat_changed_files': changed, 'adt_identity_sha256': identity_hash, 'adt_comparison': 'identity, structure, and random-seed only; values withheld', 'payload_sha256': sha(image), 'boot_sha256': sha(output_boot), 'build_log_sha256': sha(log), 'hardware_tested': False, 'source_build_bit_reproducible': 'unproven', 'signature_verified': 'unproven', 'historical_private_log_risk': 'older builder used a temporary private log before redaction; this builder does not capture identity-bearing private output'}
        shutil.copyfile(image, stage/'payload.img'); (stage/'manifest.json').write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
        for path in (stage/'work', stage/'boot-work', old_dir, new_dir): shutil.rmtree(path)
        source_payload.unlink(); boot_payload.unlink(); (stage/'old.toml').unlink(); (stage/'new.toml').unlink(); stage.rename(out)
        print('Prepared diagnostic boot:', out)
    finally:
        if stage.exists(): shutil.rmtree(stage)


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error: sys.exit(str(error))
