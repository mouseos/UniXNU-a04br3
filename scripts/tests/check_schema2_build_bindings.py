"""Check real producer/consumer paths and execute the AES binding controller; no SDK ABI claim."""
from pathlib import Path
import argparse
import ast
import os
import shutil
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--tree', type=Path, required=True)
args = parser.parse_args()
tree = args.tree.resolve()


def assignment(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
    raise AssertionError('assignment missing: ' + name)


def value(node, **names):
    return eval(compile(ast.Expression(node), '<actual builder expression>', 'eval'), {'__builtins__': {}}, names)


builder = (tree / 'scripts/build-arm-driver.py').read_text()
aes = (tree / 'scripts/build-native-aes-bindings.py').read_text()
hid = (tree / 'scripts/build-native-hid-input.py').read_text()
producer = {name: value(assignment(builder, 'out'), ROOT=tree, component=name)
            for name in ['NativeAES', 'NativeHidInput']}
arg = next(n for n in ast.walk(ast.parse(aes)) if isinstance(n, ast.Call)
           and isinstance(n.func, ast.Attribute) and n.func.attr == 'add_argument'
           and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == '--objects-dir')
aes_input = value(next(k.value for k in arg.keywords if k.arg == 'default'), ROOT=tree)
hid_output = value(assignment(hid, 'OUT'), ROOT=tree)
assert producer['NativeAES'] == aes_input
assert producer['NativeHidInput'] == hid_output
assert "OUT/'OriginalHidDeviceABI.h'" in hid
assert '#include "OriginalHidDeviceABI.h"' in (tree / 'drivers/NativeHidInput/NativeHidInput.cpp').read_text()
assert 'drivers/NativeHidInput/NativeHidInput.cpp' in hid
assert 'drivers/ShengHidInput/' not in hid
assert (tree / 'kits/NativePrelink/native_prelink.py').is_file()

# Run the complete real AES binder's recorded-binding branch with a tool seam.
# The seam checks every archive input exists; it does not emulate compilation/ABI.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / 'scripts/data').mkdir(parents=True)
    (root / 'kits').mkdir()
    shutil.copyfile(tree / 'scripts/build-native-aes-bindings.py', root / 'scripts/build-native-aes-bindings.py')
    shutil.copyfile(tree / 'scripts/data/native-aes-original-bindings.S', root / 'scripts/data/native-aes-original-bindings.S')
    (root / 'kits/NativePrelink').symlink_to(tree / 'kits/NativePrelink', target_is_directory=True)
    out = root / producer['NativeAES'].relative_to(tree)
    out.mkdir(parents=True)
    (out / 'NativeAES.o').write_bytes(b'owned-object-fixture')
    tools = root / 'tools'
    tools.mkdir()
    tool = tools / 'xcrun'
    tool.write_text('''#!/usr/bin/env python3
import sys
from pathlib import Path
args=sys.argv[1:]
if args[0]=='clang':
    Path(args[args.index('-o')+1]).write_bytes(b'bindings-fixture')
elif args[:2]==['ar','rcs']:
    for source in args[3:]:
        assert Path(source).is_file(), 'missing actual archive input: '+source
    Path(args[2]).write_bytes(b'archive-fixture')
else:
    raise AssertionError(args)
''')
    tool.chmod(0o755)
    subprocess.run([sys.executable, str(root / 'scripts/build-native-aes-bindings.py')], check=True,
                   env={**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH']})
    assert (out / 'libNativeAES.a').is_file()
print('PASS actual AES producer/object/binder/archive and HID producer/header/audited-source paths; full SDK build still required')
