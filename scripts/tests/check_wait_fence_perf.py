#!/usr/bin/env python3
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2];jobs=(root/'package/powervr/NativePowerVRJobs.cpp').read_text();gpu=(root/'package/powervr/NativePowerVRGpu.cpp').read_text()
a=jobs.index('enum PerfStage {');b=jobs.index('bool queueReached',a);core=jobs[a:b]
a=jobs.index('extern "C" unsigned native_powervr_wait_perf_enabled');b=jobs.index('extern "C" void native_powervr_submit_perf_report',a);api=jobs[a:b]
wait=gpu[gpu.index('  if (method == NativePowerVRMethodWaitFence) {'):gpu.index('  // The largest record',gpu.index('  if (method == NativePowerVRMethodWaitFence) {'))]
assert wait.index('native_powervr_wait_perf_enabled()')<wait.index('proc_selfname(')<wait.index('native_powervr_fence_wait(')<wait.index('native_powervr_wait_perf_end(')
assert 'if (waitPerf) native_powervr_wait_perf_end' in wait
source='''#include <cstdint>\n#include <cassert>\n#include <cerrno>\nstatic uint64_t tick=10;static uint64_t mach_absolute_time(){return tick++;}\n'''+core+api+'''\nint main(){
 assert(!native_powervr_wait_perf_enabled());assert(!native_powervr_wait_perf_begin(0));native_powervr_wait_perf_end(0,0,0);assert(!gWaitFenceResult[0][0]);
 gSubmitPerfEnabled=1;assert(native_powervr_wait_perf_enabled());
 auto a=native_powervr_wait_perf_begin(0);native_powervr_wait_perf_end(0,a,0);
 auto b=native_powervr_wait_perf_begin(1);native_powervr_wait_perf_end(1,b,-ETIMEDOUT);
 auto c=native_powervr_wait_perf_begin(1);native_powervr_wait_perf_end(1,c,-EINVAL);
 assert(gWaitFenceResult[0][0]==1 && gWaitFenceResult[1][1]==1 && gWaitFenceResult[1][2]==1);
 assert(gSubmitPerf[PerfWaitFence].entered==1 && gSubmitPerf[PerfWaitFence].finished==1);
 assert(gSubmitPerf[PerfWaitFenceBackboard].entered==2 && gSubmitPerf[PerfWaitFenceBackboard].finished==2);
 assert(gSubmitPerf[PerfWaitFence].ticks==1 && gSubmitPerf[PerfWaitFenceBackboard].ticks==2);
}\n'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.cpp').write_text(source)
 subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print('PASS production fence perf: default off, successful, timeout, other, scope, entry/completion, elapsed')
