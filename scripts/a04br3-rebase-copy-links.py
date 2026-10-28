#!/usr/bin/env python3
"""他のプロセスが変更しない隔離コピー内の生成リンクを付け替える。"""
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
    def fail_walk(error):
        raise error
    for parent, dirs, files in os.walk(copied, followlinks=False, onerror=fail_walk):
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
            # 相対dir_fdとO_NOFOLLOWで、親経路がリンクに差し替わっても辿らない。
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(copied, flags)
            temporary = f'.{link.name}.rebase-{uuid.uuid4().hex}'
            created = False
            try:
                for component in link.parent.relative_to(copied).parts:
                    next_fd = os.open(component, flags, dir_fd=fd)
                    os.close(fd)
                    fd = next_fd
                st = os.stat(link.name, dir_fd=fd, follow_symlinks=False)
                if (st.st_dev, st.st_ino) != (dev, ino) or os.readlink(link.name, dir_fd=fd) != old_target:
                    raise ValueError(f'link changed during validation: {link.relative_to(copied)}')
                if not mapped.exists() or not mapped.resolve().is_relative_to(copied):
                    raise ValueError(f'mapped target changed: {link.relative_to(copied)}')
                os.symlink(mapped, temporary, dir_fd=fd)
                created = True
                os.replace(temporary, link.name, src_dir_fd=fd, dst_dir_fd=fd)
                created = False
            finally:
                if created:
                    os.unlink(temporary, dir_fd=fd)
                os.close(fd)
    return [str(link.relative_to(copied)) for link, *_ in changes]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--copy', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps({'applied': args.apply, 'links': rebase(args.original, args.copy, args.apply)}, ensure_ascii=False))
