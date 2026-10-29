#!/usr/bin/env python3
"""Exercise actual process-log and single all-task worker startup logic."""
from pathlib import Path
import subprocess, tempfile
s=(Path(__file__).resolve().parents[2]/'package/A04BR3Board.cpp').read_text()
a=s.index('static void a04br3_proclog_init(void)');b=s.index('\nstatic void a04br3_cpu_load_thread',a)
pre=r'''
#include <cassert>
#include <map>
#include <string>
using thread_t=int; constexpr int KERN_SUCCESS=0;
std::map<std::string,unsigned> args; unsigned gProcLogSeconds=0, gAllTaskSeconds=0;
int heartbeat=0, all_started=0, all_attempts=0, released=0; bool fail_all=false;
bool PE_parse_boot_argn(const char *n,unsigned *p,unsigned){auto i=args.find(n);if(i==args.end())return false;*p=i->second;return true;}
void procLogThread(){} void allTaskThread(){}
int kernel_thread_start(void(*f)(),void*,thread_t *t){*t=1;if(f==procLogThread){++heartbeat;return 0;} assert(f==allTaskThread);++all_attempts;if(fail_all)return 1;++all_started;return 0;}
void thread_deallocate(thread_t){++released;} void IOLog(const char *, ...){}
'''
post=r'''
int main(){
 args["atp"]=1; a04br3_proclog_init(); assert(!heartbeat && !all_attempts);
 args["a04br3-proclog"]=0; a04br3_proclog_init(); assert(!heartbeat && !all_attempts);
 args["pl"]=1; a04br3_proclog_init(); assert(!heartbeat && !all_attempts); // long form takes precedence
 args.erase("a04br3-proclog"); args["atp"]=2; a04br3_proclog_init(); assert(heartbeat==1 && !all_attempts && gProcLogSeconds==10);
 args["atp"]=1; fail_all=true; a04br3_proclog_init(); assert(heartbeat==2 && all_attempts==1 && !all_started);
 fail_all=false; args["a04br3-proclog"]=~0u / 1000u + 1u; a04br3_proclog_init();
 assert(heartbeat==3 && all_attempts==1 && all_started==0);
 args["a04br3-proclog"]=~0u / 1000u; a04br3_proclog_init();
 assert(heartbeat==4 && all_started==1 && gAllTaskSeconds==~0u / 1000u);
 args["a04br3-proclog"]=60; a04br3_proclog_init();
 assert(heartbeat==5 && all_attempts==2 && all_started==1 && released==6 && gAllTaskSeconds==~0u / 1000u);
}
'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'test.cpp';e=Path(d)/'test';p.write_text(pre+s[a:b]+post);subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(p),'-o',str(e)],check=True);subprocess.run([str(e)],check=True)
print('PASS: heartbeat period, all-task opt-in, single worker and start-failure retry')
