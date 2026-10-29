#!/usr/bin/env python3
"""Exercise the real heartbeat flag decoder and independent iostat report gate."""
from pathlib import Path
import subprocess,tempfile
s=(Path(__file__).resolve().parents[2]/'package/A04BR3Board.cpp').read_text()
a=s.index('void procLogOptions(');b=s.index('void procLogThread(',a);body=s[a:b]
gate=s[s.index('bool procLogMsdcIostatRequested()'):b]
worker=s[b:s.index('\nstatic void a04br3_proclog_init',b)]
assert 'return ioStats == 1;' in gate
assert worker.index('if (perf == 1)') < worker.index('cpuDiagnosticReport()')
assert worker.index('if (perf == 1 || procLogMsdcIostatRequested())') < worker.index('msdcIrqReport(msdcPerf)') < worker.index('msdcIoReport(msdcPerf)')
assert worker.count('msdcIrqReport(msdcPerf)') == 1
assert worker.count('msdcIoReport(msdcPerf)') == 1
pre=r'''
#include <cassert>
#include <cstring>
unsigned home=0,msdc=0,io=0,root=0;
bool PE_parse_boot_argn(const char*n,void*out,unsigned len){assert(len==sizeof(unsigned));
 unsigned value=!strcmp(n,"a04br3-home-perf")?home:!strcmp(n,"a04br3-msdc-perf")?msdc:!strcmp(n,"native-msdc-iostat")?io:root;
 *static_cast<unsigned*>(out)=value;return value!=0;}
'''
post=r'''
int main(){unsigned a=9,b=9,c=9;procLogOptions(a,b,c);assert(a==0&&b==0&&c==0);assert(!procLogMsdcIostatRequested());
 home=1;msdc=1;procLogOptions(a,b,c);assert(a==1&&c==1);
 msdc=0;io=1;procLogOptions(a,b,c);assert(a==1&&c==1);
 home=0;procLogOptions(a,b,c);assert(a==0&&c==1);assert(procLogMsdcIostatRequested());
 home=1;io=2;procLogOptions(a,b,c);assert(a==1&&c==0);
 assert(!procLogMsdcIostatRequested());
 io=0;msdc=2;procLogOptions(a,b,c);assert(c==2); // reporter accepts exactly 1
 io=1;msdc=2;root=1;procLogOptions(a,b,c);assert(a==1&&b==1&&c==1);
}
'''
with tempfile.TemporaryDirectory(prefix='msdc-flags-') as d:
 p=Path(d)/'test.cpp';x=Path(d)/'test';p.write_text(pre+body+post)
 subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(p),'-o',str(x)],check=True)
 subprocess.run([str(x)],check=True)
print('PASS actual heartbeat options: independent flags, exact values, home gate and iostat override')

# Run the real procLogThread body once on the host.  The stubs deliberately
# record only the three reports under review; the second IOSleep returns via
# longjmp so the kernel thread executes exactly one loop iteration.
thread=s.index('void procLogThread(')
thread_end=s.index('\n}\n\nstatic void a04br3_proclog_init',thread)+2
heartbeat=s[thread:thread_end].rsplit('\n}',1)[0]
harness_pre=r'''
#include <cassert>
#include <csetjmp>
#include <cstring>
#include <string>
#include <vector>
using wait_result_t = int;
using uint64_t = unsigned long long;
constexpr unsigned kProcAll = 0;
struct ProcLine { char text[480]; unsigned used; unsigned count; };
unsigned gProcLogSeconds = 1;
unsigned home = 0, msdc = 0, io = 0, root = 0, trace = 0;
std::vector<char> reports;
std::jmp_buf escaped;
unsigned sleeps = 0;
bool PE_parse_boot_argn(const char *name, void *out, unsigned len) {
  assert(len == sizeof(unsigned));
  unsigned value = !std::strcmp(name, "a04br3-home-perf") ? home :
                   !std::strcmp(name, "a04br3-msdc-perf") ? msdc :
                   !std::strcmp(name, "native-msdc-iostat") ? io :
                   !std::strcmp(name, "native-root-timing") ? root :
                   !std::strcmp(name, "native-io-trace") ? trace : 0;
  *static_cast<unsigned *>(out) = value;
  return value != 0;
}
void IOSleep(unsigned) { if (++sleeps == 2) std::longjmp(escaped, 1); }
void IOLog(const char *, ...) {}
void proc_iterate(unsigned, int (*)(void *, void *), void *, int (*)(void *, void *), void *) {}
int procCallout(void *, void *) { return 0; }
void procFlush(ProcLine &) {}
void a04br3_display_report() {}
void a04br3_thread_report() {}
void boot_memory_snapshot(uint64_t out[7]) { for (unsigned i = 0; i < 7; ++i) out[i] = 0; }
struct boot_memory_queue_record { uint64_t throttled, pageable_internal, pageable_external; int compressor_mode; unsigned compressor_active, swap_active; };
void boot_memory_queue_snapshot(boot_memory_queue_record *) {}
void boot_home_perf_sample() {}
void msdcIrqReport(unsigned enabled) { if (enabled == 1) reports.push_back('I'); }
void msdcIoReport(unsigned enabled) { if (enabled == 1) reports.push_back('O'); }
void cpuDiagnosticReport() { reports.push_back('C'); }
void boot_home_irq_report() {}
void boot_live_pc_report() {}
void boot_ipi_reason_report() {}
void boot_ast_caller_report() {}
void native_powervr_submit_perf_report() {}
void native_powervr_io_trace_report() {}
void msdcRootTimingReport(unsigned) {}
void (*native_cs_trace_report)() = nullptr;
void msdcCacheReport() {}
'''
harness_post=r'''
static void heartbeat(unsigned homeValue, unsigned msdcValue, unsigned ioValue, const char *expected) {
  home = homeValue; msdc = msdcValue; io = ioValue; root = trace = 0; sleeps = 0; reports.clear();
  if (setjmp(escaped) == 0) procLogThread(nullptr, 0);
  assert(std::strcmp(expected, std::string(reports.begin(), reports.end()).c_str()) == 0);
}
int main() {
  heartbeat(0, 0, 1, "IO");       // iostat independently enables both MSDC reports
  heartbeat(0, 0, 0, "");         // both flags disabled: neither report executes
  heartbeat(1, 0, 0, "C");        // CPU report only when msdc-perf remains disabled
  heartbeat(1, 1, 0, "IOC");      // exact heartbeat ordering: IRQ, IO, CPU
  heartbeat(0, 0, 2, "");         // iostat uses exact-value opt-in, not truthiness
  heartbeat(0, 2, 1, "IO");       // iostat=1 overrides msdc-perf=2 to enable both
}
'''
with tempfile.TemporaryDirectory(prefix='msdc-heartbeat-') as d:
 p=Path(d)/'test.cpp';x=Path(d)/'test';p.write_text(harness_pre+body+heartbeat+harness_post)
 subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(p),'-o',str(x)],check=True)
 subprocess.run([str(x)],check=True)
print('PASS actual procLogThread heartbeat: independent iostat, disabled flags, exact values, and IRQ/IO/CPU order')
