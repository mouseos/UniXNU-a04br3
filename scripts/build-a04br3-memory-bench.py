#!/usr/bin/env python3
"""Build and sign the optional benchmark; emit trust.json for the probe builder."""
import argparse
import json
import plistlib
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        sys.exit('iOS benchmark build requires macOS and Xcode')
    args.out.mkdir(parents=True, exist_ok=True)
    executable = args.out / 'a04br3-memory-copy-bench'
    sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
    subprocess.run(['xcrun', '--sdk', 'iphoneos', 'clang', '-target', 'arm64-apple-ios17.0',
                    '-isysroot', sdk, '-O2', '-fstack-protector-strong',
                    str(ROOT / 'tools/a04br3-memory-copy-bench.c'), '-o', str(executable)], check=True)
    subprocess.run(['strip', '-x', str(executable)], check=True)
    helper = runpy.run_path(str(ROOT / 'scripts/build-a04br3-powervr-probe.py'))
    helper['check_macho'](executable)
    entitlements = {'com.apple.private.security.no-container': True}
    entitlement_file = args.out / 'entitlements.plist'
    entitlement_file.write_bytes(plistlib.dumps(entitlements))
    cdhash = helper['sign'](executable, 'org.unixnu.a04br3.memory-copy-bench', entitlement_file)
    actual = plistlib.loads(subprocess.check_output(['codesign', '-d', '--entitlements', ':-', str(executable)], stderr=subprocess.DEVNULL))
    if actual != entitlements:
        sys.exit('signed benchmark entitlements differ')
    (args.out / 'trust.json').write_text(json.dumps({'memory_bench_cdhash': cdhash}, indent=2) + '\n')
    print('memory benchmark signed and verified; CDHash', cdhash)

if __name__ == '__main__':
    main()
