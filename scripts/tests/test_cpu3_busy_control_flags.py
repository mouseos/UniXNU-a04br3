#!/usr/bin/env python3
"""Check the actual builder gate, including invalid and missing prerequisites."""
import ast
from types import SimpleNamespace
from itertools import product
from pathlib import Path
import sys
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from a04br3_busy_control_config import busy_control_flags
source = (root / 'build-arm-driver.py').read_text()
tree = ast.parse(source)
node = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
            and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == 'busy_control_flags' for call in ast.walk(node)))
code = compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), __file__, 'exec')
def builder_gate(component, env):
    command = []
    exec(code, {'component': component, 'os': SimpleNamespace(environ=env),
                'busy_control_flags': busy_control_flags, 'command': command})
    return command
keys = ('XNU_ARMCPU_GICV2', 'XNU_ARMCPU_A04BR3_RUNTIME', 'XNU_ARMCPU_RUNTIME_WINDOW', 'XNU_ARMCPU_IDLE_EPOCH', 'XNU_PMUV3_ACTIVE', 'XNU_ARMCPU_SWITCH_OBSERVER')
assert busy_control_flags({}) == []
assert builder_gate("ArmCpu", {}) == []
assert builder_gate("NativeMsdc", {"XNU_ARMCPU_BUSY_CONTROL": "1"}) == []
for bits in product(('0', '1'), repeat=len(keys)):
    env = dict(zip(keys, bits), XNU_ARMCPU_BUSY_CONTROL='1')
    try:
        flags = busy_control_flags(env)
    except ValueError:
        assert '0' in bits
        try:
            builder_gate('ArmCpu', env)
        except SystemExit:
            pass
        else:
            raise AssertionError(bits)
    else:
        assert all(x == '1' for x in bits)
        assert flags == ['-DARMCPU_RUNTIME_BUSY_CONTROL=1']
        assert builder_gate("ArmCpu", env) == flags
for value in ('yes', '', '2'):
    try:
        busy_control_flags({'XNU_ARMCPU_BUSY_CONTROL': value})
    except ValueError:
        pass
    else:
        raise AssertionError(value)
print('PASS CPU3 build flag: default off, prerequisites and invalid values')
