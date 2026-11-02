#!/usr/bin/env python3
"""Build the MT8167 MSDC eMMC driver of the a04br3 kernel (drivers/NativeMsdc, github.com/mouseos/NativeMsdc).

A [kernel] archives entry of board.toml. NativeMsdc is a community driver outside the UniXNU catalog, so this board
script compiles it the way UniXNU scripts/build-arm-driver.py compiles catalog drivers: with the kernel's own compile
record of IOPlatformExpert.cpp and the -Werror warning set, then the ARM64 object audit of scripts/kernel_target.py.

The configuration selects the variant (CONFIG_XNU_* of configs/a04br3_defconfig reach this script as XNU_*):
  XNU_NATIVE_MSDC_BLOCK=1  the userdata windows as IOBlockStorageDevice subclasses (NativeMsdcBlock), bound to the
                           original class by UniXNU scripts/build-virtio-mmio-bindings.py --object --block-class;
  --verified-root          every window block is verified against the owner-signed policy in userdata (VerifiedRoot);
  XNU_NATIVE_RAM_BLOCK=1   the persistent store is also exported under the names RamBlock imports.
The result is build/native-msdc/libNativeMsdc.a, linked through CONFIG_XNU_EXTRA_LD_ARCHIVES.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kernel_target import TRIPLE, check_object  # noqa: E402  (UniXNU scripts/kernel_target.py)

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument('--verified-root', action='store_true', help='verify every window block against the owner-signed policy')
args = parser.parse_args()

block_devices = os.environ.get('XNU_NATIVE_MSDC_BLOCK') == '1'
if args.verified_root and not block_devices:
    parser.error('--verified-root needs the window block devices (CONFIG_XNU_NATIVE_MSDC_BLOCK=y)')
# The driver is not a catalog component, so the build tree does not link it: it is read from the device checkout that
# "unixnu build kernel" passes as UNIXNU_DEVICE (drivers/NativeMsdc, a submodule), or from NATIVE_MSDC_DIR.
if not os.environ.get('NATIVE_MSDC_DIR') and not os.environ.get('UNIXNU_DEVICE'):
    raise SystemExit('Run from "unixnu build kernel" (UNIXNU_DEVICE) or set NATIVE_MSDC_DIR')
source = Path(os.environ.get('NATIVE_MSDC_DIR') or Path(os.environ['UNIXNU_DEVICE']) / 'drivers/NativeMsdc') / 'NativeMsdc.cpp'
if not source.is_file():
    raise SystemExit(f'{source} missing: run "git submodule update --init drivers/NativeMsdc" in the device repository')
out = ROOT / 'build/native-msdc'
out.mkdir(parents=True, exist_ok=True)
destination = out / 'NativeMsdc.o'
record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json')
                    .read_text().strip().rstrip(','))
arguments, command, index = record['arguments'], [], 0
while index < len(arguments):
    value = arguments[index]
    if value == record['file']:
        command.append(str(source))
    elif value in ('-o', '-MF', '-MT', '-MJ'):
        if value == '-o':
            command.extend(['-o', str(destination)])
        index += 1
    elif any(value.startswith(flag) and value != flag for flag in ('-MF', '-MT', '-MJ')):
        pass   # the borrowed compile record must not overwrite the kernel's dependency files
    elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
        index += 1
    elif not value.startswith('-W'):
        command.append(value)
    index += 1
command += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-c99-designator', '-Wno-reorder-init-list',
            '-Wno-deprecated-enum-enum-conversion', '-Wno-unnecessary-virtual-specifier',
            '-I', str(ROOT / '.cache/xnu-work/osfmk'),
            '-DNATIVE_MSDC_BLOCK_DEVICES=' + ('1' if block_devices else '0')]
if block_devices:
    command += ['-I', str(ROOT / 'build/storage-abi/include')]
if args.verified_root:
    command += ['-I', str(ROOT / 'kits/VerifiedRoot/include'), '-DNATIVE_VERIFIED_BLOCK=1']
if os.environ.get('XNU_NATIVE_RAM_BLOCK') == '1':
    command += ['-DNATIVE_MSDC_UFS_STORE_ABI=1']
for stale in ('bindings.json', 'original-bindings.S', 'original-bindings.o', 'libNativeMsdc.a'):
    (out / stale).unlink(missing_ok=True)
(out / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
result = subprocess.run(command, cwd=record['directory'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
(out / 'compile.log').write_text(result.stdout)
if result.returncode:
    raise SystemExit(result.stdout[-3500:])
check_object(destination.read_bytes(), 'NativeMsdc')
if block_devices:
    # Binds NativeMsdcBlock to the original IOBlockStorageDevice and writes libNativeMsdc.a with the bindings.
    subprocess.run([sys.executable, str(ROOT / 'scripts/build-virtio-mmio-bindings.py'), '--object', 'NativeMsdc',
                    '--block-class', 'NativeMsdcBlock', '--directory', str(out)], check=True)
else:
    subprocess.run(['xcrun', 'ar', 'rcs', out / 'libNativeMsdc.a', destination], check=True)
print(f'NativeMsdc: -Werror build and {TRIPLE} object audit passed')

