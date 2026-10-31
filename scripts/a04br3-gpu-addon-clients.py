#!/usr/bin/env python3
"""Add, replace or remove the Metal plugin's process list (addons/NativePowerVRMetal.clients) in an existing NGPUADD2 GPU addon.

The plugin (UniXNU drivers/NativeMetal/NativeVulkanMetalPlugin.mm) gives a native Metal device to backboardd and to every process its
<Name>.clients beside the bundle names; the addon's addons/ tree is the union view over /System/Library/Extensions, so the list
travels with the addon and the prepared Data seed stays unchanged. The list is data, not code: the trust cache is unchanged.
usage: a04br3-gpu-addon-clients.py IN OUT [NAME ...]      (no NAME: remove the list)
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENTS = 'addons/NativePowerVRMetal.clients'


def helper():
    spec = importlib.util.spec_from_file_location('powervr_probe_build', ROOT / 'scripts/build-a04br3-powervr-probe.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def with_clients(blob, names, module=None):
    module = module or helper()
    for name in names:
        if not name or not all(c.isalnum() or c in '._-' for c in name) or len(name) > 32:
            raise SystemExit('Bad process name: %r' % name)
    files = [f for f in module.unpack_addon(blob) if f[0] != CLIENTS]
    if names:
        files.append((CLIENTS, 0o644, ('\n'.join(names) + '\n').encode()))
    out = module.pack_addon(files)
    module.unpack_addon(out)
    return out


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    source, target, names = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
    if target.exists():
        raise SystemExit('Refuse to overwrite ' + str(target))
    target.write_bytes(with_clients(source.read_bytes(), names))
    print('PASS: %s with %s' % (target, ('clients ' + ','.join(names)) if names else 'no clients list'))


if __name__ == '__main__':
    main()

