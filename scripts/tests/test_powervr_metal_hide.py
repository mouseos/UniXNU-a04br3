#!/usr/bin/env python3
"""Host test of the process-name list used by a04br3-gpu-metal-hide=1 (package/powervr/NativePowerVRNameList.h): the same rule as the
kernel's sandbox grant (exact names, an entry of 16 or more characters compared by its first 16), and the accelerator hooks refuse
the plugin keys only through it."""
import os, subprocess, tempfile
from pathlib import Path
root = Path(__file__).resolve().parents[2]
header = root / 'package/powervr/NativePowerVRNameList.h'
cases = [
    ('backboardd', 'backboardd', 1), ('backboardd', 'backboard', 0), ('backboardd', 'backboarddX', 0), ('backboardd', '', 0),
    ('backboardd,SpringBoard', 'SpringBoard', 1), ('backboardd,SpringBoard', 'Springboard', 0), (',backboardd,', 'backboardd', 1),
    ('', 'backboardd', 0), ('iconservicesagent', 'iconservicesage', 0), ('iconservicesagen', 'iconservicesagen', 1),
    ('iconservicesagent', 'iconservicesagen', 1), ('WidgetRenderer_Default', 'WidgetRenderer_D', 1), ('backboardd', 'iconservicesagen', 0),
    ('a,b,c', 'b', 1), ('a,b,c', 'c', 1), ('a,b,c', 'd', 0), ('abc', 'ab', 0),
]
src = '#include <stdio.h>\n#include "%s"\nint main(){int bad=0;\n' % header
for lst, name, want in cases:
    src += 'if(native_powervr_name_listed("%s","%s")!=%d){printf("FAIL %s / %s\\n");bad=1;}\n' % (lst, name, want, lst, name)
src += 'if(native_powervr_name_listed(nullptr,"a")||native_powervr_name_listed("a",nullptr)){puts("FAIL null");bad=1;}\nreturn bad;}\n'
with tempfile.TemporaryDirectory() as d:
    c = Path(d) / 't.cpp'; c.write_text(src); exe = Path(d) / 't'
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
# The three accelerator overrides return nothing when the visibility check refuses, and the check is the only place that parses the flag.
acc = (root / 'package/powervr/NativePowerVRAccelerator.cpp').read_text()
assert acc.count('if (!native_powervr_metal_note_property_read(') == 3, 'every copyProperty override honours the visibility check'
metal = (root / 'package/powervr/NativePowerVRMetal.cpp').read_text()
assert metal.count('a04br3-gpu-metal-hide') >= 2 and 'bootArg("a04br3-gpu-metal-hide", 0) == 1' in metal, 'default off, enabled only by =1'
assert 'proc_selfpid() == 0' in metal, 'kernel reads keep the keys'
print('powervr name list:', len(cases), 'cases passed')

