#!/usr/bin/env python3
"""Compile the a04br3 board archive (kits/a04br3: the package/ of UniXNU-a04br3) against the source-kernel ABI.

The archive defines unixnu_board_init(), which the kernel calls with CONFIG_XNU_BOARD_INIT=y (patch
native-board-init-hook), and unixnu_board_launch(), which a kernel with CONFIG_XNU_BOARD_LAUNCH=y calls before launchd
(upstream/UniXNU/0001-native-board-launch-hook.patch); it is linked with CONFIG_XNU_EXTRA_LD_ARCHIVES.

  C++ (the kernel's record for IOPlatformExpert.cpp, as in build-arm-driver.py): A04BR3Board.cpp, A04BR3AutoReset.cpp and
      KeyStoreProbe.cpp (UniXNU-tb128fu, the AppleKeyStore stand-in probe).
  C (the kernel's record for kern_exec.c, as UniXNU-tb128fu scripts/build-boot-progress.py builds it): BootWatchdogPolicy.c
      and A04BR3ProbeLaunch.c (the start of the PowerVR user-space probe from the launch hook).
  NativeKeyStoreEvents.o (UniXNU drivers/NativeKeyStoreEvents, built by "build-arm-driver.py NativeKeyStoreEvents" before
      this script in board.toml): its static initializer is unregistered and made global, as UniXNU-tb128fu
      scripts/build-native-legacy-display.py does, so KeyStoreProbe.cpp runs it between OSMetaClass::preModLoad and
      postModLoad. The instructions are checked to be unchanged.
"""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / 'kits/a04br3'
CXX_SOURCES = ['A04BR3Board.cpp', 'A04BR3AutoReset.cpp', 'KeyStoreProbe.cpp', 'A04BR3Gpu.cpp']
C_SOURCES = ['BootWatchdogPolicy.c', 'A04BR3ProbeLaunch.c']
OBJ = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
out = ROOT / 'build/a04br3-board'
out.mkdir(parents=True, exist_ok=True)


def record(relative):
    return json.loads((OBJ / relative).read_text().strip().rstrip(','))


def command_for(rec, source, destination):
    arguments = rec['arguments']
    command = []
    index = 0
    while index < len(arguments):
        value = arguments[index]
        if value == rec['file']:
            command.append(str(source))
        elif value in ('-o', '-MF', '-MT', '-MJ'):
            if value == '-o':
                command += ['-o', str(destination)]
            index += 1
        elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
            index += 1
        elif not value.startswith('-W'):
            command.append(value)
        index += 1
    return command


