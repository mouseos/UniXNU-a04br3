#!/usr/bin/env python3
"""Build the PowerVR user-space probe powervrprobed (package/powervr/user) for ARM64/iOS 17, sign it and prepare everything
the a04br3 boot needs to run it.

  build-a04br3-powervr-probe.py [--vulkan [LIBRARY]] [--out DIR] [--trust-base BUNDLE --trust-out FILE] [--addon FILE]
  build-a04br3-powervr-probe.py --check            (any host: syntax of the sources, plist, trust bundle and addon logic)

The pieces follow UniXNU scripts/build-adreno-metal-plugin.py (the Adreno probe daemon adrenoprobed), make-owner-trust-bundle.py and
make-gpu-addon.py:

  OUT/powervrprobed                   ad-hoc signature with 4 KiB pages and the entitlements below
  OUT/lib/libvulkan_powervr_mesa.dylib  with --vulkan: Mesa's PowerVR driver (scripts/build-pvr-darwin.py), signed the same way
  OUT/powervrprobed-entitlements.plist  build input
  OUT/org.unixnu.a04br3.powervrprobed.plist  a launchd job (see "Start" below)
  OUT/trust.json                      the CDHashes
  --trust-out FILE                    trust bundle: one owner module with those CDHashes, then the modules of --trust-base (the
                                      original bundle of build-a04br3-trust-cache.py, checked against board.toml
                                      [payload] trust_cache_sha256) byte for byte; for "unixnu build payload --owner-trust-cache"
  --addon FILE                        GPU addon (format NGPUADD2 of make-gpu-addon.py) with bin/powervrprobed (and identical lib/... + addons/NativePowerVRVulkan.bundle/...); for
                                      "unixnu build payload --gpu-addon" with the kernel argument sheng-gpu-addon=1

Vulkan. Without --vulkan the daemon is built without its Mesa stage (powervrprobed_vulkan.c is not compiled). With --vulkan it is
compiled with POWERVRPROBED_VULKAN=1 against the Vulkan headers of the pinned Mesa (.cache/mesa-pvr/include, or --vulkan-include)
and opens LIBRARY at run time from /private/var/ngpu/lib (dlopen: no link-time dependency). LIBRARY defaults to the output of
"build-pvr-darwin.py --target ios".

Entitlements: com.apple.private.security.no-container (an owner binary without it is refused by AMFI's launch constraints when it is
not started by launchd: UniXNU-droidvm build-droidvm-zsh.py/-toybox.py) and com.apple.security.iokit-user-client-class naming the one
class it opens (the grant adrenoprobed carries; consulted only under a sandbox profile).

Start. launchd on iPadOS 17 runs only the jobs of its signed compiled cache and ignores an overlay plist's ProgramArguments and new
labels (UniXNU-tb128fu board.toml, UniXNU-droidvm board.toml), so the a04br3 kernel starts the probe itself from the board launch hook
(package/A04BR3ProbeLaunch.c, a04br3-powervrprobed=1). The job plist is written for parity with adrenoprobed and goes into the addon
only with --addon-launchd-job.

Output stays under build/ (ignored); nothing is committed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'org.unixnu.a04br3.powervrprobed'
IDENTIFIER = LABEL
LIB_IDENTIFIER = 'org.unixnu.a04br3.libvulkan_powervr_mesa'
DEVICE_BIN = '/private/var/ngpu/bin/powervrprobed'
LIB_NAME = 'libvulkan_powervr_mesa.dylib'
ENTITLEMENTS = {'com.apple.private.security.no-container': True,
                'com.apple.security.iokit-user-client-class': ['NativePowerVRUserClient']}


def powervr_dir():
    """package/powervr of the device repository, or kits/<kit>/powervr in a UniXNU build tree (as build-pvr-darwin.py)."""
    for directory in [ROOT / 'package/powervr'] + sorted((ROOT / 'kits').glob('*/powervr')):
        if (directory / 'user/powervrprobed.c').is_file() and (directory / 'NativePowerVRABI.h').is_file():
            return directory
    sys.exit('package/powervr/user/powervrprobed.c not found')


def flags(powervr, vulkan_include):
    result = ['-std=gnu11', '-Wall', '-Wextra', '-Werror', '-I', powervr / 'user', '-I', powervr / 'user/compat', '-I', powervr,
              '-I', powervr / 'linux/uapi']
    sources = [powervr / 'user/powervrprobed.c']
    if vulkan_include:
        result += ['-DPOWERVRPROBED_VULKAN=1', '-I', vulkan_include]
        sources.append(powervr / 'user/powervrprobed_vulkan.c')
    return [str(f) for f in result], [str(s) for s in sources]


def tq_clip_probe_source():
    """Owner-probe-only startup flag; absent from the default build and Mesa library."""
    return r'''#include <stdlib.h>
#include <stdio.h>
__attribute__((constructor)) static void enable_tq_clip_comparison(void)
{
    if (setenv("PVR_DARWIN_TQ_CLIP", "1", 1) != 0) {
        fputs("powervrprobed: cannot enable TQ clip comparison\n", stderr);
        _Exit(1);
    }
}
'''


def tq_first_probe_source():
    """Change only the owner's test order; this does not enable a Mesa workaround."""
    return r'''#include <stdlib.h>
#include <stdio.h>
__attribute__((constructor)) static void enable_tq_first_comparison(void)
{
    if (setenv("PVR_PROBE_TQ_FIRST", "1", 1) != 0) {
        fputs("powervrprobed: cannot enable TQ-first comparison\n", stderr);
        _Exit(1);
    }
}
'''


