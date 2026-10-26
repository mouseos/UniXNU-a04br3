# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Optional board archive integration; imported by build-a04br3-board.py."""
import hashlib
import json
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import urllib.request
from a04br3_wmt_firmware import pack

VENDOR_REF = '0c756f413e7011231c8fb793af0930311023f68d'
FIRMWARE = {
    'WIFI_RAM_CODE_MT6630': '07fe1a225e6b9de6b310894db3b40bcc1cb66107b913e4c43eb6a62089aada22',
    'mt6630_patch_e3_0_hdr.bin': 'cd345f88f69566aa77bf64ec4bb7e3035c5cdf45a9dc6084c8381b55c441752c',
    'mt6630_patch_e3_1_hdr.bin': '45ddf35f02e8ea957be2146fbc7396b14408d0bf66418779e279ff99fa589026',
}

def firmware(out):
    out.mkdir(parents=True, exist_ok=True)
    for name, digest in FIRMWARE.items():
        path = out / name
        if not path.exists():
            url = f'https://raw.githubusercontent.com/mouseos/android_vendor_benesse_a04br3/{VENDOR_REF}/proprietary/vendor/firmware/{name}'
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read(16 * 1024 * 1024 + 1)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f'firmware hash mismatch: {name}')
            path.write_bytes(data)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'cached firmware hash mismatch: {name}')
    wmt, report = pack([out / name for name in FIRMWARE if name.startswith('mt6630_patch')])
    (out / 'wmt.bin').write_bytes(wmt)
    (out / 'firmware.json').write_text(json.dumps({'vendor_ref': VENDOR_REF, 'sha256': FIRMWARE, 'wmt': report}, indent=2) + '\n')
    return out / 'wmt.bin', out / 'WIFI_RAM_CODE_MT6630'


def source_files(source):
    return [*sorted((source / 'network/apple').glob('*.cpp')),
            *sorted((source / 'network').glob('*.cpp')), source / 'sdio/A04BR3SDIO.cpp', source / 'WifiBoard.cpp']


BLOCKED_NETWORK_FAMILIES = frozenset((
    'com.apple.iokit.IONetworkingFamily', 'com.apple.iokit.IO80211Family',
))


def effective_roots(profile):
    """Match the qualified emitter's appended --root arguments; fail closed on input rewrites."""
    if profile.get('tool') != 'kits/NativePrelink/emit_prelink.py':
        raise ValueError('WiFi requires the qualified NativePrelink emitter')
    roots = profile.get('roots')
    extra = profile.get('extra_args', [])
    if not isinstance(roots, list) or not roots or not all(isinstance(r, str) and r for r in roots):
        raise ValueError('missing/invalid effective prelink roots')
    if not isinstance(extra, list) or not all(isinstance(a, str) for a in extra):
        raise ValueError('invalid prelink extra_args')
    roots = list(roots)
    i = 0
    while i < len(extra):
        arg = extra[i]
        if arg in ('--root', '--early-security-init'):
            if i + 1 == len(extra) or not extra[i + 1] or extra[i + 1].startswith('-'):
                raise ValueError('missing prelink extra argument value')
            if arg == '--root': roots.append(extra[i + 1])
            i += 2
        elif arg.startswith('--root='):
            if not arg[7:]: raise ValueError('empty extra prelink root')
            roots.append(arg[7:]); i += 1
        elif arg.startswith('--early-security-init=') and arg.partition('=')[2]:
            i += 1
        else:
            raise ValueError('WiFi prelink guard cannot qualify extra argument: ' + arg)
    return roots


def prelink_closure(entries, roots):
    """Same OSBundleLibraries/OSKernelResource closure as NativePrelink.plan."""
    by_id = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('CFBundleIdentifier'), str):
            raise ValueError('malformed prelink entry')
        name = entry['CFBundleIdentifier']
        if name in by_id: raise ValueError('duplicate prelink identifier: ' + name)
        by_id[name] = entry
    selected, interfaces, pending = set(), set(), list(roots)
    while pending:
        name = pending.pop()
        if name in BLOCKED_NETWORK_FAMILIES:
            raise ValueError('source WiFi conflicts with original family: ' + name)
        if name in selected or name in interfaces: continue
        if name not in by_id: raise ValueError('missing prelink dependency: ' + name)
        entry = by_id[name]
        if entry.get('OSKernelResource') is True:
            interfaces.add(name); continue
        dependencies = entry.get('OSBundleLibraries') or {}
        if not isinstance(dependencies, dict) or not all(isinstance(k, str) and k for k in dependencies):
            raise ValueError('malformed prelink dependencies: ' + name)
        selected.add(name); pending.extend(dependencies)
    return selected, interfaces


