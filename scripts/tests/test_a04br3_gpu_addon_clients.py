#!/usr/bin/env python3
"""Host test of scripts/a04br3-gpu-addon-clients.py on the real addon format (pack/unpack of build-a04br3-powervr-probe.py): the
list is added, replaced and removed, every other file keeps its bytes and mode, bad names are refused, and the default grant names
iconservicesagent next to backboardd."""
import importlib.util
from pathlib import Path
root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('clients', root / 'scripts/a04br3-gpu-addon-clients.py')
clients = importlib.util.module_from_spec(spec); spec.loader.exec_module(clients)
helper = clients.helper()
base = helper.pack_addon([('bin/powervrprobed', 0o755, b'\xcf\xfa\xed\xfe' + bytes(100)),
                          ('addons/NativePowerVRMetal.bundle/NativePowerVRMetal', 0o755, b'plugin' * 1000),
                          ('addons/NativePowerVRMetal.bundle/Info.plist', 0o644, b'<plist/>')])
one = clients.with_clients(base, ['iconservicesagent'], helper)
files = {n: (m, d) for n, m, d in helper.unpack_addon(one)}
assert files[clients.CLIENTS] == (0o644, b'iconservicesagent\n')
for n, m, d in helper.unpack_addon(base):
    assert files[n] == (m, d), n
two = clients.with_clients(one, ['iconservicesagent', 'Preferences'], helper)
assert [d for n, m, d in helper.unpack_addon(two) if n == clients.CLIENTS] == [b'iconservicesagent\nPreferences\n']
assert helper.unpack_addon(clients.with_clients(two, [], helper)) == helper.unpack_addon(base)
for bad in (['../x'], ['a b'], [''], ['x' * 33]):
    try:
        clients.with_clients(base, bad, helper); raise AssertionError(bad)
    except SystemExit:
        pass
metal = (root / 'package/powervr/NativePowerVRMetal.cpp').read_text()
assert 'char grant[128] = "backboardd,iconservicesagent";' in metal
print('PASS: addon clients list added/replaced/removed, other files unchanged, bad names refused, default grant')

