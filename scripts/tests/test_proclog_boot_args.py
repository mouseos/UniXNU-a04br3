#!/usr/bin/env python3
"""Exercise the board's actual process-log boot-argument selection."""
from pathlib import Path
import subprocess,tempfile
s=(Path(__file__).resolve().parents[2]/'package/A04BR3Board.cpp').read_text();a=s.index('static void a04br3_proclog_init(void)');b=s.index('\nstatic void a04br3_cpu_load_thread',a)
pre='''#include <cassert>\n#include <map>\n#include <string>\nusing thread_t=int;constexpr int KERN_SUCCESS=0;std::map<std::string,unsigned> args;unsigned gProcLogSeconds=0;int started=0;\nbool PE_parse_boot_argn(const char *n,unsigned *p,unsigned){auto i=args.find(n);if(i==args.end())return false;*p=i->second;return true;}\nvoid procLogThread(){}\nint kernel_thread_start(void(*)(),void*,thread_t *t){++started;*t=1;return 0;}\nvoid thread_deallocate(thread_t){}\n'''
post='''int main(){a04br3_proclog_init();assert(!started);args["pl"]=1;a04br3_proclog_init();assert(started==1&&gProcLogSeconds==10);args["a04br3-proclog"]=3;a04br3_proclog_init();assert(started==2&&gProcLogSeconds==3);args["a04br3-proclog"]=0;a04br3_proclog_init();assert(started==2);}\n'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'test.cpp';e=Path(d)/'test';p.write_text(pre+s[a:b]+post);subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(p),'-o',str(e)],check=True);subprocess.run([str(e)],check=True)
print('PASS: short process-log period and long-form precedence')
