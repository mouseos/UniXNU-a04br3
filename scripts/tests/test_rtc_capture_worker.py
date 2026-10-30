#!/usr/bin/env python3
"""Compile the actual capture worker with host hardware and scheduler mocks."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'package/A04BR3AutoReset.cpp').read_text()
a=s.index('struct RtcCaptureIO {'); b=s.index('\n#endif\n\nvoid cycleThread',a)
s=s[a:b]
original='uint64_t timer() { uint64_t v; __asm__ volatile("isb\\n\\tmrs %0, cntvct_el0" : "=r"(v) :: "memory"); return v; }'
assert s.count(original)==1
s=s.replace(original,'uint64_t timer() { return mockTimer(); }')
pre=r'''
#include <cassert>
#include <cstdarg>
#include <cstring>
#include "A04BR3RtcWacs.h"
#include "A04BR3RtcReference.h"
using wait_result_t=int;
static unsigned commands=0, clears=0, slept=0, rows=0, finished=0, expectedValid=1, expectedCount=64, failAt=0, panicAt=0;
static bool held=false, issued=false, cleared=false;
static volatile uint8_t *gPwrap=nullptr;
static volatile uint32_t a04br3_runtime_probe_active=0,a04br3_runtime_recovery_ready=1;
static constexpr unsigned kWacsRdata=0xa4,kWacsCmd=0xa0,kWacsVldclr=0xa8;
static bool a04br3_pwrap_try_lock(){if(held)return false;held=true;return true;}
static void a04br3_pwrap_unlock(){assert(held);held=false;}
static uint32_t rd(volatile uint8_t *,unsigned){assert(held);if(!issued || cleared)return 1u<<21;if(failAt && commands==failAt)return 1u<<21;return (1u<<21)|(6u<<16)|((commands-1)/20);}
static void wr(volatile uint8_t *,unsigned off,uint32_t v){assert(held);if(off==kWacsCmd){assert((v>>31)==0 && ((v>>16)&0x7fffU)*2U==0x800aU && (v&0xffffU)==0);++commands;issued=true;cleared=false;}else{assert(off==kWacsVldclr && v==1);++clears;cleared=true;}}
static uint64_t ticks=0; static uint64_t mockTimer(){return ticks+=10;}
static unsigned panic_active(){return panicAt && slept>=panicAt;}
static void IOSleep(unsigned x){assert(x==50 && !held);++slept;}
static int current_thread(){return 1;} static void thread_terminate(int){++finished;}
static void IOLog(const char *fmt,...){va_list ap;va_start(ap,fmt);if(strstr(fmt,"samples=%u")){unsigned count=va_arg(ap,unsigned);unsigned valid=va_arg(ap,unsigned);assert(valid==expectedValid);assert(count==expectedCount);}else {assert(strstr(fmt,"rtc-ref-row"));++rows;}va_end(ap);}
'''
main=r'''
int main(){rtcCaptureThread(nullptr,0);assert(commands==64 && clears==64 && slept==64 && rows==64 && !held && finished==1);
commands=clears=slept=rows=finished=0;issued=cleared=false;failAt=1;expectedCount=1;expectedValid=0;
rtcCaptureThread(nullptr,0);assert(commands==1 && clears==0 && slept==0 && rows==1 && held && finished==1);
held=false;commands=clears=slept=rows=finished=0;issued=cleared=false;failAt=64;expectedCount=64;
rtcCaptureThread(nullptr,0);assert(commands==64 && clears==63 && slept==63 && rows==64 && held && finished==1);
held=false;commands=clears=slept=rows=finished=0;issued=cleared=false;failAt=0;panicAt=64;
rtcCaptureThread(nullptr,0);assert(commands==64 && clears==64 && slept==64 && rows==64 && !held && finished==1);
}
'''
with tempfile.TemporaryDirectory(prefix='rtc-worker-') as d:
 cpp=Path(d)/'worker.cpp';exe=Path(d)/'worker';cpp.write_text(pre+s+main)
 subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined','-I',str(root/'package'),str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
print('PASS actual RTC capture worker: 64 samples, raw rows, incomplete timeout and retained shared lock; hardware mocked')