def ui_fill_probe_source():
    """Select only the explicit owner UI-fill test; Mesa gates remain separate."""
    return tq_first_probe_source().replace('enable_tq_first_comparison', 'enable_ui_fill_comparison').replace(
        'PVR_PROBE_TQ_FIRST', 'PVR_PROBE_UI_FILL').replace('TQ-first comparison', 'UI-fill comparison')


def ui_batch_probe_source():
    """Select four-mip owner UI-batch comparison without changing Mesa routing."""
    return tq_first_probe_source().replace('enable_tq_first_comparison', 'enable_ui_batch_comparison').replace(
        'PVR_PROBE_TQ_FIRST', 'PVR_PROBE_UI_BATCH').replace('TQ-first comparison', 'UI-batch comparison')


def submit_barrier_probe_source():
    """Enable only the explicit UMD pre-submit store-barrier comparison."""
    return tq_first_probe_source().replace('enable_tq_first_comparison', 'enable_submit_barrier_comparison').replace(
        'PVR_PROBE_TQ_FIRST', 'PVR_DARWIN_SUBMIT_BARRIER').replace('TQ-first comparison', 'submit barrier comparison')


def serial_transfer_probe_source():
    """Serialize transfer submissions only in the explicit diagnostic probe."""
    return tq_first_probe_source().replace('enable_tq_first_comparison', 'enable_serial_transfer_comparison').replace(
        'PVR_PROBE_TQ_FIRST', 'PVR_DARWIN_SERIAL_TRANSFER').replace('TQ-first comparison', 'serial transfer comparison')


def sipf1_encoding_probe_source():
    """Enable v1 encoding only; leave upstream routing and external gates intact."""
    return tq_first_probe_source().replace('enable_tq_first_comparison', 'enable_sipf1_encoding_comparison').replace(
        'PVR_PROBE_TQ_FIRST', 'PVR_DARWIN_SIPF1').replace('TQ-first comparison', 'SIPF1 encoding comparison')


def sipf1_dump_probe_source():
    """Enable existing bounded snapshot diagnostics only in this owner probe."""
    return tq_first_probe_source().replace('enable_tq_first_comparison', 'enable_sipf1_dump_comparison').replace(
        'PVR_PROBE_TQ_FIRST', 'PVR_DARWIN_SIPF1_DUMP').replace('TQ-first comparison', 'SIPF1 dump comparison')