def compile_one(rec, source, extra):
    destination = out / (source.stem + '.o')
    command = command_for(rec, source, destination) + extra
    (out / (destination.stem + '.command.json')).write_text(json.dumps(command, indent=2) + '\n')
    result = subprocess.run(command, cwd=rec['directory'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (out / (destination.stem + '.log')).write_text(result.stdout)
    if result.returncode:
        raise SystemExit(result.stdout[-3500:])
    check_object(destination)
    return destination


def check_object(path):
    magic, cpu, subtype, kind = struct.unpack_from('<4I', path.read_bytes())
    if (magic, cpu, subtype, kind) != (0xfeedfacf, 0x100000c, 0, 1):
        raise SystemExit(f'{path}: unexpected object format')


def text_section(path):
    sys.path.insert(0, str(ROOT / 'kits/NativePrelink'))
    from native_prelink import macho
    data = path.read_bytes()
    section = next(s for g in macho(data)['segments'] for s in g['sections'] if s['name'] == '__text')
    return data[section['fileoff']:section['fileoff'] + section['size']]


def llvm_objcopy():
    try:
        prefix = subprocess.run(['brew', '--prefix', 'llvm'], check=True, capture_output=True, text=True).stdout.strip()
        candidate = Path(prefix) / 'bin/llvm-objcopy'
        if candidate.is_file():
            return str(candidate)
    except (OSError, subprocess.CalledProcessError):
        pass
    found = shutil.which('llvm-objcopy')
    if not found:
        raise SystemExit('llvm-objcopy not found (brew install llvm)')
    return found


# The warning set of build-arm-driver.py: the XNU IOKit headers need these exceptions under -Werror.
CXX_FLAGS = ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-c99-designator', '-Wno-reorder-init-list',
             '-Wno-deprecated-enum-enum-conversion', '-Wno-unnecessary-virtual-specifier', '-I', str(ROOT / '.cache/xnu-work/osfmk')]
C_FLAGS = ['-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror']

objects = []
cxx_record = record('iokit/DEVELOPMENT/IOPlatformExpert.cpo.json')
for name in CXX_SOURCES:
    objects.append(compile_one(cxx_record, KIT / name, CXX_FLAGS))
c_record = record('bsd/DEVELOPMENT/kern_exec.o.json')
for name in C_SOURCES:
    objects.append(compile_one(c_record, KIT / name, C_FLAGS))
# NativePowerVR (package/powervr): the unchanged Linux drm/imagination headers through the Linux type shim, and the
# upstream PowerVR firmware for the GE8300, downloaded from imagination/linux-firmware and checked against a pinned SHA-256
# (redistributable, not stored in this repository), embedded with .incbin.
import hashlib, urllib.request
import os
FW_URL = 'https://gitlab.freedesktop.org/imagination/linux-firmware/-/raw/powervr/powervr/rogue_22.67.54.30_v1.fw'
# A local copy with the same SHA-256 may be given instead (offline builds): A04BR3_POWERVR_FW=<path>.
if os.environ.get('A04BR3_POWERVR_FW'):
    (out / 'rogue_22.67.54.30_v1.fw').write_bytes(Path(os.environ['A04BR3_POWERVR_FW']).read_bytes())
FW_SHA256 = 'cfe6f58db2ad604b1729349629510c5a3434b266c99ea886d2959887bff4fed8'
fw_path = out / 'rogue_22.67.54.30_v1.fw'
if not fw_path.is_file() or hashlib.sha256(fw_path.read_bytes()).hexdigest() != FW_SHA256:
    fw_path.write_bytes(urllib.request.urlopen(FW_URL, timeout=120).read())
if hashlib.sha256(fw_path.read_bytes()).hexdigest() != FW_SHA256:
    raise SystemExit(f'{FW_URL}: unexpected SHA-256')
fw_asm = out / 'native_powervr_fw.S'
fw_asm.write_text(f'''.section __DATA_CONST,__const
.globl _native_powervr_fw
.p2align 12
_native_powervr_fw:
.incbin "{str(fw_path).replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))}"
.globl _native_powervr_fw_size
.p2align 2
_native_powervr_fw_size:
.long {fw_path.stat().st_size}
''')
fw_obj = out / 'native_powervr_fw.o'
subprocess.run(['xcrun', '--sdk', 'iphoneos', 'clang', '-arch', 'arm64', '-c', str(fw_asm), '-o', str(fw_obj)], check=True)
check_object(fw_obj)
objects.append(fw_obj)
powervr_flags = CXX_FLAGS + ['-I', str(KIT / 'powervr' / 'compat'), '-I', str(KIT / 'powervr' / 'linux'), '-Wno-missing-field-initializers',
                             '-Wno-gnu-variable-sized-type-not-at-end', '-Wno-c99-extensions']
powervr_flags += ['-I', str(KIT / 'powervr')]
objects.append(compile_one(cxx_record, KIT / 'powervr' / 'NativePowerVR.cpp', powervr_flags))
objects.append(compile_one(cxx_record, KIT / 'powervr' / 'NativePowerVRQueue.cpp', powervr_flags))
# The IOKit service and user client (NativePowerVRGpu.cpp: the NativePowerVRABI.h methods, BOs, VM contexts, device
# queries). Its classes derive from kernel classes, so the kernel's own C++ start-up registers them (the archive is
# force-loaded); native_powervr_publish() publishes the service once the firmware runs.
objects.append(compile_one(cxx_record, KIT / 'powervr' / 'NativePowerVRGpu.cpp', powervr_flags))
# The job layer (NativePowerVRJobs.cpp: native_powervr_jobs.h, Linux pvr_queue.c/pvr_job.c without drm_sched) includes
# the headers of the unchanged kernel/ sources it drives and the pvr_device stand-in (stub/).
objects.append(compile_one(cxx_record, KIT / 'powervr' / 'NativePowerVRJobs.cpp',
                           powervr_flags + ['-I', str(KIT / 'powervr' / 'stub'), '-I', str(KIT / 'powervr' / 'kernel')]))
# The unchanged Linux kernel sources (package/powervr/kernel: pvr_mmu.c and the job layer's pvr_context.c, pvr_cccb.c,
# pvr_free_list.c, pvr_hwrt.c, pvr_stream.c, pvr_stream_defs.c, pvr_dump.c) as C with the kern_exec.c record, through the
# Linux API shim (compat/native_powervr_linux_kernel.h, _jobs.h) and the pvr_device stand-in (stub/). Their own warnings that the
# kernel's -Werror set rejects (signed/unsigned comparisons, packed member addresses, C23 static_assert) are not changed
# in the sources, only disabled for them.
powervr_c_flags = C_FLAGS + ['-Wno-unknown-warning-option', '-Wno-sign-compare', '-Wno-address-of-packed-member',
                             '-Wno-c23-extensions', '-Wno-c2x-extensions', '-Wno-unused-parameter',
                             '-I', str(KIT / 'powervr' / 'stub'), '-I', str(KIT / 'powervr' / 'compat'),
                             '-I', str(KIT / 'powervr' / 'linux'), '-I', str(KIT / 'powervr' / 'kernel')]
for name in ('pvr_mmu.c', 'pvr_context.c', 'pvr_cccb.c', 'pvr_free_list.c', 'pvr_hwrt.c', 'pvr_stream.c', 'pvr_stream_defs.c',
             'pvr_dump.c'):
    objects.append(compile_one(c_record, KIT / 'powervr' / 'kernel' / name, powervr_c_flags))
# pvr_device_info.c (unchanged) fills the device information of NativePowerVRGpu.cpp from the firmware image; its
# struct pvr_device is the stand-in of stub/devinfo, found before stub/ on the include path. Compiled once: the job layer
# reads the result through native_powervr_device_features()/_quirks()/_enhancements() (native_powervr_jobs.h).
objects.append(compile_one(c_record, KIT / 'powervr' / 'kernel' / 'pvr_device_info.c',
                           C_FLAGS + powervr_c_flags[len(C_FLAGS):powervr_c_flags.index('-I')] +
                           ['-I', str(KIT / 'powervr' / 'stub' / 'devinfo')] + powervr_c_flags[powervr_c_flags.index('-I'):]))
# UniXNU-tb128fu's process pass (BootProcessProbe.c) needs the Mach-private task and thread layout: the osfmk record, as
# tb128fu's build-boot-progress.py compiles it. Its rows go to UART through a04br3-proclog (A04BR3Board.cpp).
osfmk_record = record('osfmk/DEVELOPMENT/startup.o.json')
for name in ('BootProcessProbe.c', 'BootUiTraceProbe.c'):
    objects.append(compile_one(osfmk_record, KIT / name, C_FLAGS))

events = ROOT / 'build/native-keystore-events/NativeKeyStoreEvents.o'
if not events.is_file():
    raise SystemExit(f'Missing {events}: board.toml must run "build-arm-driver.py NativeKeyStoreEvents" before this script')
events_late = out / 'NativeKeyStoreEvents.late.o'
subprocess.run([llvm_objcopy(), '--remove-section=__DATA,__mod_init_func',
                '--globalize-symbol=__GLOBAL__sub_I_NativeKeyStoreEvents.cpp', str(events), str(events_late)], check=True)
if text_section(events) != text_section(events_late):
    raise SystemExit('NativeKeyStoreEvents instructions changed by the late initializer staging')
objects.append(events_late)

archive = out / 'libA04BR3Board.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, *objects], check=True)
print(f'a04br3 board archive: {archive} ({", ".join(o.name for o in objects)})')
