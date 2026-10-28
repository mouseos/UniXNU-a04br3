#!/usr/bin/env python3
"""Reject a prelinked kernel containing the first-pass PMGR/display stubs."""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REQUIRED = {
    '__GLOBAL__sub_I_ShengPlatformPower.cpp',
    '__GLOBAL__sub_I_NativeLegacyDisplay.cpp',
    '__ZTV18ShengPlatformPower',
    '_sheng_original_pmgr_nub_create',
    '_native_legacy_display_probe',
}


def validate(path, nm):
    if not path.is_file():
        raise ValueError('kernel not found')
    result = subprocess.run([nm, str(path)], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('cannot read Mach-O symbols')
    defined = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-2].upper() not in {'U', 'N'}:
            defined.add(fields[-1])
    missing = REQUIRED - defined
    if missing:
        raise ValueError('missing required PMGR/display definitions: ' + ', '.join(sorted(missing)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kernel', type=Path, required=True)
    args = parser.parse_args()
    nm = shutil.which('llvm-nm')
    if not nm:
        parser.error('llvm-nm is required for kernel validation')
    try:
        validate(args.kernel, nm)
    except (OSError, ValueError) as error:
        print(f'kernel validation failed: {error}', file=sys.stderr)
        return 1
    print('kernel validation passed: PMGR and display definitions present')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
