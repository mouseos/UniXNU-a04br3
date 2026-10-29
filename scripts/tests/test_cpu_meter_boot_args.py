#!/usr/bin/env python3
"""Compile the board's actual clock-meter and PMU selection."""
from pathlib import Path
import subprocess,tempfile
s=(Path(__file__).resolve().parents[2]/'package/A04BR3Board.cpp').read_text();a=s.index('  PE_parse_boot_argn("cm", &gCpuMeterEnabled');b=s.index('  a04br3_proclog_init();',a)
pre='''#include <cassert>\n#include <map>\n#include <string>\nunsigned gCpuMeterEnabled=0;std::map<std::string,unsigned> args;unsigned logs=0;\nbool PE_parse_boot_argn(const char *n,unsigned *p,unsigned){auto i=args.find(n);if(i==args.end())return false;*p=i->second;return true;}\nvoid IOLog(const char *,...){++logs;}\nvoid select(){gCpuMeterEnabled=0;\n'''
post='''}\nint main(){select();assert(!gCpuMeterEnabled);args["cm"]=3;select();assert(gCpuMeterEnabled==3);args["arc"]=1;select();assert(!gCpuMeterEnabled&&logs==1);args["cm"]=1;select();assert(!gCpuMeterEnabled&&logs==2);args["arc"]=0;select();assert(gCpuMeterEnabled==1);args["arc"]=1;select();assert(!gCpuMeterEnabled&&logs==3);args["armcpu-runtime-cycle"]=0;select();assert(gCpuMeterEnabled==1);args["armcpu-runtime-cycle"]=1;select();assert(!gCpuMeterEnabled&&logs==4);}\n'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'test.cpp';e=Path(d)/'test';p.write_text(pre+s[a:b]+post);subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(p),'-o',str(e)],check=True);subprocess.run([str(e)],check=True)
print('PASS: meter opt-in and separate PMU boot')
