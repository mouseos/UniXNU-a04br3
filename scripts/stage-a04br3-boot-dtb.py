#!/usr/bin/env python3
"""Stage the device tree that the stock A04BR3 LK passes on into the build tree, for the boot image.

A [firmware] preflight entry of board.toml (UniXNU runs it with UNIXNU_UEFI = the uefi-a04br3 checkout, cwd = the build
tree). The boot image appends this DTB after the shim and firmware volume ([bootimg] dtb = "build/a04br3-boot/a04br3.dtb");
it is the uefi-a04br3 Resources/DTBs/a04br3.dtb of every hardware-verified a04br3 image (SHA-256 below). The XNU device
tree is a separate file, generated from dts/a04br3.dts.
"""
import hashlib
import os
from pathlib import Path
import shutil

EXPECTED_SHA256 = '07cef7be51fc3cbf73a19c5c5d26c5676d9bf85743287a2c195917138731a81c'
uefi = os.environ.get('UNIXNU_UEFI')
if not uefi:
    raise SystemExit('UNIXNU_UEFI is not set: run through "unixnu build firmware --uefi DIR"')
source = Path(uefi) / 'Resources/DTBs/a04br3.dtb'
if not source.is_file():
    raise SystemExit(f'{source} missing: --uefi must be the uefi-a04br3 checkout')
digest = hashlib.sha256(source.read_bytes()).hexdigest()
if digest != EXPECTED_SHA256:
    raise SystemExit(f'{source}: SHA-256 {digest}, expected {EXPECTED_SHA256}')
target = Path.cwd() / 'build/a04br3-boot/a04br3.dtb'
target.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(source, target)
print(f'boot DTB {target} sha256 {digest}')