def job_plist():
    return plistlib.dumps(dict(Label=LABEL, ProgramArguments=[DEVICE_BIN], RunAtLoad=True, KeepAlive=False, UserName='root',
                               StandardErrorPath='/dev/null'), fmt=plistlib.FMT_XML)


# ---- trust bundle (UniXNU make-owner-trust-bundle.py) ----
def valid_like_the_loader(data):
    """The structural checks of drivers/DarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c ValidTrustCaches, as make-owner-trust-bundle.py."""
    count = struct.unpack_from('<I', data)[0]
    if not 0 < count <= 128:
        return False
    for index in range(count):
        start = struct.unpack_from('<I', data, 4 + 4 * index)[0]
        end = struct.unpack_from('<I', data, 8 + 4 * index)[0] if index + 1 < count else len(data)
        if start < 4 + 4 * count or start & 3 or end > len(data) or end - start < 24 or struct.unpack_from('<I', data, start)[0] != 2:
            return False
        entries = struct.unpack_from('<I', data, start + 20)[0]
        if end - start != 24 + entries * 24:
            return False
        for entry in range(entries):
            at = start + 24 + entry * 24
            if not 1 <= data[at + 20] <= 4 or (entry and data[at - 24:at - 4] >= data[at:at + 20]):
                return False
    return True


def owner_bundle(original, cdhashes):
    """One owner module (version 2, hash type 2, flags 0, constraint category 0, as make-owner-trust-bundle.py), then the
    original modules unchanged."""
    if not valid_like_the_loader(original):
        raise SystemExit('The base trust bundle is not one the boot loader accepts')
    count = struct.unpack_from('<I', original)[0]
    offsets = list(struct.unpack_from('<%dI' % count, original, 4)) + [len(original)]
    originals = [original[offsets[i]:offsets[i + 1]] for i in range(count)]
    hashes = sorted(bytes.fromhex(h) for h in cdhashes)
    if len(set(hashes)) != len(hashes) or any(len(h) != 20 for h in hashes):
        raise SystemExit('CDHashes must be distinct 20-byte values')
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, LABEL + ':' + ''.join(h.hex() for h in hashes))
    own = struct.pack('<I16sI', 2, identifier.bytes, len(hashes)) + b''.join(h + bytes([2, 0, 0, 0]) for h in hashes)
    modules = [own, *originals]
    cursor, starts = 4 + 4 * len(modules), []
    for module in modules:
        starts.append(cursor)
        cursor += len(module)
    bundle = struct.pack('<%dI' % (1 + len(modules)), len(modules), *starts) + b''.join(modules)
    if not valid_like_the_loader(bundle):
        raise SystemExit('The trust bundle would be refused by the boot loader')
    if bundle[starts[1]:] != original[offsets[0]:]:
        raise SystemExit('The original modules changed')
    return bundle


def pinned_hashes(board):
    import tomllib
    return tomllib.loads(board.read_text()).get('payload', {}).get('trust_cache_sha256', [])


# ---- GPU addon (UniXNU make-gpu-addon.py, format NGPUADD2) ----
def pad(data):
    return data + bytes(-len(data) % 8)


def pack_addon(files):
    body = b''
    for name, mode, data in files:
        encoded = name.encode() + b'\0'
        if len(encoded) > 160 or '..' in name.split('/') or name.startswith('/') or mode & ~0o777:
            raise SystemExit('Bad addon path ' + name)
        packed = zlib.compress(data, 9)
        body += struct.pack('<IIQQ', len(encoded), mode, len(data), len(packed)) + pad(encoded) + pad(packed)
    return b'NGPUADD2' + struct.pack('<IIQ', len(files), 0, 24 + len(body)) + body


