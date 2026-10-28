#!/usr/bin/env python3
"""Exercise the actual UniXNU tree copier against same-size stale scripts."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
unixnu = Path(os.environ.get('UNIXNU_ROOT', Path.cwd())).resolve()
if not (unixnu/'scripts/unixnu/tree.py').is_file():
    raise SystemExit('Set UNIXNU_ROOT to the fixed UniXNU source checkout')
sys.path[:0] = [str(ROOT/'scripts'), str(unixnu/'scripts')]
from a04br3_tree_scripts import verify_tree_scripts
from unixnu.tree import _copy_scripts

with tempfile.TemporaryDirectory(prefix='memory-tree-script-') as tmp:
    root = Path(tmp)
    source, device, tree = root/'source', root/'device', root/'tree'
    for base in (source, device, tree): (base/'scripts').mkdir(parents=True)
    base = source/'scripts/build.py'
    override = device/'scripts/build.py'
    base.write_bytes(b'BASE')
    override.write_bytes(b'DEV!')
    now = time.time_ns()
    os.utime(base, ns=(now, now))
    os.utime(override, ns=(now-10_000_000_000, now-10_000_000_000))
    dest = tree/'scripts/build.py'
    _copy_scripts(source/'scripts', tree/'scripts')
    _copy_scripts(device/'scripts', tree/'scripts')
    assert dest.read_bytes() == b'BASE', 'actual tree copier did not reproduce same-size overlay defect'
    def reject():
        try:
            verify_tree_scripts(source, device, tree)
        except ValueError:
            return
        raise AssertionError('accepted modified or unexpected tree script')
    reject()
    shutil.copy2(override, dest)
    verify_tree_scripts(source, device, tree, stabilize=True)
    for _ in range(2):
        _copy_scripts(source/'scripts', tree/'scripts')
        _copy_scripts(device/'scripts', tree/'scripts')
        verify_tree_scripts(source, device, tree)
        assert dest.read_bytes() == b'DEV!'
    # Same size + newer mtime causes the real copier to preserve altered code.
    dest.write_bytes(b'EVIL')
    future = time.time_ns()+60_000_000_000
    os.utime(dest, ns=(future,future))
    _copy_scripts(source/'scripts', tree/'scripts')
    _copy_scripts(device/'scripts', tree/'scripts')
    assert dest.read_bytes() == b'EVIL'
    reject()
    shutil.copy2(override, dest)
    (tree/'scripts/stale.py').write_text('unexpected')
    reject()
    (tree/'scripts/stale.py').unlink()
    (tree/'scripts/__pycache__').mkdir()
    (tree/'scripts/__pycache__/build.pyc').write_bytes(b'stale bytecode')
    reject()
    shutil.rmtree(tree/'scripts/__pycache__')
    (tree/'scripts/stale-link').symlink_to(dest)
    reject()
    (tree/'scripts/stale-link').unlink()
    # Different-sized overlays may be recopied but must still end at device bytes.
    base.write_bytes(b'LONGER-BASE')
    shutil.copy2(override, dest)
    verify_tree_scripts(source, device, tree, stabilize=True)
    for _ in range(2):
        _copy_scripts(source/'scripts', tree/'scripts')
        _copy_scripts(device/'scripts', tree/'scripts')
        verify_tree_scripts(source, device, tree)
        assert dest.read_bytes() == b'DEV!'
print('PASS: actual UniXNU copier stale same-size and overlay, verified pin, extras and bytecode')
