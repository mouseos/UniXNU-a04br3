#!/usr/bin/env python3
"""Build the a04br3 Metal plugin NativePowerVRMetal.bundle: UniXNU's vendor-neutral NativeMetal plugin
(drivers/NativeMetal/NativeVulkanMetalPlugin.mm, scripts/build-vulkan-metal-plugin.py) with this board's hooks
(package/powervr/user/NativePowerVRMetalBoard.mm) and Mesa's PowerVR Vulkan driver (scripts/build-pvr-darwin.py --target ios).

  build-a04br3-metal-plugin.py [--vulkan LIBRARY] [--out DIR] [--skip-bridge] [--sipf1-dump]

Runs in a UniXNU build tree (out/a04br3/tree: the tree's scripts/ holds both repositories' scripts, kits/a04br3 this package).
The names match the accelerator of package/powervr/NativePowerVRMetal.cpp: MetalPluginName NativePowerVRMetal, MetalPluginClassName
NativePowerVRMetalDevice. The driver is signed with the identifier the PowerVR probe uses for the same library
(build-a04br3-powervr-probe.py LIB_IDENTIFIER), so the same Mesa build gives the same CDHash in both places.

Output (ignored): OUT/NativePowerVRMetal.bundle and OUT/trust.json (default OUT build/a04br3-metal-plugin). Then:
  build-a04br3-powervr-probe.py ... --trust-base BUNDLE --trust-out OWNER --extra-trust OUT/trust.json   (one owner module)
  a04br3-seed-powervr-probe.py --tree TREE --probe-dir PROBE --metal-plugin OUT [--metal-mode publish|selftest] BASE.dbr OUT.dbr
and the kernel arguments a04br3-gpu-metal=1 and sheng-fb-gpu=1 (the plugin's scanout hand-off)."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
NAME, PRINCIPAL = 'NativePowerVRMetal', 'NativePowerVRMetalDevice'
LIB_NAME = 'libvulkan_powervr_mesa.dylib'
LIB_IDENTIFIER = 'org.unixnu.a04br3.libvulkan_powervr_mesa'


def powervr_dir():
    """package/powervr of the device repository, or kits/<kit>/powervr in a UniXNU build tree (as build-pvr-darwin.py)."""
    for directory in [ROOT / 'package/powervr'] + sorted((ROOT / 'kits').glob('*/powervr')):
        if (directory / 'user/NativePowerVRMetalBoard.mm').is_file():
            return directory
    sys.exit('package/powervr/user/NativePowerVRMetalBoard.mm not found')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--vulkan', type=Path, help='Mesa PowerVR driver (default: build-pvr-darwin.py --target ios output)')
    parser.add_argument('--out', type=Path, default=ROOT / 'build/a04br3-metal-plugin')
    parser.add_argument('--skip-bridge', action='store_true')
    parser.add_argument('--sipf1-dump', action='store_true',
                        help='enable bounded CPU-owned SIPF1 command snapshots in this diagnostic bundle only')
    args = parser.parse_args()
    builder = ROOT / 'scripts/build-vulkan-metal-plugin.py'
    if not builder.is_file():
        sys.exit('%s not found: run this in a UniXNU build tree (./unixnu tree -D <this repository>)' % builder)
    library = (args.vulkan or ROOT / 'build/mesa-pvr-ios/src/imagination/vulkan' / LIB_NAME).resolve()
    if not library.is_file():
        sys.exit('Build Mesa first: python3 scripts/build-pvr-darwin.py --target ios (or give --vulkan LIBRARY)')
    board = powervr_dir() / 'user/NativePowerVRMetalBoard.mm'
    command = ['python3', str(builder), '--name', NAME, '--principal-class', PRINCIPAL, '--vulkan-driver', str(library),
               '--driver-name', LIB_NAME, '--driver-identifier', LIB_IDENTIFIER, '--identifier', 'org.unixnu.a04br3.' + NAME,
               '--board-source', str(board), '--out', str(args.out.resolve()), '--portable-sha2']
    if args.sipf1_dump:
        # The generic builder recreates OUT; generated input must be its sibling.
        args.out.resolve().parent.mkdir(parents=True, exist_ok=True)
        diagnostic = args.out.resolve().with_name(args.out.name + '.sipf1-dump-enable.mm')
        diagnostic.write_text(
            '#include <stdlib.h>\n'
            '__attribute__((constructor)) static void a04br3_sipf1_dump_enable(void) {\n'
            '  setenv("PVR_DARWIN_SIPF1_DUMP", "1", 1);\n'
            '}\n')
        command += ['--board-source', str(diagnostic)]
    # MT8167's optional SHA2 instruction support is not assumed from Apple's target triple.
    if args.skip_bridge:
        command.append('--skip-bridge')
    subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
