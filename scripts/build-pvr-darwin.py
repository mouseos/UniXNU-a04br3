#!/usr/bin/env python3
"""Build the Mesa PowerVR Vulkan driver (src/imagination/vulkan) with the Darwin kernel interface
(patches/mesa/pvr-darwin-winsys.patch), which talks to the NativePowerVR IOKit user client (package/powervr/NativePowerVRABI.h).

The Imagination driver compiles its USC library from OpenCL C at build time, so it needs mesa_clc (LLVM,
SPIRV-LLVM-Translator) and the build-time compilers pco_clc and pdsc_precomp. A cross build cannot run them from the
target, hence two stages, as Mesa's cross-compilation guide does:
  --target host  builds the driver for the Mac with the real IOKit (proof that the darwin interface compiles and links
                 against the real driver) and installs mesa_clc, vtn_bindgen2, pco_clc and pdsc_precomp;
  --target ios   cross-builds the ARM64/iOS 17 library with those tools (-Dmesa-clc=system -Dprecomp-compiler=system),
                 running the host stage first when its tools are missing.
--check-patch only fetches the pinned source and checks the patch (git apply --check); it runs on any system.
Neither build runs a GPU: the interface talks to the NativePowerVR service, which exists only on the tablet.
Output stays under build/ and .cache/ and is never committed."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('--target', choices=('host', 'ios'), default='ios')
parser.add_argument('--configure-only', action='store_true', help='stop after meson setup/configure')
parser.add_argument('--check-patch', action='store_true', help='fetch the pinned Mesa and check the patch, nothing else')
parser.add_argument('--abi-dir', type=Path, help='directory of NativePowerVRABI.h (default: package/powervr, or '
                    'kits/<kit>/powervr in a UniXNU build tree)')
parser.add_argument('-j', '--jobs', type=int, default=os.cpu_count() or 4)
args = parser.parse_args()

PINS = json.loads((ROOT / 'scripts/data/pvr-mesa-inputs.json').read_text())
SOURCE = ROOT / '.cache/mesa-pvr'
VENV = ROOT / '.cache/mesa-venv-pvr'
LOGS = ROOT / '.cache'
TOOLS_OUT = ROOT / 'build/mesa-pvr-host'
TOOLS_PREFIX = TOOLS_OUT / 'install'
PATCHES = [ROOT / p for p in PINS['patches']]


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], **kw)


def read(cmd, **kw):
    return subprocess.check_output([str(c) for c in cmd], text=True, **kw).strip()


def git(*cmd, check=True, **kw):
    return run(['git', '-C', SOURCE, *cmd], check=check, **kw)


def fetch_source():
    """The pinned commit only, blobs on demand (about 550 MB checked out)."""
    mesa = PINS['mesa']
    if not (SOURCE / '.git').exists():
        SOURCE.mkdir(parents=True, exist_ok=True)
        git('init', '-q')
        git('remote', 'add', 'origin', mesa['repository'])
    if run(['git', '-C', SOURCE, 'cat-file', '-e', mesa['commit'] + '^{commit}'], capture_output=True).returncode:
        git('fetch', '-q', '--depth', '1', '--filter=blob:none', 'origin', mesa['commit'])
    if run(['git', '-C', SOURCE, 'rev-parse', '-q', '--verify', 'HEAD'], capture_output=True).returncode:
        git('-c', 'advice.detachedHead=false', 'checkout', '-q', mesa['commit'])
    if read(['git', '-C', SOURCE, 'rev-parse', 'HEAD']) != mesa['commit']:
        sys.exit('Pinned Mesa source differs: %s is not at %s' % (SOURCE, mesa['commit']))
    if read(['git', '-C', SOURCE, 'rev-parse', 'HEAD^{tree}']) != mesa['tree']:
        sys.exit('Pinned Mesa tree differs')


def apply_patches():
    """Applies the recorded patches to a clean tree, or accepts a tree that is exactly HEAD plus them."""
    changed = read(['git', '-C', SOURCE, 'status', '--porcelain', '--untracked-files=all'])
    if not changed:
        for patch in PATCHES:
            if run(['git', '-C', SOURCE, 'apply', '--check', patch]).returncode:
                sys.exit('Patch does not apply to the pinned Mesa: ' + str(patch.relative_to(ROOT)))
        if args.check_patch:
            print('PASS: %s apply to Mesa %s' % (', '.join(str(p.relative_to(ROOT)) for p in PATCHES),
                                                PINS['mesa']['commit'][:12]))
            sys.exit(0)
        for patch in PATCHES:
            git('apply', patch)
        return
    # Already patched: reversing every patch must give back exactly the pinned tree.
    for patch in reversed(PATCHES):
        if run(['git', '-C', SOURCE, 'apply', '--reverse', '--check', patch], capture_output=True).returncode:
            sys.exit('Mesa source has changes the recorded patches do not describe (scripts/save-pvr-darwin-patch.py '
                     'records them):\n' + changed)
    stash = subprocess.run(['git', '-C', str(SOURCE), 'diff', 'HEAD', '--binary'], capture_output=True, check=True).stdout
    untracked = read(['git', '-C', SOURCE, 'ls-files', '--others', '--exclude-standard'])
    expected = b''.join(p.read_bytes() for p in PATCHES)
    files = lambda text: sorted(set(l.split(' b/')[0][len('diff --git a/'):] for l in text.splitlines()
                                    if l.startswith('diff --git a/')))
    patched = files(expected.decode())
    actual = sorted(set(files(stash.decode()) + untracked.splitlines()))
    if actual != patched:
        sys.exit('Mesa source changes differ from the recorded patches: %s' % sorted(set(actual) ^ set(patched)))
    if args.check_patch:
        print('PASS: the Mesa tree is the pinned commit with %s applied' % ', '.join(
            str(p.relative_to(ROOT)) for p in PATCHES))
        sys.exit(0)


def abi_dir():
    if args.abi_dir:
        candidates = [args.abi_dir.resolve()]
    else:
        candidates = [ROOT / 'package/powervr'] + sorted((ROOT / 'kits').glob('*/powervr'))
    for directory in candidates:
        if (directory / 'NativePowerVRABI.h').is_file():
            return directory
    sys.exit('NativePowerVRABI.h not found (give --abi-dir)')


def python_env():
    """meson and Mesa's Python generators in a private venv."""
    meson = VENV / 'bin/meson'
    if not meson.exists():
        run([sys.executable, '-m', 'venv', VENV], check=True)
        run([VENV / 'bin/pip', 'install', '-q', 'meson>=1.4', 'mako', 'pyyaml', 'packaging', 'ply'], check=True)
    return meson


