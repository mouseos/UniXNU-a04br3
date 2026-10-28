#!/usr/bin/env python3
"""Check actual opt-in and user-client call ordering, not a hardware test."""
from pathlib import Path
p=Path(__file__).resolve().parents[2]/'package/powervr'
jobs=(p/'NativePowerVRJobs.cpp').read_text()
gpu=(p/'NativePowerVRGpu.cpp').read_text()
enable=jobs[jobs.index('extern "C" void native_powervr_io_trace_enable('):jobs.index('extern "C" unsigned native_powervr_io_trace_enabled(')]
record=jobs[jobs.index('extern "C" void native_powervr_io_trace_record('):jobs.index('extern "C" void native_powervr_io_trace_report(')]
assert 'enabled == 2' in enable and '1000000000ull' in enable
assert record.index('native_io_record(')<record.index('native_io_immediate_claim(')<record.index('IOLog(')
assert 'clock=mach_absolute_ns' in record
end=gpu.index('native_powervr_io_trace_record(ioStart,ioLocked')
unlock=gpu.rindex('IOLockUnlock(lock_);',0,end)
lock=gpu.rindex('IOLockLock(lock_);',0,end)
assert lock < unlock < end
wait=gpu.index('native_powervr_fence_wait(jobs, wait.fence')
assert wait < gpu.index('native_powervr_io_trace_record(ioStart,mach_absolute_time()',wait)
print('PASS immediate trace opt-in, clock label and unlocked completion wiring')
