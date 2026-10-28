#!/usr/bin/env python3
"""Compile the actual optional I/O reporter with missing and present counters."""
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'package/A04BR3Board.cpp').read_text()
a=s.index('void msdcIoReport(unsigned enabled)');b=s.index('\n// Diagnostic sampling only.',a)
body=s[a:b]
worker=s[s.index('void procLogThread('):s.index('\nstatic void a04br3_proclog_init')]
assert worker.index('if (perf == 1)') < worker.index('msdcIoReport(msdcPerf)') < worker.index('cpuDiagnosticReport()')
pre=r'''
#include <cassert>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
extern "C" {
extern volatile uint32_t native_msdc_iostat_enabled __attribute__((weak));
extern volatile uint64_t native_msdc_transport_stats[36] __attribute__((weak));
extern volatile uint64_t native_msdc_block_offer_stats[12] __attribute__((weak));
extern volatile uint64_t native_msdc_block_queue_stats[3] __attribute__((weak));
}
char output[8192];unsigned used=0,lines=0;uint64_t tick=0;
void IOLog(const char *fmt,...){va_list ap;va_start(ap,fmt);int n=vsnprintf(output+used,sizeof(output)-used,fmt,ap);va_end(ap);assert(n>=0&&(unsigned)n<sizeof(output)-used);used+=unsigned(n);++lines;}
uint64_t mach_absolute_time(){return ++tick;}void absolutetime_to_nanoseconds(uint64_t t,uint64_t*out){*out=t*100;}
'''
defs=r'''
#include <cstdint>
extern "C" {
#ifndef ENABLE_ABSENT
#ifdef DISABLED
volatile uint32_t native_msdc_iostat_enabled=0;
#else
volatile uint32_t native_msdc_iostat_enabled=1;
#endif
#endif
#ifndef STATS_ABSENT
volatile uint64_t native_msdc_transport_stats[36]={1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36};
volatile uint64_t native_msdc_block_offer_stats[12]={11,12,13,14,15,16,17,18,19,20,21,22};
volatile uint64_t native_msdc_block_queue_stats[3]={21,22,23};
#endif
}
'''
post=r'''
int main(){msdcIoReport(0);msdcIoReport(2);assert(lines==0);msdcIoReport(1);
#ifdef ENABLE_ABSENT
 assert(lines==1&&strstr(output,"io: unavailable"));
#elif defined(DISABLED)
 assert(lines==1&&strstr(output,"io: disabled"));
#elif defined(STATS_ABSENT)
 assert(lines==5&&strstr(output,"seq=1 begin_ns=100 end_ns=200 non_atomic=1")&&strstr(output,"transfer seq=1 unavailable")&&strstr(output,"offered seq=1 unavailable")&&strstr(output,"queue seq=1 unavailable"));
#else
 assert(lines==18&&strstr(output,"seq=1 begin_ns=100 end_ns=200 non_atomic=1"));
 assert(strstr(output,"mode=dma_read size=le4k calls=1 ok_bytes=2 errors=3 elapsed_ns=4"));
 assert(strstr(output,"mode=pio_read size=4k_16k calls=17 ok_bytes=18 errors=19 elapsed_ns=20"));
 assert(strstr(output,"mode=write size=gt16k calls=33 ok_bytes=34 errors=35 elapsed_ns=36"));
 assert(strstr(output,"offered seq=1 mode=read size=le4k calls=11 bytes=12"));
 assert(strstr(output,"offered seq=1 mode=write size=gt16k calls=21 bytes=22"));
 assert(strstr(output,"queue seq=1 started=21 wait_ns=22 max_wait_ns=23"));
 assert(strstr(output,"io end seq=1 rows=16"));
 used=lines=0;output[0]=0;msdcIoReport(1);
 assert(lines==18 && strstr(output,"seq=2 begin_ns=300 end_ns=400 non_atomic=1"));
 assert(strstr(output,"transfer seq=2 mode=dma_read size=le4k") && strstr(output,"io end seq=2 rows=16"));
#endif
}
'''
with tempfile.TemporaryDirectory(prefix='msdc-io-report-') as d:
 p=Path(d)/'test.cpp';x=Path(d)/'test';q=Path(d)/'defs.cpp';p.write_text(pre+body+post);q.write_text(defs)
 for flags in ([],['-DENABLE_ABSENT','-DSTATS_ABSENT'],['-DDISABLED'],['-DSTATS_ABSENT']):
  subprocess.run(['c++','-std=c++17','-O2','-Wall','-Wextra','-Werror',*flags,str(p),str(q),'-o',str(x)],check=True)
  subprocess.run([str(x)],check=True)
print('PASS actual iostat reporter: opt-in, no symbols, disabled, bins, queue and snapshot brackets')
