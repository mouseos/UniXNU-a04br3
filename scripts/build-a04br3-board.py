#!/usr/bin/env python3
"""Compile the a04br3 board archive (kits/a04br3: the package/ of UniXNU-a04br3) against the source-kernel ABI.

The archive defines unixnu_board_init(), which the kernel calls with CONFIG_XNU_BOARD_INIT=y (patch
native-board-init-hook), and unixnu_board_launch(), which a kernel with CONFIG_XNU_BOARD_LAUNCH=y calls before launchd
(upstream/UniXNU/0001-native-board-launch-hook.patch); it is linked with CONFIG_XNU_EXTRA_LD_ARCHIVES.

  C++ (the kernel's record for IOPlatformExpert.cpp, as in build-arm-driver.py): A04BR3Board.cpp, A04BR3AutoReset.cpp and
      KeyStoreProbe.cpp (UniXNU-tb128fu, the AppleKeyStore stand-in probe).
  C (the kernel's record for kern_exec.c, as UniXNU-tb128fu scripts/build-boot-progress.py builds it): BootWatchdogPolicy.c.
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
CXX_SOURCES = ['A04BR3Board.cpp', 'A04BR3AutoReset.cpp', 'KeyStoreProbe.cpp']
C_SOURCES = ['BootWatchdogPolicy.c']
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
