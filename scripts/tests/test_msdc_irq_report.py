#!/usr/bin/env python3
"""Exercise the actual opt-in MSDC IRQ reporter with present/absent weak symbols."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
s = (root / 'package/A04BR3Board.cpp').read_text()
start = s.index('void msdcIrqReport(unsigned enabled)')
end = s.index('\n// Read all counters', start)
body = s[start:end]
worker = s[s.index('void procLogThread('):s.index('\nstatic void a04br3_proclog_init')]
assert 'unsigned perf = 0, rootTiming = 0, msdcPerf = 0;' in worker
assert 'PE_parse_boot_argn("a04br3-msdc-perf", &msdcPerf, sizeof(msdcPerf))' in worker
assert worker.index('if (perf == 1)') < worker.index('msdcIrqReport(msdcPerf)') < worker.index('cpuDiagnosticReport()')
for name in ('native_msdc_irq_stats[4]', 'native_msdc_worker_stats[4]', 'native_msdc_irq_mask_failures'):
    assert name + ' __attribute__((weak_import))' in s
pre = r'''
#include <cassert>
#include <cstdint>
#include <cstdarg>
#include <cstdio>
#include <cstring>
extern "C" {
extern volatile uint64_t native_msdc_irq_stats[4] __attribute__((weak));
extern volatile uint64_t native_msdc_worker_stats[4] __attribute__((weak));
extern volatile uint64_t native_msdc_irq_mask_failures __attribute__((weak));

}
char output[2048];unsigned used=0,lines=0;
void IOLog(const char *fmt,...){va_list ap;va_start(ap,fmt);int n=vsnprintf(output+used,sizeof(output)-used,fmt,ap);va_end(ap);assert(n>=0 && (unsigned)n<sizeof(output)-used);used+=unsigned(n);++lines;}
'''
post = r'''
int main(){
 msdcIrqReport(0);msdcIrqReport(2);assert(lines==0);
 msdcIrqReport(1);assert(lines==3);
#ifdef IRQ_ABSENT
 assert(strstr(output,"a04br3 msdc irq: unavailable"));
#else
 assert(strstr(output,"captured=1 waits=2 no_delivery=3 wait_ns=18446744073709551615 (non-atomic snapshot)"));
#endif
#ifdef WORKER_ABSENT
 assert(strstr(output,"a04br3 msdc worker: unavailable"));
#else
 assert(strstr(output,"queued=11 completed=12 direct_fallback=13 reserved=14 (non-atomic snapshot)"));
#endif
#ifdef MASK_ABSENT
 assert(strstr(output,"mask_failures=unavailable"));
#else
 assert(strstr(output,"mask_failures=7"));
#endif
}
'''
with tempfile.TemporaryDirectory(prefix='msdc-report-') as d:
    src = Path(d) / 'test.cpp'; exe = Path(d) / 'test'
    src.write_text(pre + body + post)
    defs = Path(d) / 'definitions.cpp'
    defs.write_text(r'''
#include <cstdint>
extern "C" {
#ifndef IRQ_ABSENT
volatile uint64_t native_msdc_irq_stats[4] = {1,2,3,18446744073709551615ULL};
#endif
#ifndef WORKER_ABSENT
volatile uint64_t native_msdc_worker_stats[4] = {11,12,13,14};
#endif
#ifndef MASK_ABSENT
volatile uint64_t native_msdc_irq_mask_failures = 7;
#endif
}
''')
    for flags in ([], ['-DIRQ_ABSENT', '-DWORKER_ABSENT', '-DMASK_ABSENT'],
                  ['-DWORKER_ABSENT'], ['-DMASK_ABSENT'], ['-DIRQ_ABSENT']):
        subprocess.run(['c++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                        *flags, str(src), str(defs), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
print('PASS actual reporter: exact opt-in, independent weak-symbol absence, all counters and 64-bit formatting')