def unpack_addon(blob):
    """The kernel's validation (zzy-native-gpu-addon-mount.patch native_gpu_addon_install()), for the self-check."""
    assert blob[:8] == b'NGPUADD2'
    count, _, total = struct.unpack_from('<IIQ', blob, 8)
    assert total == len(blob) and 0 < count <= 4096
    at, files = 24, []
    for _ in range(count):
        name_bytes, mode, size, packed = struct.unpack_from('<IIQQ', blob, at)
        at += 24
        name = blob[at:at + name_bytes - 1].decode()
        assert 2 <= name_bytes <= 160 and blob[at + name_bytes - 1] == 0 and not name.startswith('/') and '..' not in name
        assert not mode & ~0o777 and 0 < packed
        at += (name_bytes + 7) & ~7
        data = zlib.decompress(blob[at:at + packed])
        assert len(data) == size
        files.append((name, mode, data))
        at += (packed + 7) & ~7
    assert at == len(blob)
    return files


def addon_files(out, with_job):
    files = [('bin/powervrprobed', 0o755, (out / 'powervrprobed').read_bytes())]
    if (out / 'lib' / LIB_NAME).is_file():
        # Read the final signed file once: seed-compatible runtime path and
        # legacy lib path must carry exactly the same bytes/CDHash.
        signed_library = (out / 'lib' / LIB_NAME).read_bytes()
        files.append(('lib/' + LIB_NAME, 0o755, signed_library))
        files.append(('addons/NativePowerVRVulkan.bundle/' + LIB_NAME, 0o755, signed_library))
    if with_job:
        files.append(('daemons/%s.plist' % LABEL, 0o644, (out / ('%s.plist' % LABEL)).read_bytes()))
    return files


# ---- signing ----
def sign(path, identifier, entitlements=None):
    command = ['codesign', '--force', '--sign', '-', '--pagesize', '4096', '--identifier', identifier]
    if entitlements:
        command += ['--entitlements', str(entitlements)]
    subprocess.run(command + [str(path)], check=True)
    subprocess.run(['codesign', '--verify', '--strict', str(path)], check=True)
    detail = subprocess.run(['codesign', '--display', '--verbose=4', str(path)], capture_output=True, text=True, check=True).stderr
    if not re.search(r'^Page size=4096$', detail, re.M):
        raise SystemExit('%s: the signature must use 4 KiB pages' % path)
    return re.search(r'^CDHash=([a-f0-9]{40})$', detail, re.M)[1]


def check_macho(path):
    header = path.read_bytes()[:16]
    magic, cputype, subtype = struct.unpack_from('<III', header)
    if (magic, cputype, subtype & 0xffffff) != (0xfeedfacf, 0x100000c, 0):
        raise SystemExit('%s: expected an ordinary ARM64 Mach-O' % path)


