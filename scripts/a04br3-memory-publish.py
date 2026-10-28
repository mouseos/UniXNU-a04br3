#!/usr/bin/env python3
"""Stage and atomically publish a validated memory diagnostic payload."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def stage(run, payload):
    report = json.loads((payload/'payload.json').read_text())
    if report['kernel_sha256'] != sha(run/'kernel.macho'):
        raise ValueError('payload kernel does not match staged kernel')
    if report['trust_cache_sha256'] != sha(run/'trustcache'):
        raise ValueError('payload trust does not match staged trust')
    image = payload/'payload.img'
    if report['payload_sha256'] != sha(image) or report['payload_bytes'] != image.stat().st_size:
        raise ValueError('payload image does not match its report')
    shutil.copyfile(image, run/'payload.img')
    shutil.copyfile(payload/'payload.json', run/'payload.json')
    if sha(run/'payload.img') != report['payload_sha256']:
        raise ValueError('payload changed during staging')


def publish(root, run):
    root, run = root.resolve(), run.resolve()
    if run.parent != root:
        raise ValueError('run is not inside the output root')
    if not (run/'.retain-after-publication-attempt').is_file():
        raise ValueError('run is not protected from cleanup')
    for name in ('payload.img', 'payload.json', 'kernel.macho', 'trustcache', 'data.dbr', 'a04br3-memory-inputs.json'):
        if not (run/name).is_file():
            raise ValueError('missing run artifact: ' + name)
    temporary = root / ('.current-' + uuid.uuid4().hex)
    try:
        temporary.symlink_to(run.name, target_is_directory=True)
        os.replace(temporary, root/'current')
    finally:
        temporary.unlink(missing_ok=True)


def main():
    action, root, run = sys.argv[1:4]
    if action == 'stage' and len(sys.argv) == 5:
        stage(Path(run), Path(sys.argv[4]))
    elif action == 'publish' and len(sys.argv) == 4:
        publish(Path(root), Path(run))
        print('memory build published:', str(Path(root)/'current'))
    else:
        raise ValueError('invalid publication action')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, IndexError) as error:
        sys.exit('memory publication: ' + str(error))
