#!/usr/bin/env python3
"""Reject a UniXNU generated script tree that differs from its source overlay."""
import argparse
import hashlib
import os
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_tree_scripts(source, device, tree, stabilize=False):
    expected = {}
    sources = {}
    # Match tree.assemble: base first, device wins for the same relative name.
    for base in (source, device):
        root = base / 'scripts'
        if not root.is_dir():
            continue
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root)
            if path.is_dir() or '__pycache__' in path.parts or relative.parts[0] == 'unixnu':
                continue
            expected[relative] = path
            sources.setdefault(relative, []).append(path)
    target = tree / 'scripts'
    if not target.is_dir() or target.is_symlink():
        raise ValueError('missing or symlinked generated scripts directory')
    actual = {}
    for path in target.rglob('*'):
        relative = path.relative_to(target)
        if path.is_symlink():
            raise ValueError('generated script tree contains a symlink: ' + str(path))
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('generated script tree contains a non-regular file: ' + str(path))
        actual[relative] = path
    missing = set(expected) - set(actual)
    extra = set(actual) - set(expected)
    if missing or extra:
        sample = sorted(missing or extra)[0]
        raise ValueError(('missing' if missing else 'unexpected') + ' generated script: ' + str(sample))
    h = hashlib.sha256()
    for relative, src in sorted(expected.items()):
        src_hash = digest(src)
        if digest(actual[relative]) != src_hash:
            raise ValueError('generated script differs from source: ' + str(relative))
        key = relative.as_posix().encode('utf-8')
        h.update(len(key).to_bytes(8, 'big'))
        h.update(key)
        h.update(bytes.fromhex(src_hash))
    if stabilize:
        # tree.py uses size and mtime rather than comparing contents.  Set the
        # already-verified copy newer than both overlay sources: same-sized
        # base/device files then cannot replace the intended device version.
        for relative, path in actual.items():
            newest = max(src.stat().st_mtime_ns for src in sources[relative])
            current = path.stat()
            os.utime(path, ns=(current.st_atime_ns, max(current.st_mtime_ns, newest) + 2_000_000_000))
        # Detect a source or generated-file change during timestamp updates.
        return verify_tree_scripts(source, device, tree)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stabilize', action='store_true')
    parser.add_argument('source', type=Path)
    parser.add_argument('device', type=Path)
    parser.add_argument('tree', type=Path)
    args = parser.parse_args()
    try:
        print('verified generated scripts sha256=' + verify_tree_scripts(args.source, args.device, args.tree, stabilize=args.stabilize))
    except (ValueError, OSError) as error:
        parser.exit(1, 'script tree verification: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