def build(args, powervr):
    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
    library = vulkan_include = None
    if args.vulkan is not None:
        library = Path(args.vulkan or ROOT / 'build/mesa-pvr-ios/src/imagination/vulkan' / LIB_NAME).resolve()
        if not library.is_file():
            sys.exit('Build Mesa first: python3 scripts/build-pvr-darwin.py --target ios (or give --vulkan LIBRARY)')
        vulkan_include = (args.vulkan_include or ROOT / '.cache/mesa-pvr/include').resolve()
        if not (vulkan_include / 'vulkan/vulkan.h').is_file():
            sys.exit('No vulkan/vulkan.h in %s (give --vulkan-include)' % vulkan_include)
    cflags, sources = flags(powervr, vulkan_include)
    if args.tq_clip:
        # This constructor belongs only to the explicitly selected owner probe.
        # The shared Mesa library and all application defaults stay opt-in.
        comparison = out / 'powervrprobed-tq-clip.c'
        comparison.write_text(tq_clip_probe_source())
        sources.append(str(comparison))
    if args.tq_first:
        comparison = out / 'powervrprobed-tq-first.c'
        comparison.write_text(tq_first_probe_source())
        sources.append(str(comparison))
    if args.ui_fill:
        comparison = out / 'powervrprobed-ui-fill.c'
        comparison.write_text(ui_fill_probe_source())
        sources.append(str(comparison))
    if args.ui_batch:
        comparison = out / 'powervrprobed-ui-batch.c'
        comparison.write_text(ui_batch_probe_source())
        sources.append(str(comparison))
    if args.submit_barrier:
        comparison = out / 'powervrprobed-submit-barrier.c'
        comparison.write_text(submit_barrier_probe_source())
        sources.append(str(comparison))
    if args.serial_transfer:
        comparison = out / 'powervrprobed-serial-transfer.c'
        comparison.write_text(serial_transfer_probe_source())
        sources.append(str(comparison))
    if args.sipf1_encoding:
        comparison = out / 'powervrprobed-sipf1-encoding.c'
        comparison.write_text(sipf1_encoding_probe_source())
        sources.append(str(comparison))
    if args.sipf1_dump:
        comparison = out / 'powervrprobed-sipf1-dump.c'
        comparison.write_text(sipf1_dump_probe_source())
        sources.append(str(comparison))
    daemon = out / 'powervrprobed'
    subprocess.run(['xcrun', '--sdk', 'iphoneos', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-O2', '-g',
                    '-fstack-protector-strong', *cflags, *sources, '-o', str(daemon)], check=True)
    symbols = out / 'powervrprobed.dSYM'
    subprocess.run(['xcrun', 'dsymutil', str(daemon), '-o', str(symbols)], check=False)
    subprocess.run(['strip', '-x', str(daemon)], check=True)
    check_macho(daemon)
    entitlements = out / 'powervrprobed-entitlements.plist'
    entitlements.write_bytes(plistlib.dumps(ENTITLEMENTS))
    cdhashes = {'daemon_cdhash': sign(daemon, IDENTIFIER, entitlements)}
    actual = plistlib.loads(subprocess.check_output(['codesign', '-d', '--entitlements', ':-', str(daemon)], stderr=subprocess.DEVNULL))
    if actual != ENTITLEMENTS:
        sys.exit('The signed entitlement set differs')
    if library:
        (out / 'lib').mkdir()
        staged = out / 'lib' / LIB_NAME
        shutil.copyfile(library, staged)
        subprocess.run(['strip', '-x', str(staged)], check=True)
        check_macho(staged)
        cdhashes['vulkan_cdhash'] = sign(staged, LIB_IDENTIFIER)
    (out / ('%s.plist' % LABEL)).write_bytes(job_plist())
    (out / 'trust.json').write_text(json.dumps(dict(cdhashes, vulkan=bool(library), daemon_bytes=daemon.stat().st_size,
                                                    purpose='owner-built PowerVR probe; trusted only through an extra owner module'),
                                               indent=2) + '\n')
    print('PASS: powervrprobed built and 4K-signed:', json.dumps(cdhashes))
    return out, list(cdhashes.values())


def write_trust(args, cdhashes):
    original = args.trust_base.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    board = args.board or ROOT / 'board.toml'
    if not board.is_file():
        sys.exit('board.toml not found (give --board) to check the base trust bundle')
    if digest not in pinned_hashes(board):
        sys.exit('%s (%s) is not one of board.toml [payload] trust_cache_sha256' % (args.trust_base, digest))
    bundle = owner_bundle(original, cdhashes)
    args.trust_out.write_bytes(bundle)
    print('PASS: owner trust bundle %s sha256 %s (owner module + %d original modules)' % (
        args.trust_out, hashlib.sha256(bundle).hexdigest(), struct.unpack_from('<I', original)[0]))


