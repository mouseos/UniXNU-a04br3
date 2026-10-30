#!/usr/bin/env python3
"""Exercise the shipped Darwin submit error mapping and fence publication."""
from pathlib import Path
import ast
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
patch = (root / 'patches/mesa/pvr-darwin-winsys.patch').read_text()
added = '\n'.join(line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))

def fn(sig):
    start = added.index(sig)
    pos = added.index('{', start) + 1
    depth = 1
    while depth:
        depth += (added[pos] == '{') - (added[pos] == '}')
        pos += 1
    return added[start:pos]

serial_test = ast.parse((root / 'scripts/tests/check_powervr_serial_transfer.py').read_text())
pre = next(ast.literal_eval(n.value) for n in serial_test.body if isinstance(n, ast.Assign)
           and any(isinstance(t, ast.Name) and t.id == 'pre' for t in n.targets))
pre = pre.replace('#define vk_errorf(...) VK_ERROR_DEVICE_LOST',
                  '#define vk_errorf(obj, result, ...) (result)\n#define kIOReturnIOError 123')
pre = pre.replace('static int wc,mode,submitted,locked;', 'static int wc,mode,submitted,locked,call_error;')
old = 'p->fence[i]=(mode==3&&i==1)?0:i+1;return 0;}'
assert old in pre
pre = pre.replace(old, 'p->fence[i]=(mode==3&&i==1)?0:i+1;return call_error;}')
code = pre + '\n' + fn('static void pvr_darwin_submit_barrier(') + '\n' + fn('VkResult pvr_darwin_submit_jobs(') + r'''
int main(void) {
 struct drm_pvr_ioctl_submit_jobs_args args={{1,sizeof(struct drm_pvr_job)}};
 struct pvr_darwin_sync sync={0};struct pvr_darwin_signal signal={&sync.base,0};
 unsetenv("PVR_DARWIN_SERIAL_TRANSFER");
 call_error=kIOReturnIOError;assert(pvr_darwin_submit_jobs(2,&args,0,0,&signal,1,"transfer")==VK_ERROR_DEVICE_LOST);
 assert(submitted==1&&!locked&&!sync.resolved);
 call_error=321;assert(pvr_darwin_submit_jobs(2,&args,0,0,&signal,1,"transfer")==VK_ERROR_OUT_OF_DEVICE_MEMORY);
 assert(submitted==2&&!locked&&!sync.resolved);
 call_error=0;assert(pvr_darwin_submit_jobs(2,&args,0,0,&signal,1,"transfer")==VK_SUCCESS);
 assert(submitted==3&&locked==1&&sync.resolved);
}
'''
with tempfile.TemporaryDirectory(prefix='pvr-submit-device-lost-') as temp:
    path = Path(temp)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                    '-fno-pie','-no-pie',str(path/'test.c'),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test')],check=True)
gpu = (root / 'package/powervr/NativePowerVRGpu.cpp').read_text()
a = gpu.index('IOReturn ioReturnOf(int err)')
b = gpu.index('{', a) + 1
depth = 1
while depth:
    depth += (gpu[b] == '{') - (gpu[b] == '}')
    b += 1
kernel = r"""
#include <cassert>
#include <cerrno>
using IOReturn = int;
enum {kIOReturnSuccess=0,kIOReturnBadArgument=1,kIOReturnNoMemory=2,kIOReturnUnsupported=3,
 kIOReturnNotPermitted=4,kIOReturnBusy=5,kIOReturnTimeout=6,kIOReturnNotFound=7,kIOReturnVMError=8,
 kIOReturnOverrun=9,kIOReturnNoSpace=10,kIOReturnNoDevice=11,kIOReturnIOError=12};
""" + gpu[a:b] + r"""
int main(){assert(ioReturnOf(-EIO)==kIOReturnIOError);assert(ioReturnOf(-ENOMEM)==kIOReturnNoMemory);
 assert(ioReturnOf(0)==kIOReturnSuccess);}
"""
with tempfile.TemporaryDirectory(prefix='pvr-io-error-') as temp:
    path=Path(temp)
    (path/'test.cpp').write_text(kernel)
    subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                    '-fno-pie','-no-pie',str(path/'test.cpp'),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test')],check=True)
print('PASS actual kernel EIO mapping and Darwin submit: I/O error to DEVICE_LOST, other error unchanged, no fence signal on error; ASan/UBSan')
