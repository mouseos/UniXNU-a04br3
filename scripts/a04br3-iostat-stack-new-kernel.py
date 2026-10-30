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
ALLTASK_DEVICE = 'f2dee958a180ae6fee7b84a44baf9d74dcd22aff'
LIVE_PC_KERNEL_SHA256 = '17904eca68b4c7e5f99350f4ddc57a671efc0357a93c984db9bb7f799336a4d1'
ALLTASK_BASELINE_BOOT_SHA256 = '00e2adb5cbd58f0d01384a807336a0eb99f92c84c3dd514e3c4bb7ab65bab881'
ALLTASK_BASELINE_MANIFEST_SHA256 = '89a02631bcf6750871f557f70dc44ec71e6d6cd2a8754e4f2f87bad20f995fad'
ALLTASK_BASELINE_PAIR_SHA256 = '9a1cafd31c06c546622f0e2ee42777edba416f7054d2c46e0b6e75d926c94bb1'
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


def validate_source_config(config, pair_args, require_control=False):
    if not isinstance(pair_args, str): raise ValueError('missing source boot arguments')
    entries = config.get('entry')
    if not isinstance(entries, list): raise ValueError('source FAT has no boot entries')
    entries = [entry for entry in entries if isinstance(entry, dict) and entry.get('id') == 'xnu']
    if len(entries) != 1 or entries[0].get('args') != pair_args:
        raise ValueError('pair arguments differ from source FAT boot configuration')
    if not require_control: return
    words = pair_args.split()
    required = ('a04br3-gpu=2', 'sheng-gpu-addon=1', 'a04br3-gpu-metal=1',
                'a04br3-gpu-bo-wc=1', 'a04br3-gpu-bo-map=1', 'a04br3-gpu-tq-low=1',
                'sheng-fb-gpu=1', 'a04br3-gpu-clock=380', 'a04br3-autoreset=720')
    keys = [word.partition('=')[0] for word in words]
    if len(keys) != len(set(keys)) or any(words.count(word) != 1 for word in required):
        raise ValueError('source profile lacks required GPU or recovery arguments')


def pinned_json(raw, expected, label):
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(label + ' hash mismatch')
    return json.loads(raw)


def validate_control_pair(baseline, control):
    fields = ('source_commit', 'old_device_commit', 'new_device_commit', 'uefi_commit',
              'old_boot_sha256', 'old_pair_sha256', 'seed_provenance_sha256',
              'fd_sha256', 'dtb_sha256', 'owner_data_volume_uuid', 'adt_identity_sha256',
              'kernel_build_record', 'fixed_payload_sha256')
    for key in fields:
        if key not in baseline or key not in control or baseline[key] != control[key]:
            raise ValueError('comparison fixed input mismatch: ' + key)
    if baseline.get('boot_sha256') != ALLTASK_BASELINE_BOOT_SHA256:
        raise ValueError('comparison baseline is not the pinned all-task boot')
    if baseline.get('all_task_sampling') is not True or baseline.get('live_pc_pid1', False) is not False:
        raise ValueError('comparison baseline has wrong diagnostic profile')
    if baseline.get('native_cs_stream') is not False or baseline.get('clock_meter') is not False or baseline.get('clock_meter_route') is not False:
        raise ValueError('comparison baseline has incompatible diagnostic mode')
    if baseline.get('kernel_args') != control.get('kernel_args', '') + ' atp=1':
        raise ValueError('comparison arguments differ beyond atp=1')
    for name, value in control['fixed_payload_sha256'].items():
        if baseline.get('output_fat_files_sha256', {}).get(name) != value:
            raise ValueError('comparison baseline FAT hash mismatch: ' + name)
    if baseline.get('output_fat_files_sha256', {}).get('kernel.macho') != control['kernel_build_record']['sha256']:
        raise ValueError('comparison baseline FAT kernel mismatch')


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


def check_live_pc_kernel(record):
    if record.get('sha256') != LIVE_PC_KERNEL_SHA256:
        raise ValueError('live-pc requires the recorded f2dee95 kernel')


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


