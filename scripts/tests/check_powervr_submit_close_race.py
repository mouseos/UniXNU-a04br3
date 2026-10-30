#!/usr/bin/env python3
"""Compile the real UserClient::run call/return tail under controlled close races."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'package/powervr/NativePowerVRGpu.cpp').read_text()
abi = (root / 'package/powervr/NativePowerVRABI.h').read_text()

def function(signature):
    start = src.index(signature)
    pos = src.index('{', start) + 1
    depth = 1
    while depth:
        depth += (src[pos] == '{') - (src[pos] == '}')
        pos += 1
    return src[start:pos]

run = function('IOReturn NativePowerVRUserClient::run(')
start = run.index('  const uint64_t lockPerf =')
end = run.index('  return kIOReturnSuccess;', start) + len('  return kIOReturnSuccess;')
tail = run[start:end]
assert 'closing_' not in tail[tail.index('IOLockUnlock(lock_);'):]
assert 'failed call returns no fences' in abi and 'may leave part of a batch submitted' in abi
assert 'All jobs are queued or none is' not in abi
pre = r'''
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstring>
using IOReturn=int;
enum {kIOReturnSuccess=0,kIOReturnBadArgument=1,kIOReturnNoMemory=2,kIOReturnUnsupported=3,
 kIOReturnNotPermitted=4,kIOReturnBusy=5,kIOReturnTimeout=6,kIOReturnNotFound=7,kIOReturnVMError=8,
 kIOReturnOverrun=9,kIOReturnNoSpace=10,kIOReturnNoDevice=11,kIOReturnIOError=12,kIOReturnNotReady=13};
constexpr uint32_t NativePowerVRMethodSubmitJobs=1,OtherMethod=2;
struct Shape{uint32_t output=0;};struct NativePowerVRAbiSubmitReply{uint64_t fence[2]{};};
struct Lock{bool held=false;};
class NativePowerVRUserClient {
 public:
 Lock lockStorage;Lock *lock_=&lockStorage;bool closing_=false,closeDuringCall=false,closeAfterUnlock=false;
 int result=0,calls=0;
 int call(uint32_t,void *,NativePowerVRAbiSubmitReply *reply){assert(lock_->held);++calls;reply->fence[0]=99;
  if(closeDuringCall){lock_->held=false;closing_=true;lock_->held=true;}return result;}
 IOReturn runTail(uint32_t method,void *output);
};
static NativePowerVRUserClient *currentClient;
void IOLockLock(Lock *l){assert(!l->held);l->held=true;}
void IOLockUnlock(Lock *l){assert(l->held);l->held=false;if(currentClient->closeAfterUnlock)currentClient->closing_=true;}
uint64_t native_powervr_submit_perf_ioctl_begin(int){return 0;}
void native_powervr_submit_perf_ioctl_end(int,uint64_t){}
uint64_t mach_absolute_time(){return 0;}
void native_powervr_io_trace_record(uint64_t,uint64_t,uint64_t,uint64_t,unsigned,int,int,uintptr_t){}
'''
code = pre + function('IOReturn ioReturnOf(') + r'''
IOReturn NativePowerVRUserClient::runTail(uint32_t method,void *output){
 currentClient=this;uint64_t args[17]={};NativePowerVRAbiSubmitReply reply={};Shape stored{};Shape *shape=&stored;
 bool submitPerf=method==NativePowerVRMethodSubmitJobs;uint64_t ioStart=0,ioTid=0;unsigned ioPid=0;
''' + tail + r'''
}
int main(){
 NativePowerVRAbiSubmitReply out{};NativePowerVRUserClient c;
 // PTE-like unlock allows close before the failing call finishes.
 c.closeDuringCall=true;c.result=-EIO;
 assert(c.runTail(NativePowerVRMethodSubmitJobs,&out)==kIOReturnIOError&&out.fence[0]==0&&c.calls==1);
 // Close after the protected snapshot cannot mask the returned I/O error.
 c={};c.lock_=&c.lockStorage;c.closeAfterUnlock=true;c.result=-EIO;
 assert(c.runTail(NativePowerVRMethodSubmitJobs,&out)==kIOReturnIOError&&out.fence[0]==0);
 // Other errors retain ordinary close precedence; no writes to the reply.
 c={};c.lock_=&c.lockStorage;c.closeDuringCall=true;c.result=-ENOMEM;
 assert(c.runTail(NativePowerVRMethodSubmitJobs,&out)==kIOReturnNotReady&&out.fence[0]==0);
 c={};c.lock_=&c.lockStorage;c.result=-ENOMEM;assert(c.runTail(NativePowerVRMethodSubmitJobs,&out)==kIOReturnNoMemory&&out.fence[0]==0);
 // A close already present prevents dispatch.
 c={};c.lock_=&c.lockStorage;c.closing_=true;assert(c.runTail(NativePowerVRMethodSubmitJobs,&out)==kIOReturnNotReady&&c.calls==0);
 // Successful completion uses the protected close snapshot and publishes the reply.
 c={};c.lock_=&c.lockStorage;c.closeAfterUnlock=true;assert(c.runTail(NativePowerVRMethodSubmitJobs,&out)==kIOReturnSuccess&&out.fence[0]==99);
 // The -EIO priority is specific to submit; unrelated methods preserve close behavior.
 c={};c.lock_=&c.lockStorage;c.closeDuringCall=true;c.result=-EIO;
 assert(c.runTail(OtherMethod,&out)==kIOReturnNotReady);
}
'''
with tempfile.TemporaryDirectory(prefix='pvr-close-race-') as temp:
    p=Path(temp)
    (p/'test.cpp').write_text(code)
    subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                    '-fno-pie','-no-pie',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
print('PASS actual run tail: close-before/during/after, EIO precedence, reply suppression, other errors; ASan/UBSan')
