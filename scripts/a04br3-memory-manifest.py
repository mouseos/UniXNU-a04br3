#!/usr/bin/env python3
"""Bind a completed diagnostic kernel to its seed, trust and build inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import uuid


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def git_output(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def repo_identity(repo, allow_tracked_changes=False):
    head = git_output(repo, 'rev-parse', 'HEAD').decode().strip()
    status = git_output(repo, 'status', '--porcelain', '--untracked-files=normal', '--ignore-submodules=none')
    if status.strip() and not allow_tracked_changes:
        raise ValueError('source checkout has unexpected changes: ' + str(repo))
    diff = git_output(repo, 'diff', '--binary', 'HEAD', '--', '.')
    sub = git_output(repo, 'submodule', 'status', '--recursive')
    if any(line[:1] in (b'-', b'+', b'U') for line in sub.splitlines()):
        raise ValueError('uninitialized, changed, or conflicted submodule in ' + str(repo))
    untracked = hashlib.sha256()
    if allow_tracked_changes:
        entries = git_output(repo, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0')
        for name in sorted(filter(None, entries)):
            path = repo / os.fsdecode(name)
            untracked.update(len(name).to_bytes(8, 'big'))
            untracked.update(name)
            if path.is_symlink():
                content = os.fsencode(os.readlink(path))
                untracked.update(b'L' + len(content).to_bytes(8, 'big') + content)
            elif path.is_file():
                untracked.update(b'F' + bytes.fromhex(digest(path)))
            else:
                raise ValueError('unsupported untracked XNU source: ' + str(path))
    return {'commit': head, 'tracked_diff_sha256': hashlib.sha256(diff).hexdigest(),
            'untracked_sha256': untracked.hexdigest(),
            'submodules_sha256': hashlib.sha256(sub).hexdigest()}


def snapshot():
    runner = Path(os.environ['RUNNER_TEMP']).resolve()
    device = Path(os.environ['DEVICE_DIR']).resolve()
    source = Path.cwd().resolve()
    out = Path(os.environ.get('UNIXNU_OUT', str(source / 'out'))).resolve()
    seed = json.loads((runner / 'owner-data/seed.json').read_text())
    data_uuid = str(uuid.UUID(seed['volume_uuid'])).upper()
    if data_uuid == str(uuid.UUID(int=0)).upper():
        raise ValueError('nil Data UUID')
    if os.environ.get('XNU_OWNER_DATA_VOLUME_UUID') != data_uuid:
        raise ValueError('kernel build environment does not match Data seed UUID')
    env_file = (runner / 'a04br3-data.env').read_text()
    if env_file != 'export XNU_OWNER_DATA_VOLUME_UUID=' + data_uuid + '\n':
        raise ValueError('generated Data environment does not match seed metadata')
    lock = tomllib.loads((device / 'unixnu.lock').read_text())
    pinned_xnu = lock['source']['xnu']['commit'].lower()
    tree = out / 'a04br3/tree'
    xnu = repo_identity(tree / 'refer/xnu')
    if xnu['commit'].lower() != pinned_xnu:
        raise ValueError('XNU checkout differs from device lock')
    patched_xnu = repo_identity(tree / '.cache/xnu-work', allow_tracked_changes=True)
    if patched_xnu['commit'].lower() != pinned_xnu:
        raise ValueError('patched XNU checkout differs from device lock')
    flags = {k: v for k, v in sorted(os.environ.items()) if k.startswith(('XNU_', 'CONFIG_', 'A04BR3_', 'UNIXNU_'))}
    return {'version': 1, 'source': repo_identity(source), 'device': repo_identity(device),
            'xnu_reference': xnu, 'xnu_patched': patched_xnu,
            'lock_sha256': digest(device / 'unixnu.lock'),
            'config_sha256': digest(out / 'a04br3/.config'),
            'flags_sha256': hashlib.sha256(json.dumps(flags, sort_keys=True).encode()).hexdigest(),
            'seed_uuid': data_uuid, 'data_sha256': digest(runner / 'data.dbr'),
            'trust_sha256': digest(runner / 'trustcache'),
            'kernel_path': str(out / 'a04br3/kernel/kernel.macho')}


def atomic_json(path, obj):
    tmp = path.with_name(path.name + '.tmp')
    try:
        tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('capture', 'seal', 'verify'))
    action = parser.parse_args().action
    runner = Path(os.environ['RUNNER_TEMP']).resolve()
    pending, complete = runner / 'a04br3-memory-pending.json', runner / 'a04br3-memory-inputs.json'
    if action == 'capture':
        complete.unlink(missing_ok=True)
        pending.unlink(missing_ok=True)
        atomic_json(pending, snapshot())
        return
    if action == 'seal':
        complete.unlink(missing_ok=True)
        before = json.loads(pending.read_text())
        if before != snapshot():
            raise ValueError('build inputs changed while kernel was being built')
        kernel = Path(before['kernel_path'])
        if before['seed_uuid'].encode('ascii') not in kernel.read_bytes():
            raise ValueError('built kernel does not contain the Data seed UUID')
        before['kernel_sha256'] = digest(kernel)
        atomic_json(complete, before)
        pending.unlink(missing_ok=True)
        return
    record = json.loads(complete.read_text())
    expected = dict(record)
    kernel_hash = expected.pop('kernel_sha256')
    if expected != snapshot() or digest(Path(expected['kernel_path'])) != kernel_hash:
        raise ValueError('memory payload inputs, build environment or kernel changed since completed build')
    if expected['seed_uuid'].encode('ascii') not in Path(expected['kernel_path']).read_bytes():
        raise ValueError('kernel does not contain the Data seed UUID')


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit('memory build manifest: ' + str(error))