def rewrite_args(line, stream=False, meter=False, meter_route=False, all_task=False, live_pc=False, all_task_control=False):
    if live_pc and not all_task: raise ValueError('--live-pc requires --all-task')
    if sum((bool(meter), bool(meter_route), bool(all_task), bool(all_task_control))) > 1: raise ValueError('select exactly one diagnostic mode')
    meter_enabled = meter or meter_route
    diagnostic_enabled = meter_enabled or all_task or all_task_control
    words = line.split(); keys = [word.partition('=')[0] for word in words]
    if len(keys) != len(set(keys)): raise ValueError('duplicate kernel argument keys')
    values = {word.partition('=')[0]: word.partition('=')[2] for word in words if '=' in word}
    if values.get('pl') != '1' or values.get('a04br3-home-perf') != '1' or values.get('native-msdc-iostat') != '1':
        raise ValueError('baseline arguments are not the expected iostat stack profile')
    if stream and diagnostic_enabled: raise ValueError('meter and stream trials must be separate')
    if diagnostic_enabled and any(values.get(k, '0') != '0' for k in ('native-cs-stream', 'arc', 'armcpu-runtime-cycle')):
        raise ValueError('meter trial conflicts with existing stream or PMU arguments')
    if diagnostic_enabled and 'cm' in keys: raise ValueError('unexpected existing meter argument')
    if 'atp' in keys: raise ValueError('unexpected existing all-task argument')
    if 'a04br3-live-pc' in keys: raise ValueError('unexpected existing live-pc argument')
    if diagnostic_enabled and 'a04br3-proclog' in keys: raise ValueError('unexpected overriding long-form process log interval')
    changed = [('pl=60' if word.startswith('pl=') else word) for word in words if diagnostic_enabled or not word.startswith('a04br3-home-perf=')]
    if 'a04br3-stacklog' in keys:
        raise ValueError('unexpected existing stacklog argument')
    if not all_task_control:
        changed.append('atp=1' if all_task else 'cm=2' if meter_route else 'cm=1' if meter else 'a04br3-stacklog=1')
    if live_pc: changed.append('a04br3-live-pc=1')
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


