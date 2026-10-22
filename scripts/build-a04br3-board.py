#!/usr/bin/env python3
"""Compile the a04br3 board archive (kits/a04br3: the package/ of UniXNU-a04br3) against the source-kernel ABI.

The archive defines unixnu_board_init(), which the kernel calls with CONFIG_XNU_BOARD_INIT=y (patch
native-board-init-hook), and is linked with CONFIG_XNU_EXTRA_LD_ARCHIVES. The compile command is the kernel's own
record for IOPlatformExpert.cpp, with the source and output replaced, as in build-arm-driver.py.
"""
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ['A04BR3AutoReset.cpp']
recipe = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
record = json.loads(recipe.read_text().strip().rstrip(','))
out = ROOT / 'build/a04br3-board'
out.mkdir(parents=True, exist_ok=True)
objects = []
for name in SOURCES:
    source = ROOT / 'kits/a04br3' / name
    destination = out / (Path(name).stem + '.o')
    arguments = record['arguments']
    command = []
    index = 0
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(source))
        elif value == '-o':
            command += ['-o', str(destination)]
            index += 1
        elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
            index += 1
        elif not value.startswith('-W'):
            command.append(value)
        index += 1
    # The warning set of build-arm-driver.py: the XNU IOKit headers need these exceptions under -Werror.
    command += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                '-Wno-c99-designator', '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion',
                '-Wno-unnecessary-virtual-specifier', '-I', str(ROOT / '.cache/xnu-work/osfmk')]
    (out / (destination.stem + '.command.json')).write_text(json.dumps(command, indent=2) + '\n')
    result = subprocess.run(command, cwd=record['directory'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (out / (destination.stem + '.log')).write_text(result.stdout)
    if result.returncode:
        raise SystemExit(result.stdout[-3500:])
    magic, cpu, subtype, kind = struct.unpack_from('<4I', destination.read_bytes())
    if (magic, cpu, subtype, kind) != (0xfeedfacf, 0x100000c, 0, 1):
        raise SystemExit(f'{destination}: unexpected object format')
    objects.append(destination)
archive = out / 'libA04BR3Board.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, *objects], check=True)
print(f'a04br3 board archive: {archive}')
