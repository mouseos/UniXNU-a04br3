#!/usr/bin/env python3
"""Default build (no NativeMsdc IRQ wait, no I/O overlap trace): the board reporters must link without the diagnostic
symbols. The kernel is a static link, where an unresolved weak_import reference is an error."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
s = (root / 'package/A04BR3Board.cpp').read_text()
decl = s[s.index('#if !defined(A04BR3_MSDC_IRQ_SYMBOLS) || A04BR3_MSDC_IRQ_SYMBOLS'):]
decl = decl[:decl.index('#endif') + len('#endif')]
trace = s[s.index('#if !defined(A04BR3_CS_TRACE_SYMBOL) || A04BR3_CS_TRACE_SYMBOL'):]
trace = trace[:trace.index('#endif') + len('#endif')]
absent = s[s.index('#if defined(A04BR3_MSDC_IRQ_SYMBOLS) && !A04BR3_MSDC_IRQ_SYMBOLS'):]
absent = absent[:absent.index('#define native_cs_trace_report a04br3_absent_cs_trace_report')] + '#define native_cs_trace_report a04br3_absent_cs_trace_report\n#endif\n'
body = s[s.index('void msdcIrqReport(unsigned enabled)'):s.index('\n// Read all counters')]
thread = s[s.index('void procLogThread('):s.index('\nstatic void a04br3_proclog_init')]
for line in ('if (overlapTrace == 1 && !native_cs_trace_report) IOLog("CS overlap unavailable: kernel trace not built\\n");',
             'if (overlapTrace == 1 && native_cs_trace_report) native_cs_trace_report();'):
    assert line in thread, line
board = (root / 'scripts/build-a04br3-board.py').read_text()
assert "msdc_irq = os.environ.get('XNU_MSDC_IRQ_WAIT') == '1'" in board
assert "cs_trace = os.environ.get('XNU_IO_OVERLAP_TRACE') == '1' and os.environ.get('XNU_ROOT_TIMING') == '1'" in board
assert "f'-DA04BR3_MSDC_IRQ_SYMBOLS={int(msdc_irq)}', f'-DA04BR3_CS_TRACE_SYMBOL={int(cs_trace)}'" in board
harness = r'''
#include <cassert>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
char output[1024]; unsigned used = 0;
void IOLog(const char *fmt, ...) { va_list a; va_start(a, fmt); used += vsnprintf(output + used, sizeof(output) - used, fmt, a); va_end(a); }
extern "C" {
''' + decl + '\n' + trace + '''
}
''' + absent + body + r'''
void traceOnce(unsigned overlapTrace) {
  if (overlapTrace == 1 && !native_cs_trace_report) IOLog("CS overlap unavailable: kernel trace not built\n");
  if (overlapTrace == 1 && native_cs_trace_report) native_cs_trace_report();
}
int main() {
  msdcIrqReport(1); traceOnce(1);
  assert(strstr(output, "a04br3 msdc irq: unavailable"));
  assert(strstr(output, "a04br3 msdc worker: unavailable"));
  assert(strstr(output, "mask_failures=unavailable"));
  assert(strstr(output, "CS overlap unavailable: kernel trace not built"));
}
'''
with tempfile.TemporaryDirectory(prefix='board-default-link-') as d:
    src = Path(d) / 'test.cpp'; exe = Path(d) / 'test'
    src.write_text(harness)
    # No definitions of the diagnostic symbols are linked: any remaining reference fails here, as in the kernel link.
    subprocess.run(['c++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-DA04BR3_MSDC_IRQ_SYMBOLS=0',
                    '-DA04BR3_CS_TRACE_SYMBOL=0', str(src), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
    nm = subprocess.run(['nm', '-u', str(exe)], check=True, capture_output=True, text=True).stdout
    for name in ('native_msdc_irq_stats', 'native_msdc_worker_stats', 'native_msdc_irq_mask_failures', 'native_cs_trace_report'):
        assert name not in nm, name
print('PASS default build: no diagnostic symbol references, reporters print unavailable, build switches mapped')