def brew_prefix(formula):
    try:
        return Path(read(['brew', '--prefix', formula], stderr=subprocess.DEVNULL))
    except (OSError, subprocess.CalledProcessError):
        return None


def require_host_tools():
    # Homebrew keeps llvm, bison and flex keg-only and puts the .pc files of spirv-llvm-translator and spirv-tools in
    # their own prefixes: add them, so the build needs no shell setup on a macOS runner.
    if shutil.which('brew'):
        for formula, sub in (('bison', 'bin'), ('flex', 'bin'), ('llvm', 'bin')):
            prefix = brew_prefix(formula)
            if prefix and (prefix / sub).is_dir():
                os.environ['PATH'] = '%s:%s' % (prefix / sub, os.environ.get('PATH', ''))
        pc = [str(prefix / 'lib/pkgconfig') for prefix in map(brew_prefix, ('spirv-llvm-translator', 'spirv-tools', 'llvm'))
              if prefix and (prefix / 'lib/pkgconfig').is_dir()]
        if pc:
            os.environ['PKG_CONFIG_PATH'] = ':'.join(pc + [os.environ.get('PKG_CONFIG_PATH', '')])
    missing = [t for t in ('ninja', 'pkg-config', 'xcrun', 'bison', 'flex') if not shutil.which(t)]
    if missing:
        sys.exit('Missing build tools: %s (brew install ninja pkg-config bison flex)' % ' '.join(missing))
    if not shutil.which('llvm-config'):
        sys.exit('mesa_clc needs LLVM with clang: brew install llvm')
    # libclc is not needed: Mesa only looks for it with rusticl or the Microsoft CLC (meson.build, dep_clc).
    for module in ('LLVMSPIRVLib', 'SPIRV-Tools'):
        if run(['pkg-config', '--exists', module]).returncode:
            sys.exit('pkg-config cannot find %s (brew install spirv-llvm-translator spirv-tools, matching the LLVM major '
                     'version)' % module)


COMMON = ['-Dgallium-drivers=', '-Dvulkan-drivers=imagination', '-Dimagination-kmds=darwin', '-Dplatforms=',
          '-Dglx=disabled', '-Degl=disabled', '-Dgbm=disabled', '-Dopengl=false', '-Dgles1=disabled',
          '-Dgles2=disabled', '-Dxmlconfig=disabled', '-Dshader-cache=disabled', '-Dtools=', '-Dbuild-tests=false',
          '-Dvideo-codecs=', '-Dvulkan-layers=', '-Dvalgrind=disabled', '-Dlibunwind=disabled',
          '-Dlmsensors=disabled']


def configure(meson, out, options, env, name, cross=None):
    log = LOGS / ('mesa-pvr-%s-configure.log' % name)
    if (out / 'build.ninja').exists():
        # An existing build directory takes the current options, so an option added here reaches it.
        command = [meson, 'configure', out] + options
    else:
        command = [meson, 'setup', out, SOURCE, '--wrap-mode=nodownload'] + (['--cross-file', cross] if cross else []) + options
    with log.open('w') as stream:
        if run(command, env=env, stdout=stream, stderr=subprocess.STDOUT).returncode:
            sys.exit('Configure failed; see %s\n%s' % (log, '\n'.join(log.read_text().splitlines()[-15:])))


