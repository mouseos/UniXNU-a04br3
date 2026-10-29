#!/usr/bin/env python3
"""Build the separately staged iostat ROA candidate; never access a device.

This is deliberately a candidate-specific copy of the read-batch builder.  The
normal builder's pre-build record remains unchanged: this module validates the
retained official records and then independently validates the new Data/trust/GPU
staging contract before any payload is built.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import plistlib
from pathlib import Path
import secrets
import shutil
import subprocess
import struct
import sys
import tempfile
import time
import tomllib
import uuid

UNIXNU = 'e8f5385e4cee02bb15efa2fb97c20bb59b98d72f'
DEVICE = '8fde8ba87486338342f3be528823cb46394f2c5b'
UEFI = 'd3d4f28a89b29553ede7d7523c6af86d7e2459e8'
ARMCPU = '85d06a5248a4516c777763d195866a2362e5d7b0'
MSDC = '66d40654ae43ef27090a6d6e1f552be6ea10d24b'
FIRMWARE_UNIXNU = '7fb02c678704a11a9c7455f4896afde1fef7401e'
DARWINBOOT = 'b9063ad22903288df989c3449621b7eb97de7dd2'
HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()


def check_preparation_source():
    root = HERE.parent
    if git(root, 'status', '--porcelain', '--untracked-files=normal', '--ignore-submodules=all'):
        raise ValueError('preparation source must be committed and clean')
    if any(line and line[0]!='H' for line in git(root, 'ls-files', '-v').splitlines()):
        raise ValueError('preparation source contains hidden tracked-file flags')
    subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor', DEVICE, 'HEAD'], check=True)
    return git(root, 'rev-parse', 'HEAD')


def check_repo(path, expected):
    if git(path, 'rev-parse', 'HEAD') != expected:
        raise ValueError(f'{path}: unexpected source revision')
    if git(path, 'status', '--porcelain', '--untracked-files=normal', '--ignore-submodules=none'):
        raise ValueError(f'{path}: checkout or submodules are dirty')
    flags = git(path, 'ls-files', '-v')
    if any(line and line[0] != 'H' for line in flags.splitlines()):
        raise ValueError(f'{path}: hidden tracked-file flags are not allowed')
    modules = git(path, 'submodule', 'status', '--recursive')
    if any(line.startswith(('-', '+', 'U')) for line in modules.splitlines()):
        raise ValueError(f'{path}: submodules must be initialized at pinned commits')


def check_firmware_unixnu(source, provenance):
    if provenance.get('unixnu_commit') != FIRMWARE_UNIXNU or provenance.get('darwinboot_commit') != DARWINBOOT:
        raise ValueError('firmware UniXNU/DarwinBoot provenance mismatch')
    changed = git(source, 'diff', '--name-only', FIRMWARE_UNIXNU, UNIXNU).splitlines()
    if changed != ['docs/guide/root-startup-timing.md', 'scripts/tests/check_native4k_page_validation.py']:
        raise ValueError('firmware UniXNU differs beyond documented host tests')
    for revision in (FIRMWARE_UNIXNU, UNIXNU):
        entry = git(source, 'ls-tree', revision, 'drivers/DarwinBoot').split()
        if len(entry) < 3 or entry[2] != DARWINBOOT:
            raise ValueError('DarwinBoot gitlink mismatch')


def check_uefi_checkout(uefi):
    if git(uefi, 'rev-parse', 'HEAD') != UEFI or git(uefi, 'status', '--porcelain', '--untracked-files=normal', '--ignore-submodules=all'):
        raise ValueError('UEFI top-level revision or cleanliness mismatch')
    if any(line and line[0]!='H' for line in git(uefi, 'ls-files', '-v').splitlines()):
        raise ValueError('UEFI hidden tracked-file flags are not allowed')


def check_uefi_firmware(uefi, fp):
    expected_submodules = ('Common/Mu', 'Common/Mu_OEM_Sample', 'Mu_Basecore')
    for name in expected_submodules:
        entry = git(uefi, 'ls-tree', 'HEAD', name).split()
        if len(entry) < 3 or fp.get('submodules', {}).get(name) != entry[2] or git(uefi / name, 'rev-parse', 'HEAD') != entry[2]:
            raise ValueError('UEFI submodule mismatch: ' + name)
    # Reconstruct the expected patched tree with a private Git index; do not
    # import executable code from the checkout being inspected.
    mu = uefi / 'Mu_Basecore'
    patches = ('AsmMacroLib.patch','Auth-Service.patch','Boot-Manager.patch','Timer.patch','Usb-Bus.patch')
    def tree(expected):
        with tempfile.TemporaryDirectory(prefix='a04br3-mu-index-') as temporary:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary)/'index'))
            subprocess.run(['git','-C',str(mu),'read-tree','HEAD'], env=env, check=True)
            if expected:
                for name in patches:
                    subprocess.run(['git','-C',str(mu),'apply','--cached',str(uefi/'Resources/MuPatches'/name)], env=env, check=True)
            else:
                subprocess.run(['git','-C',str(mu),'add','-u'], env=env, check=True)
            return subprocess.check_output(['git','-C',str(mu),'write-tree'],env=env,text=True).strip()
    if (git(mu, 'diff', '--cached', '--name-only') or git(mu, 'ls-files', '--others', '--exclude-standard') or
        any(line and line[0]!='H' for line in git(mu,'ls-files','-v').splitlines()) or
        tree(False) not in (git(mu, 'rev-parse', 'HEAD^{tree}'), tree(True)) or
        tree(True) != fp.get('mu_basecore_patched_tree')):
        raise ValueError('UEFI Mu_Basecore derived tree mismatch')
    for name in expected_submodules[:2]:
        if (git(uefi / name, 'status', '--porcelain', '--untracked-files=normal') or
            any(line and line[0]!='H' for line in git(uefi / name, 'ls-files', '-v').splitlines())):
            raise ValueError('dirty or hidden UEFI submodule: '+name)


def _manifest_path(manifest_path, value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (manifest_path.parent / path).resolve()


def _checked_manifest_file(manifest_path, entry, label):
    if not isinstance(entry, dict):
        raise ValueError(f'{label}: missing candidate file record')
    path = _manifest_path(manifest_path, entry.get('path', ''))
    if not path.is_file() or entry.get('sha256') != sha(path):
        raise ValueError(f'{label}: candidate file hash mismatch')
    return path


def _cdhashes_from_trust(path, label):
    try:
        values = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f'{label}: unreadable trust record') from error
    hashes = sorted({value.lower() for key, value in values.items()
                     if key.endswith('cdhash') and isinstance(value, str)
                     and len(value) == 40 and all(c in '0123456789abcdef' for c in value.lower())})
    if not hashes:
        raise ValueError(f'{label}: contains no valid CDHashes')
    return hashes


def _decoded_owner_cdhashes(path):
    """Decode the generated first owner module; provenance alone is not evidence."""
    raw = path.read_bytes()
    if len(raw) < 8:
        raise ValueError('derived trust module is truncated')
    count = struct.unpack_from('<I', raw)[0]
    if count < 1 or len(raw) < 4 + 4 * count:
        raise ValueError('derived trust module table is invalid')
    starts = list(struct.unpack_from('<%dI' % count, raw, 4)) + [len(raw)]
    start, end = starts[0], starts[1]
    if start < 4 + 4 * count or end > len(raw) or end - start < 24 or struct.unpack_from('<I', raw, start)[0] != 2:
        raise ValueError('derived owner module is invalid')
    entries = struct.unpack_from('<I', raw, start + 20)[0]
    if end - start != 24 + 24 * entries:
        raise ValueError('derived owner module entries are invalid')
    return sorted(raw[start + 24 + 24 * index:start + 44 + 24 * index].hex() for index in range(entries))


def check_candidate_contract(manifest_path, manifest, source, device, inputs, kp, fp, seed, official_dp, official_records):
    """Validate the new candidate inputs without weakening the official record."""
    record = _manifest_path(manifest_path, manifest.get('candidate_provenance', ''))
    if not record.is_file():
        raise ValueError('candidate provenance record is missing')
    try:
        candidate = json.loads(record.read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise ValueError('candidate provenance record is malformed') from error
    if candidate.get('schema') != 4 or candidate.get('kind') != 'iostat-roa-rows-candidate':
        raise ValueError('unexpected candidate provenance contract')
    sources = candidate.get('fixed_sources', {})
    if sources != {'unixnu': UNIXNU, 'device': DEVICE, 'uefi': UEFI}:
        raise ValueError('candidate fixed source commits mismatch')
    original = candidate.get('original_pre_build', {})
    required_original = ('kernel_provenance', 'firmware_provenance', 'seed_provenance', 'data_provenance', 'pre_build_log')
    if set(original) != set(required_original):
        raise ValueError('candidate does not bind every original pre-build record')
    for name in required_original:
        if original[name] != hashlib.sha256(official_records[name]).hexdigest():
            raise ValueError('candidate original pre-build record hash mismatch: ' + name)
    # Validate the original pre-build record exactly as the normal builder does,
    # except its data/trust/gpu hashes necessarily name the prior generation.
    if (official_dp.get('method') != 'official-pre-build-from-ipsw' or official_dp.get('source_commit') != UNIXNU or
        official_dp.get('device_commit') != DEVICE or official_dp.get('pre_build_sha256') != sha(device / 'scripts/a04br3-pre-build.sh') or
        official_dp.get('seed_record_sha256') != hashlib.sha256(official_records['seed_provenance']).hexdigest() or
        official_dp.get('pre_build_log_sha256') != hashlib.sha256(official_records['pre_build_log']).hexdigest()):
        raise ValueError('original official pre-build record mismatch')
    original_inputs = candidate.get('original_inputs', {})
    for name in ('data', 'trust', 'gpu'):
        entry = original_inputs.get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get('sha256'), str) or official_dp.get(name + '_sha256') != entry['sha256']:
            raise ValueError('original pre-build input hash mismatch: ' + name)
        original_path = _manifest_path(manifest_path, entry.get('path', ''))
        if not original_path.is_file() or sha(original_path) != entry['sha256']:
            raise ValueError('original pre-build input missing or changed: ' + name)
    expected_uuid = seed.get('volume_uuid')
    if not isinstance(expected_uuid, str) or not kp.get('data_uuid') or expected_uuid.upper() != kp['data_uuid'].upper():
        raise ValueError('original seed UUID and kernel provenance mismatch')
    staged = candidate.get('staged_seed', {})
    report = _checked_manifest_file(manifest_path, staged.get('report', {}), 'seed staging report')
    if (staged.get('expected_volume_uuid', '').upper() != expected_uuid.upper() or
        staged.get('source_data_sha256') != sha(_checked_manifest_file(manifest_path, staged.get('source_data', {}), 'seed source data')) or
        staged.get('grown_data_sha256') != sha(_checked_manifest_file(manifest_path, staged.get('grown_data', {}), 'grown data')) or
        staged.get('data_sha256') != sha(inputs['data']) or staged.get('data_sha256') != sha(_checked_manifest_file(manifest_path, staged.get('data', {}), 'staged data')) or
        staged.get('seed_stager_sha256') != sha(device / 'scripts/a04br3-seed-powervr-probe.py') or
        staged.get('grow_script_sha256') != sha(source / 'scripts/seed-data-grow.py') or
        staged.get('metal_mode') != 'publish' or staged.get('cli') != ['--metal-mode', 'publish', '--expected-volume-uuid', expected_uuid.upper()]):
        raise ValueError('candidate seed staging provenance mismatch')
    try:
        seed_report = json.loads(report.read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise ValueError('seed staging report is malformed') from error
    required_paths = {
        '/private/var/ngpu/bin/powervrprobed',
        '/private/var/ngpu/addons/NativePowerVRMetal.bundle/NativePowerVRMetal',
        '/private/var/ngpu/addons/NativePowerVRMetal.bundle/libvulkan_powervr_mesa.dylib',
        '/private/var/ngpu/addons/NativePowerVRMetal.publish',
    }
    if (seed_report.get('metal_mode') != 'publish' or not isinstance(seed_report.get('staged'), list) or
        not required_paths.issubset(set(seed_report['staged']))):
        raise ValueError('seed staging report does not describe the required publish tree')
    trusted = candidate.get('derived_trust', {})
    trust_base = _checked_manifest_file(manifest_path, trusted.get('base', {}), 'trust base')
    trust_file = _checked_manifest_file(manifest_path, trusted.get('output', {}), 'derived trust')
    if trust_file != inputs['trust'] or trusted.get('base_sha256') != sha(trust_base) or trusted.get('sha256') != sha(inputs['trust']):
        raise ValueError('candidate derived trust hash mismatch')
    plugin = candidate.get('plugin', {})
    plugin_dir = _manifest_path(manifest_path, plugin.get('path', ''))
    executable = plugin_dir / 'NativePowerVRMetal.bundle' / 'NativePowerVRMetal'
    plugin_trust = _checked_manifest_file(manifest_path, plugin.get('trust_record', {}), 'plugin trust record')
    if not executable.is_file() or plugin.get('executable_sha256') != sha(executable):
        raise ValueError('candidate plugin executable hash mismatch')
    probe_trust = _checked_manifest_file(manifest_path, plugin.get('probe_trust_record', {}), 'probe trust record')
    expected_hashes = sorted(set(_cdhashes_from_trust(plugin_trust, 'plugin trust')) | set(_cdhashes_from_trust(probe_trust, 'probe trust')))
    if plugin.get('verified_hashes') != expected_hashes:
        raise ValueError('candidate plugin verified hashes mismatch')
    decoded = _decoded_owner_cdhashes(trust_file)
    if trusted.get('decoded_owner_hashes') != expected_hashes or decoded != expected_hashes:
        raise ValueError('derived trust does not actually encode the four verified GPU CDHashes')
    gpu = candidate.get('gpu_addon', {})
    gpu_file = _checked_manifest_file(manifest_path, gpu, 'candidate GPU addon')
    if gpu_file != inputs['gpu'] or gpu.get('sha256') != sha(inputs['gpu']) or gpu.get('builder_sha256') != sha(device / 'scripts/make-a04br3-gpu-addon.py'):
        raise ValueError('candidate GPU addon provenance mismatch')
    return record, expected_uuid

def profile_lines(measurement='signature'):
    profile = HERE / 'a04br3-read-batch-profiles.py'
    namespace = {'__name__': 'a04br3_profile', '__file__': str(profile)}
    exec(compile(profile.read_bytes(), str(profile), 'exec'), namespace)
    template = json.loads((HERE / 'data/read-batch-baseline.json').read_text())
    return namespace['profiles'](template['kernel_args'], measurement)


def commands(source, device, uefi, out, variant, line, board, inputs):
    words = line.split()
    keys = [word.split('=', 1)[0] for word in words]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate profile arguments')
    if variant not in ('baseline', 'batch'):
        raise ValueError('unknown profile')
    board_words = board['payload']['kernel_args']
    board_keys = [word.split('=', 1)[0] for word in board_words]
    if len(board_keys) != len(set(board_keys)):
        raise ValueError('duplicate board arguments')
    omissions = [key for key in board_keys if key not in keys]
    tool = str(source / 'unixnu')
    common = ['-D', str(device), '--out', str(out / ('work-' + variant)), '--uefi', str(uefi)]
    payload = [tool, 'build', 'payload', *common, '--kernel', str(inputs['kernel']),
               '--data-ramdisk', str(inputs['data']), '--owner-trust-cache', str(inputs['trust']),
               '--gpu-addon', str(inputs['gpu'])]
    for word in words:
        payload.append('--kernel-arg=' + word)
    for key in omissions:
        payload.append('--omit-kernel-arg=' + key)
    image = out / variant / 'boot.img'
    boot = [tool, 'bootimg', *common, '--fd', str(inputs['fd']), '--dtb', str(inputs['dtb']),
            '--ramdisk', str(out / variant / 'payload.img'), '-o', str(image)]
    return payload, boot


def verify_data_uuid(source, device, data, expected, staged_seed, plugin_dir, report_path):
    if sys.platform != 'darwin':
        raise ValueError('official Data seed UUID validation needs macOS hdiutil')
    packed_path = source / 'drivers/DarwinBoot/Test/packed_ramdisk.py'
    stager_path = device / 'scripts/a04br3-seed-powervr-probe.py'
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    packed = load('a04br3_packed', packed_path)
    stager = load('a04br3_seed_stager', stager_path)
    logical, extents = packed.regions(data.read_bytes())
    folder = Path(tempfile.mkdtemp(prefix='a04br3-batch-seed-'))
    keep = False
    try:
        raw, mount = folder / 'data.raw', folder / 'mount'
        mount.mkdir()
        with raw.open('wb') as f:
            f.truncate(logical)
            for offset, content in extents:
                f.seek(offset)
                f.write(content)
        # Until the absence of this image is verified, retain the mountpoint
        # even on malformed output, interruption, or other unexpected errors.
        keep = True
        try:
            attached = plistlib.loads(subprocess.check_output(['hdiutil', 'attach', '-readonly',
                '-imagekey', 'diskimage-class=CRawDiskImage', '-nobrowse', '-mountpoint', str(mount), '-plist', str(raw)]))
            entities = attached.get('system-entities', [])
            image_device = next((entry.get('dev-entry') for entry in entities if entry.get('dev-entry')), None)
            if not image_device:raise ValueError('hdiutil did not report image device')
        except Exception as error:
            raise RuntimeError('image attach outcome uncertain; retained ' + str(folder)) from error
        try:
            stager.verify_volume_uuid(mount, uuid.UUID(expected))
            plan_work = folder / 'plan'
            planned = stager.plan(
                staged_seed / 'probe', False, plugin_dir, 'publish', None, plan_work)
            expected_files = {
                '/private/var/ngpu/' + target: (source_file, mode)
                for source_file, target, mode in planned
            }
            observed = []
            for guest_path, (source_file, mode) in sorted(expected_files.items()):
                destination = mount / guest_path.removeprefix('/private/var/')
                if not destination.is_file() or destination.is_symlink():
                    raise ValueError('seeded Data missing regular file: ' + guest_path)
                stat = destination.stat()
                actual_mode = format(stat.st_mode & 0o777, '04o')
                if stat.st_uid != 0 or stat.st_gid != 0 or actual_mode != mode or sha(destination) != sha(source_file):
                    raise ValueError('seeded Data content, owner, or mode mismatch: ' + guest_path)
                observed.append({'path': guest_path, 'sha256': sha(destination), 'mode': actual_mode, 'uid': stat.st_uid, 'gid': stat.st_gid})
            report = {'volume_uuid': str(uuid.UUID(expected)).upper(), 'files': observed}
            report_path.write_text(json.dumps(report, indent=2) + '\n')
        finally:
            detached = False
            for attempt in range(3):
                try:
                    subprocess.run(['hdiutil', 'detach', image_device], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    info = plistlib.loads(subprocess.check_output(['hdiutil', 'info', '-plist']))
                    if not isinstance(info.get('images'), list):raise ValueError('missing image list')
                    images = info['images']
                    if any(not isinstance(image,dict) or not isinstance(image.get('image-path'),str) or not image['image-path'] or
                           not isinstance(image.get('system-entities'),list) or not image['system-entities'] or
                           any(not isinstance(entry,dict) or not isinstance(entry.get('dev-entry'),str) or not entry['dev-entry']
                               for entry in image['system-entities']) for image in images):
                        raise ValueError('incomplete image information')
                    devices = {entry['dev-entry'] for image in images for entry in image['system-entities']}
                    paths = {os.path.realpath(image['image-path']) for image in images}
                    if image_device not in devices and os.path.realpath(raw) not in paths:
                        detached = True
                        keep = False
                        break
                except Exception:
                    pass
                if attempt < 2: time.sleep(1)
            if not detached:
                keep = True
                raise RuntimeError('image detach unverified: ' + image_device + '; retained ' + str(folder))
    finally:
        if keep:print('image verification files retained: ' + str(folder), file=sys.stderr)
        else:shutil.rmtree(folder)



def adt_comparison(path, identity):
    raw = path.read_bytes()
    fields = []
    random_count = 0
    identity_count = 0
    def node(offset, depth):
        nonlocal random_count, identity_count
        if depth > 64 or offset + 8 > len(raw):
            raise ValueError('invalid ADT node')
        properties, children = struct.unpack_from('<II', raw, offset)
        offset += 8
        if properties > 4096 or children > 4096:
            raise ValueError('invalid ADT counts')
        fields.append(('node', properties, children))
        for _ in range(properties):
            if offset + 36 > len(raw):
                raise ValueError('truncated ADT property')
            name = raw[offset:offset + 32].split(b'\0', 1)[0]
            size_field = struct.unpack_from('<I', raw, offset + 32)[0]
            size = size_field & 0x7fffffff
            offset += 36
            if offset + ((size + 3) & ~3) > len(raw):
                raise ValueError('truncated ADT value')
            value = raw[offset:offset + size]
            offset += (size + 3) & ~3
            if name == b'random-seed':
                if size != 64:
                    raise ValueError('unexpected random-seed length')
                random_count += 1
                value = b'<random-seed>'
            if name == b'unique-chip-id':
                identity_count += 1
                if value != struct.pack('<Q', identity):
                    raise ValueError('ADT software identity mismatch')
            fields.append((name, size_field, value))
        for _ in range(children):
            offset = node(offset, depth + 1)
        return offset
    if node(0, 0) != len(raw) or random_count != 1 or identity_count != 1:
        raise ValueError('unexpected ADT structure')
    return fields


def validate_cli(source, payload_cmd, boot_cmd, line, board):
    sys.path.insert(0, str(source / 'scripts'))
    try:
        from unixnu import cli, payload
        if Path(cli.__file__).resolve().parent != (source / 'scripts/unixnu').resolve():
            raise ValueError('unexpected CLI module origin')
        parser = cli.parser()
        parsed = parser.parse_args(payload_cmd[1:])
        boot = parser.parse_args(boot_cmd[1:])
        if parsed.command != 'build' or parsed.steps != ['payload'] or boot.command != 'bootimg':
            raise ValueError('official CLI command mismatch')
        actual = payload.kernel_arguments(board['payload']['kernel_args'], parsed.kernel_arg, parsed.omit_kernel_arg)
        if ' '.join(actual) != line or len(line.encode('ascii')) > 1023:
            raise ValueError('official CLI argument merge mismatch')
    finally:
        sys.path.pop(0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'device', 'uefi', 'inputs', 'out'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--log-dir', type=Path, required=True)
    p.add_argument('--measurement', choices=('signature','iostat'), default='signature')
    args = p.parse_args()
    preparation_commit = check_preparation_source()
    candidate_builder_sha256 = sha(Path(__file__).resolve())
    source, device, uefi = (args.source.resolve(), args.device.resolve(), args.uefi.resolve())
    out = args.out.resolve()
    check_repo(source, UNIXNU)
    check_repo(device, DEVICE)
    if git(device / 'drivers/ArmCpu', 'rev-parse', 'HEAD') != ARMCPU or git(source / 'drivers/NativeMsdc', 'rev-parse', 'HEAD') != MSDC:
        raise ValueError('ArmCpu or NativeMsdc checkout mismatch')
    if any((source / 'scripts/unixnu').rglob('*.pyc')):
        raise ValueError('ignored Python bytecode found in fixed CLI checkout')
    check_uefi_checkout(uefi)
    manifest_path = args.inputs.resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    def input_path(name):
        value = Path(name)
        return value.resolve() if value.is_absolute() else (manifest_path.parent / value).resolve()
    if manifest.get('source_commit') != UNIXNU or manifest.get('device_commit') != DEVICE or manifest.get('uefi_commit') != UEFI:
        raise ValueError('input provenance does not match fixed sources')
    if manifest.get('data_origin') != 'official-pre-build-from-ipsw':
        raise ValueError('Data seed must come from the official IPSW pre-build, not device extraction')
    if manifest.get('root_timing') != 1:
        raise ValueError('kernel must be built with XNU_ROOT_TIMING=1')
    inputs = {}
    for name in ('kernel', 'data', 'trust', 'gpu', 'fd', 'dtb'):
        entry = manifest['files'][name]
        path = input_path(entry['path'])
        if not path.is_file() or sha(path) != entry['sha256']:
            raise ValueError(f'{name}: missing file or hash mismatch')
        inputs[name] = path
    kernel_bytes = inputs['kernel'].read_bytes()
    if any(flag not in kernel_bytes for flag in (b'native-root-timing', b'native-cs-read-batch')):
        raise ValueError('kernel lacks the required diagnostic flag strings')
    record_names = ('kernel_provenance', 'firmware_provenance', 'data_provenance', 'seed_provenance', 'pre_build_log')
    record_bytes = {name:input_path(manifest[name]).read_bytes() for name in record_names}
    kp = json.loads(record_bytes['kernel_provenance'])
    fp = json.loads(record_bytes['firmware_provenance'])
    if (kp.get('source_commit') != UNIXNU or kp.get('device_commit') != DEVICE or
        kp.get('armcpu_commit') != ARMCPU or kp.get('native_msdc_commit') != MSDC or
        kp.get('kernel_sha256') != sha(inputs['kernel']) or kp.get('kernel_build_flags', {}).get('XNU_ROOT_TIMING') != '1'):
        raise ValueError('kernel provenance mismatch')
    if fp.get('uefi_commit') != UEFI or fp.get('fd_sha256') != sha(inputs['fd']):
        raise ValueError('firmware provenance mismatch')
    check_firmware_unixnu(source, fp)
    if (not kp.get('reference_kernel_sha256') or kp.get('pmgr_build_report', {}).get('virtual_entries') != 340 or
        kp.get('display_build_report', {}).get('virtual_entries') != 358 or
        kp.get('display_build_report', {}).get('original_candidate_sha256') != kp.get('reference_kernel_sha256')):
        raise ValueError('kernel PMGR/display provenance mismatch')
    seed = json.loads(record_bytes['seed_provenance'])
    dp = json.loads(record_bytes['data_provenance'])
    candidate_record, candidate_uuid = check_candidate_contract(manifest_path, manifest, source, device, inputs, kp, fp, seed, dp, record_bytes)
    check_uefi_firmware(uefi, fp)
    if inputs['dtb'] != (uefi / 'Resources/DTBs/a04br3.dtb').resolve():
        raise ValueError('DTB must come from the fixed UEFI checkout')
    if out.exists():
        raise ValueError('output already exists')
    board = tomllib.loads((device / 'board.toml').read_text())
    if args.measurement == 'iostat' and b'native-msdc-iostat' not in kernel_bytes:
        raise ValueError('kernel lacks native-msdc-iostat diagnostic flag string')
    profiles = profile_lines(args.measurement)
    if profiles['batch'] != profiles['baseline'] + ' native-cs-read-batch=1':
        raise ValueError('profiles are not a one-token comparison')
    for variant, line in profiles.items():
        c1, c2 = commands(source, device, uefi, out, variant, line, board, inputs)
        validate_cli(source, c1, c2, line, board)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.log_dir / (out.name + '.build.log')
    if log_path.exists():
        raise ValueError('build log already exists')
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.'+out.name+'.', dir=out.parent))
    try:
        identity = stage / 'software-identity.txt'
        # A new software identity is generated per pair; it is not read from a device.
        value = secrets.randbits(64) or 1
        fd = os.open(identity, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(f'{value:016x}\n')
        env = dict(os.environ, UNIXNU_UNIQUE_CHIP_ID=f'{value:016x}', PYTHONDONTWRITEBYTECODE='1',
                   PYTHONPYCACHEPREFIX=str(stage/'isolated-pycache'))
        # Copy all payload inputs into this private staging directory and verify
        # their original hashes again before either variant can consume them.
        staged = stage / 'inputs'; staged.mkdir(mode=0o700)
        recorded_hashes = {name: sha(path) for name, path in inputs.items()}
        frozen = {}
        for name, original in inputs.items():
            target = staged / name
            shutil.copyfile(original, target)
            if sha(target) != recorded_hashes[name] or recorded_hashes[name] != manifest['files'][name]['sha256']:
                raise ValueError(f'{name}: input changed during copy')
            frozen[name] = target
        inputs = frozen
        # Check the exact immutable staged data that the payload builder will use.
        verified_seed = stage / 'seed-verification.json'
        seed_inputs = stage / 'seed-inputs'
        seed_inputs.mkdir()
        shutil.copytree(_manifest_path(manifest_path, manifest.get('candidate_provenance', '')).parent.parent / 'fresh' / 'powervr-probe', seed_inputs / 'probe')
        verify_data_uuid(source, device, inputs['data'], candidate_uuid, seed_inputs, _manifest_path(manifest_path, json.loads(candidate_record.read_text())['plugin']['path']), verified_seed)
        report = {'measurement': args.measurement, 'source_commit': UNIXNU, 'device_commit': DEVICE, 'uefi_commit': UEFI, 'firmware_unixnu_commit': FIRMWARE_UNIXNU, 'firmware_darwinboot_commit': DARWINBOOT, 'preparation_commit': preparation_commit,
                  'inputs_sha256': recorded_hashes,
                  'input_records_sha256': {name: hashlib.sha256(record_bytes[name]).hexdigest() for name in record_names},
                  'identity_origin': 'new software identity generated per pair; diagnostic only',
                  'identity_sha256': sha(identity),
                  'preparation_source_sha256': {str(p.relative_to(HERE)):sha(p) for p in (HERE/'a04br3-read-batch-build.py', HERE/'a04br3-read-batch-record-inputs.py', HERE/'a04br3-read-batch-profiles.py', HERE/'data/read-batch-baseline.json')},
                  'validator_sha256': sha(device/'scripts/check-a04br3-bootimg.py'),
                  'data_origin': 'new candidate Data staged from the recorded official baseline; original pre-build records retained and independently checked',
                  'candidate_provenance_sha256': sha(candidate_record), 'candidate_builder_sha256': candidate_builder_sha256,
                  'adt_random_seed': 'fresh per variant; boot behavior may differ, do not assume a single-variable test',
                  'identity_scope': 'synthetic diagnostic pair only; not a device identity or distributable image',
                  'hardware_tested': False, 'signature_verified_by_builder': False, 'release_status': 'diagnostic only; not approved for hardware or distribution', 'variants': {}}
        baseline_adt = None
        for variant, line in profiles.items():
            target = stage / variant
            target.mkdir()
            payload_cmd, boot_cmd = commands(source, device, uefi, stage, variant, line, board, inputs)
            with log_path.open('a', encoding='utf-8') as log:
                subprocess.run(payload_cmd, cwd=source, env=env, check=True, stdout=log, stderr=subprocess.STDOUT)
            payload_dir = stage / ('work-' + variant) / 'a04br3/payload'
            meta = json.loads((payload_dir / 'payload.json').read_text())
            checks = {'kernel_sha256': 'kernel', 'data_ramdisk_sha256': 'data',
                      'trust_cache_sha256': 'trust', 'gpu_addon_sha256': 'gpu'}
            if meta['kernel_args'] != line or any(meta[key] != sha(inputs[name]) for key, name in checks.items()):
                raise ValueError(f'{variant}: payload profile or input mismatch')
            adt = target / 'kernel.macho.adt'
            with log_path.open('a', encoding='utf-8') as log:
                subprocess.run(['mcopy', '-i', str(payload_dir / 'payload.img'),
                                '::/EFI/UniXNU/Payloads/kernel.macho.adt', str(adt)],
                               check=True, stdout=log, stderr=subprocess.STDOUT)
            if sha(adt) != meta['adt_sha256']:
                raise ValueError(f'{variant}: ADT hash mismatch')
            adt_fields = adt_comparison(adt, value)
            if baseline_adt is None:
                baseline_adt = adt_fields
            elif baseline_adt != adt_fields:
                raise ValueError('paired ADTs differ beyond their random seeds')
            shutil.copyfile(payload_dir / 'payload.img', target / 'payload.img')
            if sha(target / 'payload.img') != meta['payload_sha256']:
                raise ValueError(f'{variant}: payload copy mismatch')
            with log_path.open('a', encoding='utf-8') as log:
                subprocess.run(boot_cmd, cwd=source, check=True, stdout=log, stderr=subprocess.STDOUT)
                subprocess.run([sys.executable, str(device / 'scripts/check-a04br3-bootimg.py'), str(target / 'boot.img')], check=True, stdout=log, stderr=subprocess.STDOUT)
            report['variants'][variant] = {'kernel_args': line, 'payload_sha256': sha(target / 'payload.img'),
                                           'boot_sha256': sha(target / 'boot.img'), 'adt_sha256': meta['adt_sha256']}
        check_repo(source, UNIXNU)
        check_repo(device, DEVICE)
        check_uefi_checkout(uefi)
        report['build_log_sha256'] = sha(log_path)
        (stage / 'NOT_FOR_DISTRIBUTION.txt').write_text('Diagnostic software identity; hardware and provenance are unverified.\n')
        shutil.copyfile(log_path, stage / 'build.log')
        shutil.rmtree(stage / 'work-baseline')
        shutil.rmtree(stage / 'work-batch')
        shutil.rmtree(staged)
        shutil.rmtree(stage / 'isolated-pycache', ignore_errors=True)
        (stage / 'pair.json').write_text(json.dumps(report, indent=2) + '\n')
        if out.exists():
            raise ValueError('output appeared while building')
        stage.rename(out)
        print('Prepared baseline and batch; hardware use not verified:', out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, TypeError, AttributeError, OSError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
