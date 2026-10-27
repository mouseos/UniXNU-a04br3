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


def canonical_uuid(value):
    import re
    import uuid
    if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
        raise argparse.ArgumentTypeError('UUID must use the 8-4-4-4-12 hexadecimal form')
    return str(uuid.UUID(value)).upper()


def write_launchers(destination, manifest, data_uuid, cache_uuid):
    outputs = ('wifi-sdk-manifest.json', 'build-wifi-sdk.py', 'build-wifi-sdk.sh')
    for name in outputs:
        path = destination / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError('launcher output is not a regular file: ' + str(path))
    # Record only non-secret, explicit inputs. Credentials are read at BUILD time.
    environment = {k: os.environ[k] for k in
                   ('PATH', 'HOME', 'TMPDIR', 'DEVELOPER_DIR') if k in os.environ}
    environment.update(A04BR3_WIFI='1', XNU_OWNER_DATA_VOLUME_UUID=data_uuid,
                       XNU_OWNER_CACHE_VOLUME_UUID=cache_uuid)
    manifest.update(data_volume_uuid=data_uuid, cache_volume_uuid=cache_uuid,
                    preparation_script_sha256=digest(Path(__file__)),
                    build_environment=environment)
    config = {'environment': environment, 'commands': manifest['commands']}
    launcher = '''#!/usr/bin/env python3
"""Fixed WiFi SDK build. Token is runtime-only, never serialized or placed in argv."""
import json
import os
import subprocess

CONFIG = json.loads(%r)

def main():
    environment = dict(CONFIG['environment'])
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        environment['GITHUB_TOKEN'] = token
    for command in CONFIG['commands']:
        subprocess.run(command, env=environment, check=True)

if __name__ == '__main__':
    main()
''' % json.dumps(config)
    script = destination / 'build-wifi-sdk.sh'
    python_launcher = destination / 'build-wifi-sdk.py'
    python_launcher.write_text(launcher)
    python_launcher.chmod(0o700)
    script.write_text('#!/bin/sh\nset -eu\nexec python3 -I ' + shlex.quote(str(python_launcher)) + '\n')
    script.chmod(0o700)
    (destination / 'wifi-sdk-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return script


def refresh_launchers(destination, data_uuid, cache_uuid):
    # Supported warm retry: update launchers only, never source, SDK or OBJ.
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError('destination has a symlink component')
    manifest_path = destination / 'wifi-sdk-manifest.json'
    if manifest_path.is_symlink():
        raise ValueError('manifest must not be a symlink')
    manifest = json.loads(manifest_path.read_text())
    try:
        build = manifest['commands'][1]
        jobs = int(build[build.index('-j') + 1])
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError('invalid prepared build commands') from None
    if jobs < 1 or manifest.get('pins') != PINS or manifest['commands'] != commands(destination, jobs):
        raise ValueError('prepared source pins or build commands mismatch')
    if digest(destination / 'kernelcache.decompressed') != CACHE_SHA:
        raise ValueError('prepared kernelcache mismatch')

    def verify(repo, sha):
        for flag in ('--absolute-git-dir', '--git-common-dir'):
            metadata = Path(git(repo, 'rev-parse', '--path-format=absolute', flag)).resolve()
            if not metadata.is_relative_to(destination.resolve()):
                raise ValueError('prepared repository references external Git metadata')
            if (metadata / 'objects/info/alternates').exists():
                raise ValueError('prepared repository has external Git object alternates')
        if Path(git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo.resolve():
            raise ValueError('prepared repository has an external worktree')
        if git(repo, 'rev-parse', 'HEAD') != sha or git(repo, 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('prepared repository is not clean at its pin: ' + str(repo))
        for relative, child_sha in gitlinks(repo):
            verify(repo / relative, child_sha)
    for repo in ('UniXNU', 'UniXNU-a04br3'):
        verify(destination / repo, PINS[repo])
    if git(destination / 'UniXNU/drivers/NativeMsdc', 'rev-parse', 'HEAD') != PINS['NativeMsdc']:
        raise ValueError('prepared NativeMsdc mismatch')
    script = write_launchers(destination, manifest, data_uuid, cache_uuid)
    print('Refreshed launchers only: ' + str(script))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--existing-base', type=Path)
    p.add_argument('--existing-device', type=Path)
    p.add_argument('--existing-tree', type=Path)
    p.add_argument('--kernelcache', type=Path)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--data-volume-uuid', type=canonical_uuid, required=True,
                   help='observed Data UUID (never inherited implicitly)')
    p.add_argument('--cache-volume-uuid', type=canonical_uuid,
                   default='8846FE78-2910-4297-AF97-D59BB6F8FC16',
                   help='SystemOS UUID; default matches fixed base source')
    p.add_argument('--jobs', type=int, default=4)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--prepare', action='store_true', help='create NEW workspace; otherwise check only')
    mode.add_argument('--refresh-launcher', action='store_true',
                      help='verify an existing prepared workspace and refresh launchers only')
    a = p.parse_args()
    if platform.system() != 'Darwin':
        p.error('runner preparation requires macOS (host fixtures run separately)')
    if a.jobs < 1:
        p.error('jobs must be positive')
    if a.refresh_launcher:
        refresh_launchers(a.destination.absolute(), a.data_volume_uuid, a.cache_volume_uuid)
        return
    for name in ('existing_base', 'existing_device', 'existing_tree', 'kernelcache'):
        if getattr(a, name) is None:
            p.error('--' + name.replace('_', '-') + ' is required without --refresh-launcher')
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
    script = write_launchers(destination, manifest, a.data_volume_uuid, a.cache_volume_uuid)
    print('Prepared only: ' + str(script))

if __name__ == '__main__':
    main()
