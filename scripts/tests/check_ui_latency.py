#!/usr/bin/env python3
"""Actual input edge hooks and generic default-null GOP observer, no device I/O."""
from pathlib import Path
import subprocess,tempfile,runpy,sys,os
r=Path(__file__).resolve().parents[2]
s=(r/'package/A04BR3Input.cpp').read_text()
publish=s[s.index('void publish(bool'):s.index('\nvoid touchThread(')].replace('__asm__ volatile("dmb ish" ::: "memory");','')
hook=s[s.index('static void uiLatency('):s.index('static void uiLatencyThread(')]
source=r'''
#include "A04BR3UiLatencyTrace.h"
#include <cassert>
#include <mutex>
static bool locked=false,gUiLatencyEnabled=false;static unsigned clocks=0;
static uilatency::Trace gUiLatency;
static uint64_t mach_absolute_time(){return ++clocks;}
HOOK
using IOLock=std::mutex;static IOLock lock;static IOLock *gPublishLock=&lock;
static void IOLockLock(IOLock *p){p->lock();locked=true;}
static void IOLockUnlock(IOLock *p){locked=false;p->unlock();}
static volatile uint32_t native_touch_seq=0,native_touch_count=0;
static volatile int native_touch_ids[12],native_touch_xs[12],native_touch_ys[12];
static void native_touch_published(){assert(!locked);clocks+=10;}
PUBLISH
int main(){
uilatency::Event e;publish(true,10,20);assert(!gUiLatency.pop(e)&&clocks==10);
gUiLatencyEnabled=true;publish(false,10,20);
assert(gUiLatency.pop(e)&&e.kind==2&&e.id==4&&e.tick==11);
assert(gUiLatency.pop(e)&&e.kind==4&&e.id==4&&e.tick==22);
publish(false,10,20);assert(!gUiLatency.pop(e));

}
'''.replace('HOOK',hook).replace('PUBLISH',publish)
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.cpp').write_text(source)
 subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(r/'package'),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
assert 'latency==1' in s and '"native-ui-latency"' in s
print('PASS actual input edge timestamps, no move event, default-null resident observer, one-time registration, completion ordering')

# Execute the offline analyzer with deliberately reordered UART emission.
module=runpy.run_path(str(r/'scripts/analyze-a04br3-ui-latency.py'));analyze=module['analyze']
raw=b"Darwin Kernel Version test\nUIK clock n1 d1\nUIL p56 clock n1 d1\nUIK e33 i1 t989680\nUIK e1 i2 t0\nUIL p56 e11 i3 t2dc6c0\nUIL p56 e10 i3 tf4240\nUIK e30 i1 t4c4b40\nUIK e31 i1 t5b8d80\nUIK e32 i1 t895440\nUIK e3 i2 t7a120\nUIL p56 e42 i1 t895440\nUIL p56 e43 i1 t895440\n"
a=analyze(raw);assert a['events'][0]['kind']==1 and not a['ui_completion_proven']
assert next(x['ms'] for x in a['intervals'] if x['label']=='offer_to_legacy_return')==5
assert a['temporal_candidates'][0]['temporal_gap_ms_bounds']==[1,1] and not a['temporal_candidates'][0]['causal_link_proven']
assert not analyze(raw.replace(b'UIK clock n1 d1\n',b''))['clock_calibration']['available']
assert analyze(raw+b'UIK lost 2\n')['incomplete_or_dropped']
for invalid in [raw+b'Darwin Kernel Version second\n',raw.replace(b'n1 d1',b'n1 d0')]:
 try:analyze(invalid)
 except ValueError:pass
 else:raise AssertionError('accepted ambiguous epoch/timebase')
print('PASS offline analyzer epoch/timebase guards, UART reordering, scoped intervals, missing/temporal-vs-causal evidence')