def ninja(out, env, name, target=None):
    log = LOGS / ('mesa-pvr-%s-build.log' % name)
    with log.open('w') as stream:
        result = run(['ninja', '-C', out, '-j%d' % args.jobs] + ([target] if target else []), env=env, stdout=stream,
                     stderr=subprocess.STDOUT)
    if result.returncode:
        sys.exit('Build failed; see %s\n%s' % (log, '\n'.join(
            l for l in log.read_text().splitlines() if ': error:' in l or 'fatal error:' in l or l.startswith('FAILED:'))[:3000]))


def check_library(library, what):
    if not library.exists():
        sys.exit('Missing ' + str(library))
    symbols = run(['nm', '-gU', library], capture_output=True, text=True).stdout
    for symbol in ('_vk_icdGetInstanceProcAddr', '_vkGetInstanceProcAddr'):
        if ' T ' + symbol not in symbols:
            sys.exit('%s does not export %s' % (library, symbol[1:]))
    print('PASS: PowerVR Vulkan with the darwin kernel interface builds for %s (%d bytes); no GPU or IOKit service was '
          'exercised' % (what, library.stat().st_size))


def base_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PVR_', 'NIR_', 'MESA_', 'PCO_'))}
    env['PATH'] = str(VENV / 'bin') + ':' + env['PATH']
    return env


def build_host(meson, abi):
    require_host_tools()
    env = base_env()
    env.update(CC='/usr/bin/clang', CXX='/usr/bin/clang++')
    options = COMMON + ['-Dimagination-darwin-abi-dir=' + str(abi), '-Dllvm=enabled', '-Dmesa-clc=enabled',
                        '-Dinstall-mesa-clc=true', '-Dprecomp-compiler=enabled', '-Dinstall-precomp-compiler=true',
                        '--prefix=' + str(TOOLS_PREFIX)]
    configure(meson, TOOLS_OUT, options, env, 'host')
    if args.configure_only and args.target == 'host':
        print('PASS: PowerVR darwin configuration (host)')
        sys.exit(0)
    ninja(TOOLS_OUT, env, 'host')
    ninja(TOOLS_OUT, env, 'host-install', 'install')
    check_library(TOOLS_OUT / 'src/imagination/vulkan/libvulkan_powervr_mesa.dylib', 'macOS')


def build_ios(meson, abi):
    tools = [TOOLS_PREFIX / 'bin' / t for t in ('mesa_clc', 'vtn_bindgen2', 'pco_clc', 'pdsc_precomp')]
    if not all(t.exists() for t in tools):
        build_host(meson, abi)
    if not shutil.which('xcrun'):
        sys.exit('The iOS build needs Xcode (xcrun)')
    sdk = read(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'])
    find = lambda tool: read(['xcrun', '--sdk', 'iphoneos', '--find', tool])
    flags = ['-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-fstack-protector-strong']
    cross = ROOT / 'build/mesa-pvr-ios.cross'
    cross.parent.mkdir(exist_ok=True)
    cross.write_text(
        '[binaries]\nc = %r\ncpp = %r\nobjc = %r\nar = %r\nstrip = %r\npkg-config = \'/usr/bin/false\'\n'
        % (find('clang'), find('clang++'), find('clang'), find('ar'), find('strip')) +
        "[host_machine]\nsystem = 'darwin'\ncpu_family = 'aarch64'\ncpu = 'arm64'\nendian = 'little'\n"
        "[properties]\nneeds_exe_wrapper = true\n[built-in options]\n" +
        ''.join('%s = %r\n' % (k, flags) for k in ('c_args', 'cpp_args', 'objc_args', 'c_link_args', 'cpp_link_args',
                                                    'objc_link_args')))
    env = base_env()
    env['PATH'] = str(TOOLS_PREFIX / 'bin') + ':' + env['PATH']
    env.update(CC=find('clang'), CXX=find('clang++'), CFLAGS='', CXXFLAGS='', CPPFLAGS='', LDFLAGS='')
    # Release with NDEBUG for the tablet, as the Turnip build: no per-pass assertions or NIR validation.
    options = COMMON + ['-Dimagination-darwin-abi-dir=' + str(abi), '-Dllvm=disabled', '-Dmesa-clc=system',
                        '-Dprecomp-compiler=system', '-Dexpat=disabled', '-Dzstd=disabled', '-Dbuildtype=release',
                        '-Db_ndebug=true']
    out = ROOT / 'build/mesa-pvr-ios'
    configure(meson, out, options, env, 'ios', cross)
    if args.configure_only:
        print('PASS: PowerVR darwin configuration (ios)')
        sys.exit(0)
    ninja(out, env, 'ios', 'src/imagination/vulkan/libvulkan_powervr_mesa.dylib')
    check_library(out / 'src/imagination/vulkan/libvulkan_powervr_mesa.dylib', 'iOS arm64')


fetch_source()
apply_patches()
if sys.platform != 'darwin':
    sys.exit('The builds need macOS (IOKit, Xcode); --check-patch runs anywhere')
meson = python_env()
abi = abi_dir()
if args.target == 'host':
    build_host(meson, abi)
else:
    build_ios(meson, abi)
