#!/usr/bin/env python3
"""Prepare independent fixed-source SDK qualification; never run the build."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess

PINS = {
    'UniXNU': '9121ed78a2bb3d3f1385b2e6c63256dd37ebe62e',
    'UniXNU-a04br3': 'ef48e0f88820d7be221c711322ad48f3fc8473ad',
    'NativeMsdc': '2beb6ac980824c2f461317a963aca17701bc1362',
}
CACHE_SHA = 'f18b4df865405f2a287b84977d7aca745e056d656d9fc9df1662970ce2969de7'
REQUIRED = ('.xnu-10063.141.1-headers', 'usr/local/libexec/availability.pl',
            'usr/local/lib/kernel/libfirehose_kernel.a',
            'System/Library/Frameworks/Kernel.framework/Headers',
            'System/Library/Frameworks/Kernel.framework/PrivateHeaders')

def run(*args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1')
    return subprocess.check_output(args, text=True, env=env).rstrip('\n')

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def validate(destination, sdk, cache):
    # Do not resolve away destination symlinks before checking them.
    destination = destination.absolute()
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError('destination has a symlink component')
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError('destination must be absent with an existing parent')
    for source in (sdk.resolve(), cache.resolve()):
        if destination == source or destination in source.parents or source in destination.parents:
            raise ValueError('destination overlaps an input')
    for relative in REQUIRED:
        if not (sdk / relative).exists():
            raise ValueError('missing SDK prerequisite: ' + relative)
    if not os.access(sdk / 'usr/local/libexec/availability.pl', os.X_OK):
        raise ValueError('SDK availability.pl is not executable')
    # Only relative internal SDK symlinks are reusable. Never copy an alias
    # through which a subsequent compiler could write into the old checkout.
    for root, dirs, files in os.walk(sdk, followlinks=False):
        for name in dirs + files:
            p = Path(root) / name
            if p.is_symlink():
                target = os.readlink(p)
                if os.path.isabs(target) or not p.resolve().is_relative_to(sdk.resolve()):
                    raise ValueError('SDK symlink escapes snapshot: ' + str(p))
                if not p.exists():
                    raise ValueError('SDK symlink is dangling: ' + str(p))
    if digest(cache) != CACHE_SHA:
        raise ValueError('original kernelcache SHA256 mismatch')

def sdk_manifest(sdk):
    result = {}
    for root, dirs, files in os.walk(sdk, followlinks=False):
        for name in dirs + files:
            p = Path(root) / name
            if p.is_symlink():
                result[str(p.relative_to(sdk))] = ['link', os.readlink(p)]
            elif p.is_file():
                result[str(p.relative_to(sdk))] = ['file', digest(p), p.stat().st_mode & 0o777]
    return result

def commands(destination, jobs):
    base = destination / 'UniXNU'
    device = destination / 'UniXNU-a04br3'
    common = ['-D', str(device), '--out', str(destination / 'out')]
    return [[str(base / 'unixnu'), 'defconfig', 'a04br3_defconfig', *common],
            [str(base / 'unixnu'), 'build', 'kernel', *common, '-j', str(jobs),
             '--kernelcache', str(destination / 'kernelcache.decompressed')]]

def git(repo, *args):
    return run('git', '-C', str(repo), *args)


def gitlinks(repo):
    records = git(repo, 'ls-tree', '-rz', 'HEAD').split('\0')
    return [(record.split('\t', 1)[1], record.split()[2])
            for record in records if record.startswith('160000 ')]


def repository_state(source):
    return {'head': git(source, 'rev-parse', 'HEAD'),
            'status': git(source, 'status', '--porcelain', '--untracked-files=normal'),
            'index': digest(Path(git(source, 'rev-parse', '--path-format=absolute', '--git-path', 'index')))}


def check_repository(source, destination):
    source = source.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('destination overlaps repository: ' + str(source))
    common = Path(git(source, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    if common == destination or common in destination.parents or destination in common.parents:
        raise ValueError('destination overlaps original Git metadata')
    if (common / 'objects/info/alternates').exists():
        raise ValueError('source uses external Git object alternates: ' + str(source))
    if git(source, 'rev-parse', '--show-object-format') != 'sha1':
        raise ValueError('only SHA1 repositories are supported')
    for relative, sha in gitlinks(source):
        child = source / relative
        if not (child / '.git').exists():
            raise ValueError('required submodule is not initialized: ' + str(child))
        check_repository(child, destination)


def copy_repository(source, target):
    """Copy tracked working files and independent objects; never retain gitdir links."""
    before = repository_state(source)
    common = Path(git(source, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    target.mkdir()
    meta = target / '.git'
    meta.mkdir()
    # No source config/hooks/alternates/worktree pointers, credentials or reflogs.
    for name in ('objects', 'refs'):
        shutil.copytree(common / name, meta / name, symlinks=False)
    # Replacement refs must not change the meaning of a pinned object ID.
    if (meta / 'refs/replace').exists():
        shutil.rmtree(meta / 'refs/replace')
    for name in ('packed-refs', 'shallow'):
        if (common / name).exists():
            if name == 'packed-refs':
                (meta / name).write_text(''.join(line for line in (common / name).read_text().splitlines(True)
                                                  if ' refs/replace/' not in line))
            else:
                shutil.copyfile(common / name, meta / name)
    (meta / 'HEAD').write_text(before['head'] + '\n')
    (meta / 'config').write_text('[core]\nrepositoryformatversion = 0\nbare = false\n')
    for record in git(source, 'ls-tree', '-rz', 'HEAD').split('\0'):
        if not record or record.startswith('160000 '):
            continue
        relative = record.split('\t', 1)[1]
        path = source / relative
        # Deleted dirty files are restored only in the new copy below.
        if not path.exists() and not path.is_symlink():
            continue
        if any(p.is_symlink() for p in path.parents if p != source and source in p.parents):
            raise ValueError('tracked file parent is a symlink: ' + str(path))
        out = target / relative
        out.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            out.symlink_to(os.readlink(path))
        else:
            shutil.copy2(path, out)
    # Discard copied dirty bytes only in the isolated destination.
    git(target, '-c', 'core.hooksPath=/dev/null', 'reset', '--hard', before['head'])
    if repository_state(source) != before:
        raise ValueError('source repository changed during copy: ' + str(source))
    return before


def apply_pin(source, target, url, sha, snapshots):
    snapshots[str(source)] = copy_repository(source, target)
    git(target, 'remote', 'add', 'origin', url)
    # Fetch public branch, then verify exact SHA before changing any copied files.
    git(target, 'fetch', '--no-tags', 'origin', 'refs/heads/wifi-sdk')
    if git(target, 'rev-parse', 'FETCH_HEAD') != sha:
        raise ValueError('public branch changed during fetch: ' + url)
    git(target, '-c', 'core.hooksPath=/dev/null', 'checkout', '--detach', sha)
    copy_submodules(source, target, snapshots)
    if git(target, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('isolated checkout is not clean: ' + str(target))


def copy_submodules(source, target, snapshots):
    # Interpret the PINNED tree, not a dirty source .gitmodules/index.
    import configparser
    config = configparser.ConfigParser(interpolation=None)
    modules = target / '.gitmodules'
    if modules.exists():
        config.read(modules)
    for relative, sha in gitlinks(target):
        src = source / relative
        if not (src / '.git').exists():
            raise ValueError('pinned submodule has no existing checkout: ' + str(src))
        check_repository(src, target)
        url = next((config[s]['url'] for s in config.sections()
                    if config[s].get('path') == relative), None)
        if not url or not url.startswith('https://github.com/'):
            raise ValueError('unsupported submodule source: ' + relative)
        out = target / relative
        if out.exists():
            out.rmdir()  # Only an empty gitlink directory is permitted.
        snapshots[str(src)] = copy_repository(src, out)
        git(out, 'remote', 'add', 'origin', url)
        # No submodule update/clone: every repository starts as an existing copy.
        git(out, 'fetch', '--no-tags', 'origin', sha)
        git(out, '-c', 'core.hooksPath=/dev/null', 'checkout', '--detach', sha)
        if git(out, 'rev-parse', 'HEAD') != sha:
            raise ValueError('submodule pin mismatch: ' + relative)
        copy_submodules(src, out, snapshots)
        if git(out, 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('submodule is not clean: ' + relative)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--existing-base', type=Path, required=True)
    p.add_argument('--existing-device', type=Path, required=True)
    p.add_argument('--existing-tree', type=Path, required=True)
    p.add_argument('--kernelcache', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--prepare', action='store_true', help='create NEW workspace; otherwise check only')
    a = p.parse_args()
    if platform.system() != 'Darwin':
        p.error('runner preparation requires macOS (host fixtures run separately)')
    if a.jobs < 1:
        p.error('jobs must be positive')
    sdk = a.existing_tree.resolve() / '.cache/fakeroot'
    destination = a.destination.absolute()
    if destination == a.existing_tree.resolve() or a.existing_tree.resolve() in destination.parents:
        p.error('destination must be outside existing tree')
    sources = {'UniXNU': a.existing_base.resolve(), 'UniXNU-a04br3': a.existing_device.resolve()}
    for source in sources.values():
        check_repository(source, destination)
    validate(destination, sdk, a.kernelcache)
    tools = {name: run('xcrun', '--sdk', name, '--show-sdk-path') for name in ('macosx', 'iphoneos')}
    for tool in ('git', 'make', 'python3', 'clang', 'ar', 'ditto', 'xcodebuild', 'unifdef', 'patch'):
        if not shutil.which(tool):
            p.error('required tool missing: ' + tool)
    for repo, sha in PINS.items():
        remote = run('git', 'ls-remote', 'https://github.com/mouseos/' + repo + '.git', 'refs/heads/wifi-sdk')
        if remote.split()[0] != sha:
            p.error('public wifi-sdk ref changed: ' + repo)
    if not a.prepare:
        print('PASS: prerequisites and public refs; no workspace created')
        return
    before = sdk_manifest(sdk)
    destination.mkdir()  # No removal on failure: preserve evidence for owner.
    snapshots = {}
    for repo, source in sources.items():
        apply_pin(source, destination / repo,
                  'https://github.com/mouseos/' + repo + '.git', PINS[repo], snapshots)
    for source, state in snapshots.items():
        if repository_state(Path(source)) != state:
            raise ValueError('source checkout changed during preparation: ' + source)
    base = destination / 'UniXNU'
    msdc = base / 'drivers/NativeMsdc'
    if run('git', '-C', str(msdc), 'rev-parse', 'HEAD') != PINS['NativeMsdc']:
        raise ValueError('NativeMsdc gitlink mismatch')
    if run('git', '-C', str(base), 'config', '-f', '.gitmodules', '--get', 'submodule.drivers/NativeMsdc.url') != 'https://github.com/mouseos/NativeMsdc.git':
        raise ValueError('NativeMsdc source URL mismatch')
    copied = destination / 'out/a04br3/tree/.cache/fakeroot'
    copied.parent.mkdir(parents=True)
    shutil.copytree(sdk, copied, symlinks=True)
    shutil.copyfile(a.kernelcache, destination / 'kernelcache.decompressed')
    if before != sdk_manifest(sdk) or before != sdk_manifest(copied):
        raise ValueError('SDK changed during snapshot; do not build this destination')
    if digest(destination / 'kernelcache.decompressed') != CACHE_SHA:
        raise ValueError('copied kernelcache mismatch')
    manifest = {'preparation_script_sha256': digest(Path(__file__)),
                'source_repositories': snapshots, 'sdk_source': str(sdk), 'kernelcache_source': str(a.kernelcache.resolve()),
                'xcode_version': run('xcodebuild', '-version'),
                'pins': PINS, 'kernelcache_sha256': CACHE_SHA, 'sdk_tools': tools,
                'sdk_manifest': before, 'commands': commands(destination, a.jobs),
                'build_executed': False, 'reused_obj': False}
    (destination / 'wifi-sdk-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    # Deliberately discard inherited XNU/local override flags, not the owner's
    # environment or checkout. Only the NEW build process has this environment.
    environment = ' '.join(shlex.quote(k + '=' + os.environ[k]) for k in
                           ('PATH', 'HOME', 'TMPDIR', 'DEVELOPER_DIR') if k in os.environ)
    lines = ['#!/bin/sh', 'set -eu']
    for command in manifest['commands']:
        lines.append('env -i ' + environment + ' A04BR3_WIFI=1 ' + shlex.join(command))
    script = destination / 'build-wifi-sdk.sh'
    script.write_text('\n'.join(lines) + '\n')
    script.chmod(0o700)
    print('Prepared only: ' + str(script))

if __name__ == '__main__':
    main()
