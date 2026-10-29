#!/usr/bin/env python3
"""Exercise the actual boot-option gate without a kernel or device."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'package/powervr/NativePowerVRJobs.cpp').read_text()
start = source.index('extern "C" void native_powervr_submit_stage_enable(')
end = source.index('\nextern "C" void native_powervr_io_trace_enable', start)
function = source[start:end]
fixture = '''#include <cassert>
static unsigned gSubmitStageEnabled=0, logs=0;
void IOLog(const char *, ...) {++logs;}
''' + function + '''
int main() {
 native_powervr_submit_stage_enable(0,6); assert(!gSubmitStageEnabled && !logs);
 native_powervr_submit_stage_enable(1,5); assert(!gSubmitStageEnabled && logs==1);
 native_powervr_submit_stage_enable(2,6); assert(!gSubmitStageEnabled && logs==1);
 native_powervr_submit_stage_enable(1,6); assert(gSubmitStageEnabled && logs==1);
 native_powervr_submit_stage_enable(0,6); assert(!gSubmitStageEnabled);
}
'''
with tempfile.TemporaryDirectory() as temp:
    c = Path(temp) / 'gate.cpp'
    binary = Path(temp) / 'gate'
    c.write_text(fixture)
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',str(c),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
print('PASS actual boot option gate, default off and mode 6 required (UBSan)')