def check(powervr):
    """Host check: both source configurations with the host clang (Darwin-only headers stubbed), the plists, the trust bundle and the
    addon logic. Builds and signs nothing."""
    clang = shutil.which('clang')
    if not clang:
        sys.exit('--check needs clang')
    with tempfile.TemporaryDirectory(prefix='powervrprobed-check-') as temp:
        stub = Path(temp)
        (stub / 'mach').mkdir()
        (stub / 'sys').mkdir()
        # <mach/mach.h>: the two names the probe uses. <sys/ioccom.h>: drm.h's BSD branch (its ioctl macros are not expanded).
        (stub / 'mach/mach.h').write_text('typedef unsigned int mach_port_t;\nmach_port_t mach_task_self(void);\n')
        (stub / 'sys/ioccom.h').write_text('')
        include = ROOT / '.cache/mesa-pvr/include'
        configurations = [None] + ([include] if (include / 'vulkan/vulkan.h').is_file() else [])
        for vulkan_include in configurations:
            cflags, sources = flags(powervr, vulkan_include)
            # -U__linux__: drm.h then takes the branch the Darwin build takes.
            for source in sources:
                subprocess.run([clang, '-fsyntax-only', '-U__linux__', '-I', str(stub), *cflags, source], check=True)
            print('PASS: syntax %s (%s)' % (', '.join(Path(s).name for s in sources),
                                            'POWERVRPROBED_VULKAN=1' if vulkan_include else 'IOKit only'))
        if len(configurations) == 1:
            print('NOTE: no Mesa headers at %s; the Vulkan stage was not checked' % include)
    job = plistlib.loads(job_plist())
    assert job['ProgramArguments'] == [DEVICE_BIN] and job['Label'] == LABEL
    assert plistlib.loads(plistlib.dumps(ENTITLEMENTS)) == ENTITLEMENTS
    print('PASS: job plist', LABEL, '->', DEVICE_BIN, 'and entitlements', sorted(ENTITLEMENTS))
    # A synthetic base bundle with two modules shaped like the original ones.
    def module(seed, n):
        hashes = sorted(hashlib.sha256(bytes([seed, i])).digest()[:20] for i in range(n))
        return struct.pack('<I16sI', 2, bytes(16), n) + b''.join(h + bytes([2, 0, 1, 0]) for h in hashes)
    m1, m2 = module(1, 3), module(2, 5)
    base = struct.pack('<3I', 2, 12, 12 + len(m1)) + m1 + m2
    bundle = owner_bundle(base, [hashlib.sha1(b'daemon').hexdigest(), hashlib.sha1(b'lib').hexdigest()])
    assert struct.unpack_from('<I', bundle)[0] == 3 and bundle.endswith(m1 + m2)
    print('PASS: owner trust bundle logic (loader checks, originals byte for byte)')
    files = [('bin/powervrprobed', 0o755, b'\xcf\xfa\xed\xfe' + os.urandom(3000)), ('daemons/%s.plist' % LABEL, 0o644, job_plist())]
    assert unpack_addon(pack_addon(files)) == files
    with tempfile.TemporaryDirectory(prefix='powervr-addon-path-') as temp:
        staged = Path(temp)
        (staged / 'lib').mkdir()
        (staged / 'powervrprobed').write_bytes(b'probe')
        signed_library = b'final signed library bytes'
        (staged / 'lib' / LIB_NAME).write_bytes(signed_library)
        entries = unpack_addon(pack_addon(addon_files(staged, False)))
        assert len(entries) == 3
        entries = {name: (mode, data) for name, mode, data in entries}
        assert entries['lib/' + LIB_NAME] == (0o755, signed_library)
        assert entries['addons/NativePowerVRVulkan.bundle/' + LIB_NAME] == (0o755, signed_library)
    print('PASS: GPU addon logic (NGPUADD2, the kernel\'s checks)')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tq-first', action='store_true', help='owner Vulkan probe only: run TQ clear/copy before render tests')
    parser.add_argument('--tq-clip', action='store_true', help='owner Vulkan probe only: opt in to the MT8167 TQ clip comparison')
    parser.add_argument('--check', action='store_true', help='host check only (syntax, plist, trust and addon logic)')
    parser.add_argument('--out', type=Path, default=ROOT / 'build/a04br3-powervr-probe')
    parser.add_argument('--vulkan', nargs='?', const='', default=None, metavar='LIBRARY',
                        help='build the Mesa stage and stage LIBRARY (default: build/mesa-pvr-ios/.../%s)' % LIB_NAME)
    parser.add_argument('--vulkan-include', type=Path, help='directory with vulkan/vulkan.h (default: .cache/mesa-pvr/include)')
    parser.add_argument('--trust-base', type=Path, help='original trust bundle (build-a04br3-trust-cache.py --out)')
    parser.add_argument('--trust-out', type=Path, help='write the owner trust bundle here')
    parser.add_argument('--board', type=Path, help='board.toml with [payload] trust_cache_sha256 (default: ROOT/board.toml)')
    parser.add_argument('--addon', type=Path, help='write a GPU addon (make-gpu-addon.py format) here')
    parser.add_argument('--addon-launchd-job', action='store_true', help='put the launchd job plist into the addon too')
    parser.add_argument('--extra-trust', type=Path, action='append', default=[], metavar='TRUST_JSON',
                        help='also trust the CDHashes of another owner build (its trust.json, every "*cdhash" value), for example '
                             'build-a04br3-metal-plugin.py --out DIR/trust.json; one owner module carries them all')
    parser.add_argument('--submit-barrier', action='store_true', help='enable diagnostic ARM64 store barrier immediately before job submission')
    parser.add_argument('--serial-transfer', action='store_true', help='wait transfer fences synchronously for overlap comparison')
    parser.add_argument('--sipf1-encoding', action='store_true', help='enable SIPF1 encoding without forcing TQ Clip routing')
    parser.add_argument('--sipf1-dump', action='store_true', help='enable existing bounded SIPF1 snapshots in this owner probe')
    parser.add_argument('--ui-batch', action='store_true', help='four-mip UI-shaped clear/copy batch; skips original tests')
    parser.add_argument('--ui-fill', action='store_true', help='388x12 optimal BGRA clear/copy comparison; skips original tests')
    args = parser.parse_args()
    if args.ui_batch and args.vulkan is None:
        parser.error('--ui-batch requires --vulkan')
    if args.submit_barrier and args.vulkan is None:
        parser.error('--submit-barrier requires --vulkan')
    if args.serial_transfer and args.vulkan is None:
        parser.error('--serial-transfer requires --vulkan')
    if args.sipf1_encoding and args.vulkan is None:
        parser.error('--sipf1-encoding requires --vulkan')
    if args.sipf1_dump and args.vulkan is None:
        parser.error('--sipf1-dump requires --vulkan')
    if args.ui_fill and args.vulkan is None:
        parser.error('--ui-fill requires --vulkan')
    if args.tq_first and args.vulkan is None:
        parser.error('--tq-first requires --vulkan')
    if args.tq_clip and args.vulkan is None:
        parser.error('--tq-clip requires --vulkan')
    powervr = powervr_dir()
    if args.check:
        check(powervr)
        return
    if bool(args.trust_base) != bool(args.trust_out):
        sys.exit('--trust-base and --trust-out go together')
    if sys.platform != 'darwin':
        sys.exit('The build needs macOS with Xcode (xcrun, codesign); --check runs anywhere')
    out, cdhashes = build(args, powervr)
    for extra in args.extra_trust:
        values = json.loads(extra.read_text())
        cdhashes += [v for k, v in sorted(values.items()) if k.endswith('cdhash') and isinstance(v, str)]
    # The Metal plugin carries the same signed Mesa library as the probe (same bytes and identifier): one entry.
    cdhashes = sorted(set(cdhashes))
    if args.trust_out:
        write_trust(args, cdhashes)
    if args.addon:
        blob = pack_addon(addon_files(out, args.addon_launchd_job))
        unpack_addon(blob)
        args.addon.write_bytes(blob)
        print('PASS: GPU addon %s (%d bytes)' % (args.addon, len(blob)))


if __name__ == '__main__':
    main()

