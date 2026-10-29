#!/usr/bin/env python3
"""Compile the actual board argument selection, including explicit zero precedence."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'package/A04BR3Board.cpp').read_text()
a=s.index('  unsigned gpuIoTrace = 0;');b=s.index('  native_powervr_submit_stage_enable(',a)
pre='''#include <cassert>\n#include <map>\n#include <string>\n#include "A04BR3DiagArgs.h"\nstd::map<std::string,unsigned> args;\nbool PE_parse_boot_argn(const char *n, void *out, unsigned){auto i=args.find(n);if(i==args.end())return false;*static_cast<unsigned*>(out)=i->second;return true;}\nvoid check(unsigned expectedIo,unsigned expectedStage){\n'''
post='''assert(gpuIoTrace==expectedIo && gpuStage==expectedStage);}\nint main(){check(0,0);args["gi"]=6;args["gs"]=1;check(6,1);args["a04br3-gio"]=0;args["a04br3-gpu-stage"]=0;check(0,0);args["a04br3-gpu-io-trace"]=4;check(4,0);args["a04br3-gio"]=6;args["a04br3-gpu-stage"]=1;check(4,1);}\n'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'t.cpp';e=Path(d)/'t';p.write_text(pre+s[a:b]+post);subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined','-I',str(root/'package'),str(p),'-o',str(e)],check=True);subprocess.run([str(e)],check=True)
print('PASS default off, aliases and explicit long option precedence')
