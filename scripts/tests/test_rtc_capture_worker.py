#!/usr/bin/env python3
"""Compile the actual capture worker with host hardware and scheduler mocks."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'package/A04BR3AutoReset.cpp').read_text()
a=s.index('struct RtcCaptureIO {'); b=s.index('\n#endif\n\nvoid cycleThread',a)
probe=s[s.index('static bool a04br3_pmu_gate_lock_bounded(void)'):s.index('// The lock coordinates known board WACS2 users')]
probe=probe[:probe.index('extern "C" bool a04br3_pmu_lease_acquire')] + probe[probe.index('extern "C" bool a04br3_runtime_probe_begin'): ]
s=s[a:b]
# Inject temporary gate contention at the actual bounded retry call.
s=s.replace("gateLocked=gWatchdogGate.lock()", "gateLocked=test_gate_lock()")
original='uint64_t timer() { uint64_t v; __asm__ volatile("isb\\n\\tmrs %0, cntvct_el0" : "=r"(v) :: "memory"); return v; }'
assert s.count(original)==1
s=s.replace(original,'uint64_t timer() { return mockTimer(); }')
pre=r'''
#include <cassert>
#include <cstdarg>
#include <cstring>
#include "A04BR3RtcWacs.h"
#include "A04BR3RtcReference.h"
#include "A04BR3WatchdogGate.h"
static A04BR3WatchdogGate gWatchdogGate={}; static uint32_t gRtcCaptureActive;
using wait_result_t=int;
static unsigned commands=0, clears=0, slept=0, rows=0, finished=0, expectedValid=1, expectedCount=64, failAt=0, panicAt=0, fault=0, stateAt=0, summaries=0, gateFailures=0, gateAttempts=0, releaseFailures=0, retrySleeps=0, unavailable=0, panicOnAcquire=0, manualPanic=0;
static bool held=false, issued=false, cleared=false;
static bool test_gate_lock(){++gateAttempts;if(gateFailures){--gateFailures;return false;}bool ok=gWatchdogGate.lock();if(ok && !gRtcCaptureActive && panicOnAcquire)manualPanic=1;return ok;}
struct MockLease {uint32_t state;}; static MockLease gRuntimePmuLease={};
extern "C" bool a04br3_runtime_probe_end_checked(void);
extern "C" bool a04br3_runtime_probe_begin(void);
static volatile uint8_t *gPwrap=nullptr;
static volatile uint32_t a04br3_runtime_probe_active=0,a04br3_runtime_recovery_ready=1;
static constexpr unsigned kWacsRdata=0xa4,kWacsCmd=0xa0,kWacsVldclr=0xa8;
static unsigned transientContention=0;
static bool a04br3_pwrap_try_lock(){if(held || (fault==1 && commands==63))return false;if(fault==9 && commands==10 && transientContention<5){++transientContention;return false;}held=true;return true;}
static void a04br3_pwrap_unlock(){assert(held);held=false;}
static uint32_t rd(volatile uint8_t *,unsigned){assert(held);if(!issued || cleared){if(fault==2 && commands==63 && !issued)return (1u<<21)|(6u<<16); if(fault==3 && commands==64 && cleared)return (1u<<21)|(6u<<16); return 1u<<21;}if(failAt && commands==failAt)return 1u<<21;if(fault==4 && commands==64)return (1u<<21)|(6u<<16)|60u;return (1u<<21)|(6u<<16)|(fault==7?0:((commands-1)/20));}
static void wr(volatile uint8_t *,unsigned off,uint32_t v){assert(held);if(off==kWacsCmd){assert((v>>31)==0 && ((v>>16)&0x7fffU)*2U==0x800aU && (v&0xffffU)==0);++commands;issued=true;cleared=false;}else{assert(off==kWacsVldclr && v==1);++clears;cleared=true;}}
static uint64_t ticks=0; static uint64_t mockTimer(){return ticks+=10;}
static unsigned panic_active(){return manualPanic || (panicAt && slept>=panicAt);}
static void IOSleep(unsigned x){if(x==1){++retrySleeps;return;}assert(x==50 && !held);++slept;if(slept==64)gateFailures=releaseFailures;if(stateAt && slept==stateAt){if(fault==8)assert(gWatchdogGate.lock());if(fault==5){assert(!a04br3_runtime_probe_begin());assert(!a04br3_runtime_probe_end_checked());bool kicked=false;assert(gWatchdogGate.feed(&a04br3_runtime_probe_active,true,[&]{kicked=true;}));assert(kicked);}if(fault==6)a04br3_runtime_recovery_ready=0;}issued=false;}
static int current_thread(){return 1;} static void thread_terminate(int){++finished;}
static void IOLog(const char *fmt,...){va_list ap;va_start(ap,fmt);if(strstr(fmt,"samples=%u")){++summaries;unsigned count=va_arg(ap,unsigned);unsigned valid=va_arg(ap,unsigned);assert(valid==expectedValid);assert(count==expectedCount);unsigned reason=va_arg(ap,unsigned);unsigned released=va_arg(ap,unsigned);assert(released==unsigned(!gRtcCaptureActive));(void)reason;}else if(strstr(fmt,"unavailable=")){++unavailable;}else {assert(strstr(fmt,"rtc-ref-row"));++rows;}va_end(ap);}
'''
main=r'''
static void reset(){commands=clears=slept=rows=finished=summaries=retrySleeps=unavailable=releaseFailures=panicOnAcquire=manualPanic=0;held=issued=cleared=false;failAt=panicAt=fault=stateAt=gateFailures=gateAttempts=0;expectedCount=64;expectedValid=0;a04br3_runtime_probe_active=0;gRtcCaptureActive=0;gWatchdogGate.busy=0;a04br3_runtime_recovery_ready=1;}
int main(){
 reset();expectedValid=1;rtcCaptureThread(nullptr,0);assert(commands==64 && clears==64 && rows==64 && summaries==1 && finished==1);
 reset();failAt=1;expectedCount=1;rtcCaptureThread(nullptr,0);assert(commands==1 && clears==0 && rows==1 && held && summaries==1 && !a04br3_runtime_probe_active && !gRtcCaptureActive);
 reset();failAt=64;rtcCaptureThread(nullptr,0);assert(commands==64 && clears==63 && rows==64 && held && summaries==1 && !a04br3_runtime_probe_active && !gRtcCaptureActive);
 // fault 9: five transient contentions before the 11th read are retried; the capture completes with 64 rows.
 reset();fault=9;transientContention=0;expectedValid=1;rtcCaptureThread(nullptr,0);assert(transientContention==5 && rows==64 && commands==64 && summaries==1 && !held && !a04br3_runtime_probe_active && !gRtcCaptureActive);
 for(unsigned kind=1;kind<=4;kind++){reset();fault=kind;rtcCaptureThread(nullptr,0);assert(rows==64 && summaries==1 && finished==1);if(kind==3)assert(held && !a04br3_runtime_probe_active && !gRtcCaptureActive);else assert(!held && !a04br3_runtime_probe_active);}
 reset();fault=7;rtcCaptureThread(nullptr,0);assert(rows==64 && summaries==1 && !a04br3_runtime_probe_active && !gRtcCaptureActive);
 reset();gateFailures=3;expectedValid=1;rtcCaptureThread(nullptr,0);assert(gateAttempts==4 && retrySleeps==3 && !a04br3_runtime_probe_active && !gRtcCaptureActive && summaries==1);
 reset();panicOnAcquire=1;rtcCaptureThread(nullptr,0);assert(!commands && !summaries && !unavailable && !gRtcCaptureActive && !a04br3_runtime_probe_active && finished==1);
 reset();gateFailures=100;rtcCaptureThread(nullptr,0);assert(gateAttempts==100 && retrySleeps==100 && unavailable==1 && !a04br3_runtime_probe_active);
 reset();fault=8;stateAt=64;expectedValid=1;rtcCaptureThread(nullptr,0);assert(!gRtcCaptureActive && summaries==1);assert(!a04br3_runtime_probe_begin());gWatchdogGate.unlock();assert(a04br3_runtime_probe_begin());assert(a04br3_runtime_probe_end_checked());
 reset();fault=5;stateAt=64;expectedValid=1;rtcCaptureThread(nullptr,0);assert(rows==64 && summaries==1 && !a04br3_runtime_probe_active);
 reset();fault=6;stateAt=64;rtcCaptureThread(nullptr,0);assert(rows==64 && summaries==1 && !a04br3_runtime_probe_active && !gRtcCaptureActive);
 reset();panicAt=64;rtcCaptureThread(nullptr,0);assert(rows==64 && summaries==0 && finished==1 && !a04br3_runtime_probe_active && !gRtcCaptureActive);
 reset();a04br3_runtime_probe_active=1;rtcCaptureThread(nullptr,0);assert(!commands && !summaries && unavailable==1 && finished==1 && !gRtcCaptureActive);
 reset();panicAt=1;rtcCaptureThread(nullptr,0);assert(rows==1 && summaries==0 && finished==1 && !a04br3_runtime_probe_active && !gRtcCaptureActive);
}
'''
with tempfile.TemporaryDirectory(prefix='rtc-worker-') as d:
 cpp=Path(d)/'worker.cpp';exe=Path(d)/'worker';cpp.write_text(pre+s+probe+main)
 subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined','-I',str(root/'package'),str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
print('PASS actual RTC capture worker: 64 samples, raw rows, incomplete timeout and retained shared lock; hardware mocked')
