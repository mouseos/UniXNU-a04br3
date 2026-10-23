#!/usr/bin/env python3
"""Regenerate patches/mesa/pvr-darwin-winsys.patch from the working Mesa tree (.cache/mesa-pvr, which
build-pvr-darwin.py fetches at the pinned commit).

The patch is the whole difference between the pinned commit and the working tree, new files included. Edit the Mesa
tree, run this, then run build-pvr-darwin.py --check-patch to prove the tree equals the recorded patch."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MESA = ROOT / '.cache/mesa-pvr'
PINS = json.loads((ROOT / 'scripts/data/pvr-mesa-inputs.json').read_text())
OUT = ROOT / 'patches/mesa/pvr-darwin-winsys.patch'


def git(*args):
    return subprocess.run(['git', '-C', str(MESA), *args], check=True, capture_output=True, text=True).stdout


if git('rev-parse', 'HEAD').strip() != PINS['mesa']['commit']:
    sys.exit('Mesa work tree is not at the pinned commit')
new = [f for f in git('ls-files', '--others', '--exclude-standard').splitlines() if f]
if new:
    git('add', '-N', '--', *new)
try:
    patch = git('diff', 'HEAD', '--binary')
finally:
    if new:
        git('reset', '-q', '--', *new)
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(patch)
print('wrote', OUT.relative_to(ROOT), len(patch.splitlines()), 'lines')
