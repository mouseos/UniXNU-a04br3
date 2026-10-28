#!/usr/bin/env python3
from pathlib import Path
p=Path(__file__).resolve().parents[2]/'package/powervr'
gpu=(p/'NativePowerVRGpu.cpp').read_text();jobs=(p/'NativePowerVRJobs.cpp').read_text()
a=gpu.index('native_powervr_wait_series_begin(ioPid,ioTid,wait.fence,reinterpret_cast<uintptr_t>(jobs))')
b=gpu.index('const int err = native_powervr_fence_wait(jobs, wait.fence',a)
c=gpu.index('native_powervr_io_trace_record(ioStart,mach_absolute_time()',b)
assert a<b<c and 'ioStart=mach_absolute_time()' in gpu[a:b]
assert gpu.rindex('IOLockUnlock(lock_);',0,a) > gpu.rindex('IOLockLock(lock_);',0,a)
assert 'enabled == 3' in jobs and '1000000000ull' in jobs and '10000000000ull' in jobs
assert 'native_wait_end(&gNativeWaitSeries' in jobs and 'clock=mach_absolute_ns' in jobs
monitor=jobs[jobs.index('static void native_wait_monitor('):jobs.index('extern "C" void native_powervr_io_trace_enable(')]
assert 'IOSleep(250)' in monitor and 'native_wait_active(' in monitor and 'gpu wait active:' in monitor
assert 'enabled==4' in jobs and 'monitor unavailable' in jobs
print('PASS begin-before-wait, timing excludes begin log, bounded mode and completed-series wiring')