def guard_prelink(root, environment):
    """Before compile, check actual profile/cache supplied by the official optional archive hook."""
    if environment.get('A04BR3_WIFI') != '1': return None
    try:
        profile = json.loads(environment['UNIXNU_PRELINK_SPEC'])
        cache = Path(environment['UNIXNU_PRELINK_KERNELCACHE'])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('WiFi needs official archive preflight profile/cache inputs') from error
    if not isinstance(profile, dict): raise ValueError('invalid prelink profile')
    roots = effective_roots(profile)
    # Reject direct conflicts even if the cache is unavailable/malformed.
    if BLOCKED_NETWORK_FAMILIES.intersection(roots):
        raise ValueError('source WiFi conflicts with original family in effective roots')
    data = cache.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    wanted = profile.get('kernelcache_sha256')
    if not isinstance(wanted, str) or not re.fullmatch('[0-9a-f]{64}', wanted) or wanted != digest:
        raise ValueError('WiFi prelink cache hash does not match the actual profile')
    parser_path = root / 'kits/NativePrelink/native_prelink.py'
    spec = importlib.util.spec_from_file_location('a04_wifi_prelink_reader', parser_path)
    parser = importlib.util.module_from_spec(spec); spec.loader.exec_module(parser)
    image = parser.macho(data)
    info = [s for s in image['segments'] if s['name'] == '__PRELINK_INFO']
    if image['kind'] != 2 or len(info) != 1: raise ValueError('invalid prelink metadata image')
    info = info[0]
    offset = parser.backed_offset(image['segments'], info['va'], info['filesize'], len(data))
    metadata = parser.unserialize_xml(data[offset:offset + info['filesize']])
    entries = metadata.get('_PrelinkInfoDictionary') if isinstance(metadata, dict) else None
    if not isinstance(entries, list): raise ValueError('missing prelink entries')
    selected, interfaces = prelink_closure(entries, roots)
    return {'kernelcache_sha256': digest, 'effective_roots': roots,
            'selected': sorted(selected), 'interfaces': sorted(interfaces)}


def build_wifi(root, kit, out, compile_one, cxx_record, c_record, cxx_flags, c_flags, objcopy, text_section):
    guard_prelink(root, os.environ)
    source = kit / 'wifi'
    includes = out / 'wifi-include/IOKit/network'
    includes.mkdir(parents=True, exist_ok=True)
    for header in (source / 'network/apple').glob('*.h'):
        link = includes / header.name
        if link.is_symlink(): link.unlink()
        link.symlink_to(header.resolve())
    flags = cxx_flags + ['-DA04BR3_WIFI=1', '-DARM64_BOARD_CONFIG_A04BR3=1', '-D__PRIVATE_SPI__=1',
                         '-I', str(includes.parents[1]), '-Wno-unused-private-field']
    objects = []
    sources = source_files(source)
    for path in sources:
        obj = compile_one(cxx_record, path, flags)
        objects.append(obj)
    for path in ('hash/sha1.c', 'codec/dec32be.c', 'codec/enc32be.c', 'symcipher/aes_ct.c',
                 'symcipher/aes_ct_dec.c', 'symcipher/aes_ct_cbcdec.c'):
        objects.append(compile_one(c_record, source / 'bearssl/src' / path,
            c_flags + ['-I', str(source / 'bearssl/inc'), '-I', str(source / 'bearssl/src'), '-Wno-unused-parameter']))
    wmt, wlan = firmware(out / 'wifi-firmware')
    asm = out / 'WifiFirmware.S'
    lines = ['.section __DATA_CONST,__const']
    for name, path in [('wmt', wmt), ('wlan', wlan)]:
        if any(c in str(path.resolve()) for c in '\n"'): raise ValueError('invalid firmware path')
        lines += [f'.globl _a04br3_{name}_firmware, _a04br3_{name}_firmware_end', '.balign 16',
                  f'_a04br3_{name}_firmware:', f'.incbin "{path.resolve()}"', f'_a04br3_{name}_firmware_end:']
    asm.write_text('\n'.join(lines) + '\n')
    fw_obj = out / 'WifiFirmware.o'
    subprocess.run(['xcrun', '--sdk', 'iphoneos', 'clang', '-arch', 'arm64', '-c', str(asm), '-o', str(fw_obj)], check=True)
    objects.append(fw_obj)
    (out / 'wifi-build.json').write_text(json.dumps({'initializers': 'standard kernel __mod_init_func (source classes, no original NetworkingFamily prelink)', 'sources': [str(p) for p in sources],
        'objects': [str(p) for p in objects], 'runtime_opt_in': 'a04br3-wifi=1'}, indent=2) + '\n')
    return objects
