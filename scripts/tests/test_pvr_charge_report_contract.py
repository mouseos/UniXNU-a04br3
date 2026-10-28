#!/usr/bin/env python3
"""Verify diagnostic aliases and the actual trace reporter wiring."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
board=(root/'package/A04BR3Board.cpp').read_text()
charger=(root/'package/A04BR3Charger.cpp').read_text()
thread=board[board.index('void procLogThread('):board.index('static void a04br3_proclog_init(')]
init=board[board.index('static void a04br3_proclog_init('):board.index('static void a04br3_cpu_load_thread(')]
assert 'native_powervr_io_trace_report();' in thread
assert 'PE_parse_boot_argn("a04br3-proclog"' in init and 'kernel_thread_start(procLogThread' in init
assert 'a04br3_diag_arg(PE_parse_boot_argn,"a04br3-gpu-io-trace","a04br3-gio")' in board
assert 'a04br3_diag_arg(PE_parse_boot_argn,"a04br3-gauge-voltage","a04br3-gv")' in charger
code=r'''
#include "A04BR3DiagArgs.h"
#include <cassert>
#include <cstring>
int scenario;
bool parse(const char *key, void *out, unsigned long) {
  if (!std::strcmp(key,"long") && scenario==1) {*(unsigned*)out=0;return true;}
  if (!std::strcmp(key,"long") && scenario==2) {*(unsigned*)out=7;return true;}
  if (!std::strcmp(key,"short") && scenario!=0) {*(unsigned*)out=1;return true;}
  return false;
}
int main() {
 scenario=0;assert(a04br3_diag_arg(parse,"long","short")==0);
 scenario=1;assert(a04br3_diag_arg(parse,"long","short")==0);
 scenario=2;assert(a04br3_diag_arg(parse,"long","short")==7);
 scenario=3;assert(a04br3_diag_arg(parse,"long","short")==1);
}
'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.cpp').write_text(code)
 subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(root/'package'),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print('PASS trace reporter thread and diagnostic alias precedence')
