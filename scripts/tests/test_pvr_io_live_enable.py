#!/usr/bin/env python3
"""実際の診断初期化コードをホストで実行し、各モードの接続を確認する。"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'package/powervr/NativePowerVRJobs.cpp').read_text()
begin = source.index('extern "C" void native_powervr_io_trace_enable(')
end = source.index('extern "C" unsigned native_powervr_io_trace_enabled(', begin)
code = r'''
#include "NativePowerVRIoTrace.h"
#include "NativePowerVRWaitSeries.h"
#include "NativePowerVRWaitReason.h"
#include <cassert>
#include <cstdint>
using thread_t=void *;
constexpr int KERN_SUCCESS=0;
NativeIoTrace gNativeIoTrace{};
NativeWaitSeries gNativeWaitSeries{};
NativeWaitReasonLimit gNativeWaitReason{};
unsigned gWaitMonitorStarted=0,gIoLiveEnabled=0,starts=0;
void nanoseconds_to_absolutetime(uint64_t n,uint64_t *out){*out=n;}
void native_wait_monitor(void*,int){}
int kernel_thread_start(void(*)(void*,int),void*,thread_t *out){++starts;*out=(void*)1;return 0;}
void thread_deallocate(thread_t){}
void IOLog(const char*,...){}
''' + source[begin:end] + r'''
int main(){
 for(unsigned mode=0;mode<=7;++mode){
  gNativeIoTrace={};gNativeWaitSeries={};gNativeWaitReason={};gWaitMonitorStarted=0;gIoLiveEnabled=0;starts=0;
  native_powervr_io_trace_enable(mode);
  assert(gNativeIoTrace.enabled==(mode>=1 && mode<=6));
  assert(gNativeIoTrace.immediate_enabled==(mode>=2 && mode<=6));
  assert(gNativeWaitSeries.enabled==(mode>=3 && mode<=6));
  assert(gIoLiveEnabled==(mode==6));
  assert(starts==(mode>=4 && mode<=6));
  if(mode==5 || mode==6){assert(gNativeWaitSeries.mode==5);assert(gNativeWaitReason.minTicks==50000000ULL);assert(gNativeWaitReason.perFenceTicks==500000000ULL);}
  if(mode==4)assert(gNativeWaitSeries.mode==4);
  if(mode==3)assert(gNativeWaitSeries.mode==3);
 }
}
'''
with tempfile.TemporaryDirectory() as tmp:
    cpp = Path(tmp) / 'enable.cpp'
    exe = Path(tmp) / 'enable'
    cpp.write_text(code)
    subprocess.run(['/usr/bin/g++', '-std=c++17', '-O1', '-fsanitize=address,undefined',
                    '-I', str(root / 'package/powervr'), str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS modes 0..7 actual enable wiring and monitor startup')