class Once(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, default=False, **kwargs)
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest): parser.error(option_string + ' specified more than once')
        setattr(namespace, self.dest, True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'old-device', 'new-device', 'uefi', 'old-boot', 'old-pair', 'kernel', 'kernel-record', 'fd', 'out', 'seed-provenance'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--stream', action=Once, help='enable the opt-in native4k streaming trial')
    parser.add_argument('--meter', action=Once, help='enable the separate opt-in clock meter trial')
    parser.add_argument('--meter-route', action=Once, help='enable the experimental vendor clock route trial')
    parser.add_argument('--all-task', action=Once, help='enable opt-in all-process resource sampling')
    parser.add_argument('--all-task-control', action=Once, help='use the all-task profile without atp=1')
    parser.add_argument('--comparison-manifest', type=Path, help='manifest of the pinned all-task comparison boot')
    parser.add_argument('--comparison-boot', type=Path, help='pinned all-task comparison boot image')
    parser.add_argument('--live-pc', action=Once, help='enable PID 1 live PC sampling with --all-task')
    args = parser.parse_args()
    if args.live_pc and not args.all_task: parser.error('--live-pc requires --all-task')
    if args.all_task_control != bool(args.comparison_manifest and args.comparison_boot):
        parser.error('--all-task-control requires both --comparison-manifest and --comparison-boot; other modes reject them')
    if not args.all_task_control and (args.comparison_manifest or args.comparison_boot):
        parser.error('comparison inputs are only valid with --all-task-control')
    if args.stream and (args.all_task or args.all_task_control or args.meter or args.meter_route) or sum((args.all_task, args.all_task_control, args.meter, args.meter_route)) > 1:
        parser.error('conflicting diagnostic modes')
    source, old_device, new_device, uefi, old_boot, old_pair, kernel, record_path, fd, out, seed_provenance = (getattr(args, name.replace('-', '_')).resolve() for name in ('source','old_device','new_device','uefi','old_boot','old_pair','kernel','kernel_record','fd','out','seed_provenance'))
    expected_device = ALLTASK_DEVICE if args.all_task or args.all_task_control else ROUTE_DEVICE if args.meter_route else NEW_DEVICE
    check_repo(source, OLD_SOURCE); check_repo(old_device, OLD_DEVICE); check_repo(new_device, expected_device); check_repo(uefi, UEFI)
    if out.exists(): raise ValueError('refuse to overwrite output')
    if not all(path.is_file() for path in (old_boot, old_pair, kernel, record_path, fd)): raise ValueError('a required input is missing')
    record = load_kernel_record(record_path, kernel, expected_device)
    if args.live_pc or args.all_task_control: check_live_pc_kernel(record)
    pair_bytes = old_pair.read_bytes()
    pair = (pinned_json(pair_bytes, ALLTASK_BASELINE_PAIR_SHA256, 'comparison old pair')
            if args.all_task_control else json.loads(pair_bytes))
    declared = pair_hashes(pair)
    if (pair.get('source_commit'), pair.get('device_commit'), pair.get('uefi_commit')) != (OLD_SOURCE, OLD_DEVICE, UEFI): raise ValueError('old pair pins mismatch')
    if pair.get('variants', {}).get('baseline', {}).get('boot_sha256') != sha(old_boot): raise ValueError('old pair boot hash mismatch')
    dtb = uefi / 'Resources/DTBs/a04br3.dtb'
    if sha(fd) != declared['fd'] or not dtb.is_file() or sha(dtb) != declared['dtb']: raise ValueError('FD/DTB pin mismatch with old pair')
    original_args = pair.get('variants', {}).get('baseline', {}).get('kernel_args')
    if not isinstance(original_args, str): raise ValueError('old pair lacks baseline kernel args')
    rewritten = rewrite_args(original_args, stream=args.stream, meter=args.meter, meter_route=args.meter_route, all_task=args.all_task, live_pc=args.live_pc, all_task_control=args.all_task_control)
    stage = Path(tempfile.mkdtemp(prefix='.' + out.name + '.', dir=out.parent))
    try:
        source_payload, old_dir, new_dir = stage/'old.img', stage/'old', stage/'new'
        boot_ramdisk(old_boot, source_payload); old_files = fat_files(source_payload, old_dir)
        old_config = payload_config(source_payload, stage/'old.toml')
        validate_source_config(old_config, original_args, require_control=args.all_task_control)
        if not set(FIXED).issubset(old_files) or 'kernel.macho.adt' not in old_files: raise ValueError('old payload misses required files')
        for name in FIXED:
            if old_files[name] != declared[name]: raise ValueError('old pair hash mismatch: ' + name)
        if sha(seed_provenance) != SEED_PROVENANCE_SHA256: raise ValueError('seed provenance pin mismatch')
        data_uuid = check_owner_seed(record, json.loads(seed_provenance.read_text()), old_dir/'kernel.macho', kernel, old_files['scratch-data.img'])
        if old_files.get('kernel.macho') != declared['kernel.macho']:
            raise ValueError('old pair kernel hash mismatch')
        baseline = None
        if args.all_task_control:
            baseline_bytes = args.comparison_manifest.read_bytes()
            baseline = pinned_json(baseline_bytes, ALLTASK_BASELINE_MANIFEST_SHA256, 'comparison baseline manifest')
            if sha(args.comparison_boot) != ALLTASK_BASELINE_BOOT_SHA256:
                raise ValueError('comparison boot hash mismatch')
            comparison_payload = stage/'comparison.img'
            boot_ramdisk(args.comparison_boot, comparison_payload)
            comparison_config = payload_config(comparison_payload, stage/'comparison.toml')
            validate_source_config(comparison_config, baseline.get('kernel_args'), require_control=True)
            comparison_files = fat_files(comparison_payload, stage/'comparison')
            if comparison_files != baseline.get('output_fat_files_sha256'):
                raise ValueError('comparison manifest does not match baseline FAT payload')
            candidate = {'source_commit': OLD_SOURCE, 'old_device_commit': OLD_DEVICE,
                         'new_device_commit': expected_device, 'uefi_commit': UEFI,
                         'old_boot_sha256': sha(old_boot), 'old_pair_sha256': sha(old_pair),
                         'seed_provenance_sha256': sha(seed_provenance), 'fd_sha256': sha(fd),
                         'dtb_sha256': sha(dtb), 'owner_data_volume_uuid': data_uuid,
                         'adt_identity_sha256': compare_adt(old_dir/'kernel.macho.adt', old_dir/'kernel.macho.adt'),
                         'kernel_build_record': {k: record[k] for k in ('sha256','device_commit','source_commit','run_id')},
                         'fixed_payload_sha256': {name: old_files[name] for name in FIXED}, 'kernel_args': rewritten}
            validate_control_pair(baseline, candidate)
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
        compare_config(old_config, payload_config(image, stage/'new.toml'), rewritten)
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
        report = {'schema': 1, 'kind': 'a04br3-iostat-stack-new-kernel', 'source_commit': OLD_SOURCE, 'old_device_commit': OLD_DEVICE, 'new_device_commit': git(new_device, 'rev-parse', 'HEAD'), 'uefi_commit': UEFI, 'native_cs_stream': bool(args.stream), 'clock_meter': bool(args.meter or args.meter_route), 'clock_meter_route': bool(args.meter_route), 'all_task_sampling': bool(args.all_task), 'all_task_control': bool(args.all_task_control), 'live_pc_pid1': bool(args.live_pc), 'owner_data_volume_uuid': data_uuid, 'seed_provenance_sha256': sha(seed_provenance), 'kernel_build_record': {'sha256': record['sha256'], 'device_commit': record['device_commit'], 'source_commit': record['source_commit'], 'run_id': record['run_id']}, 'old_boot_sha256': sha(old_boot), 'old_pair_sha256': sha(old_pair), 'fd_sha256': sha(fd), 'dtb_sha256': sha(dtb), 'kernel_args': rewritten, 'fixed_payload_sha256': {name: old_files[name] for name in FIXED}, 'output_fat_files_sha256': new_files, 'output_fat_changed_files': changed, 'adt_identity_sha256': identity_hash, 'adt_comparison': 'identity, structure, and random-seed only; values withheld', 'payload_sha256': sha(image), 'boot_sha256': sha(output_boot), 'build_log_sha256': sha(log), 'hardware_tested': False, 'source_build_bit_reproducible': 'unproven', 'signature_verified': 'unproven', 'historical_private_log_risk': 'older builder used a temporary private log before redaction; this builder does not capture identity-bearing private output'}
        if baseline is not None:
            if (sha(args.comparison_manifest) != ALLTASK_BASELINE_MANIFEST_SHA256 or
                sha(args.comparison_boot) != ALLTASK_BASELINE_BOOT_SHA256 or
                sha(old_pair) != ALLTASK_BASELINE_PAIR_SHA256):
                raise ValueError('comparison inputs changed during build')
            validate_control_pair(baseline, report)
            report['comparison_baseline_boot_sha256'] = ALLTASK_BASELINE_BOOT_SHA256
            report['comparison_baseline_manifest_sha256'] = ALLTASK_BASELINE_MANIFEST_SHA256
        shutil.copyfile(image, stage/'payload.img'); (stage/'manifest.json').write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
        for path in (stage/'work', stage/'boot-work', old_dir, new_dir): shutil.rmtree(path)
        if baseline is not None:
            shutil.rmtree(stage/'comparison'); (stage/'comparison.img').unlink(); (stage/'comparison.toml').unlink()
        source_payload.unlink(); boot_payload.unlink(); (stage/'old.toml').unlink(); (stage/'new.toml').unlink(); stage.rename(out)
        print('Prepared diagnostic boot:', out)
    finally:
        if stage.exists(): shutil.rmtree(stage)


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error: sys.exit(str(error))
