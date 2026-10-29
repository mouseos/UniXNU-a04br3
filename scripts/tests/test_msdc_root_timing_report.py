#!/usr/bin/env python3
"""Run the board's actual reporter with mocked clock and snapshot; no device or XNU runtime."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'package/A04BR3Board.cpp').read_text()
start = src.index('void msdcRootTimingReport(')
opening = src.index('{', start)
depth, end = 1, opening + 1
while depth:
    depth += (src[end] == '{') - (src[end] == '}')
    end += 1
function = src[start:end]
thread = src[src.index('void procLogThread('):src.index('static void a04br3_proclog_init')]
options = src[src.index('void procLogOptions('):src.index('void procLogThread(')]
assert 'PE_parse_boot_argn("native-root-timing", &rootTiming, sizeof(rootTiming))' in options
assert 'PE_parse_boot_argn("native-io-trace", &overlapTrace, sizeof(overlapTrace))' in thread
assert 'if (overlapTrace == 1 && native_cs_trace_report) native_cs_trace_report();' in thread
assert 'CS overlap unavailable: kernel trace not built' in thread
assert 'msdcRootTimingReport(rootTiming)' in thread
assert 'native_root_timing_snapshot' not in src[src.index('void unixnu_board_init('):]

harness = r'''
#include <cassert>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
static unsigned calls=0, clocks=0, logs=0;
static char line[512];
void sample(uint64_t out[6]) { ++calls; for(unsigned i=0;i<6;i++) out[i]=i+11; }
void (*native_root_timing_snapshot)(uint64_t[6])=nullptr;
uint64_t mach_absolute_time() { return ++clocks; }
void absolutetime_to_nanoseconds(uint64_t t,uint64_t *out) { *out=t*100; }
void IOLog(const char *fmt,...) { ++logs; va_list a; va_start(a,fmt); vsnprintf(line,sizeof(line),fmt,a); va_end(a); }
''' + function + r'''
int main() {
  msdcRootTimingReport(0); msdcRootTimingReport(1); msdcRootTimingReport(2);
  assert(!calls && !clocks && !logs);
  native_root_timing_snapshot=sample;
  msdcRootTimingReport(0); msdcRootTimingReport(2);
  assert(!calls && !clocks && !logs);
  msdcRootTimingReport(1);
  assert(calls==1 && clocks==2 && logs==1);
  assert(!strcmp(line,"a04br3 msdc root timing: begin_ns=100 end_ns=200 read_calls=11 read_bytes=12 read_ns=13 verify_calls=14 verify_bytes=15 verify_ns=16\n"));
  msdcRootTimingReport(1);
  assert(calls==2 && clocks==4 && logs==2);
  puts("PASS actual reporter: opt-in, absent weak symbol, snapshot, timestamps and all six fields");
}
'''
with tempfile.TemporaryDirectory(prefix='msdc-report-') as d:
    src_path, exe = Path(d) / 'fixture.cpp', Path(d) / 'fixture'
    src_path.write_text(harness)
    subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-fno-pie', '-no-pie', str(src_path), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True, timeout=20)
