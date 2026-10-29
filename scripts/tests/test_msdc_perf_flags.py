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
