#!/usr/bin/env python3
"""隔離コピーの生成済みリンクだけをコピー先の対応物へ移す。"""
import argparse
import json
import os
from pathlib import Path
import uuid


def rebase(original: Path, copied: Path, apply: bool):
    original, copied = original.resolve(strict=True), copied.resolve(strict=True)
    if original == copied or original.is_relative_to(copied) or copied.is_relative_to(original):
        raise ValueError('source and copy must be separate directories')
    out = copied / 'out'
    if out.is_symlink() or not out.is_dir():
        raise ValueError('copy out must be a real directory')
    changes = []
    for parent, dirs, files in os.walk(copied, followlinks=False):
        for name in dirs + files:
            link = Path(parent) / name
            if not link.is_symlink():
                continue
            target = link.resolve(strict=False)
            if target.is_relative_to(copied):
                continue
            if not link.is_relative_to(out) or not target.is_relative_to(original):
                raise ValueError(f'unexpected external symlink: {link.relative_to(copied)}')
            mapped = copied / target.relative_to(original)
            if not mapped.exists() or not mapped.resolve().is_relative_to(copied):
                raise ValueError(f'mapped target missing or external: {link.relative_to(copied)}')
            st = link.lstat()
            changes.append((link, mapped, st.st_dev, st.st_ino, os.readlink(link)))
    if apply:
        for link, mapped, dev, ino, old_target in changes:
            st = link.lstat()
            if not link.is_symlink() or (st.st_dev, st.st_ino) != (dev, ino) or os.readlink(link) != old_target:
                raise ValueError(f'link changed during validation: {link.relative_to(copied)}')
            temporary = link.with_name(f'.{link.name}.rebase-{uuid.uuid4().hex}')
            try:
                os.symlink(mapped, temporary)
                os.replace(temporary, link)
            finally:
                if temporary.is_symlink():
                    temporary.unlink()
    return [str(link.relative_to(copied)) for link, *_ in changes]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--copy', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps({'applied': args.apply, 'links': rebase(args.original, args.copy, args.apply)}, ensure_ascii=False))
