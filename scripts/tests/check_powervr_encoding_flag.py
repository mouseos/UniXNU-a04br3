#!/usr/bin/env python3
"""Actual owner constructor/CLI: encoding independent of routing; no GPU."""
from pathlib import Path
import os
import runpy
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[2]
builder=ROOT/'scripts/build-a04br3-powervr-probe.py'
b=runpy.run_path(str(builder));ctor=b['sipf1_encoding_probe_source']()
with tempfile.TemporaryDirectory(prefix='pvr-encoding-') as t:
 p=Path(t);src=p/'t.c';exe=p/'test'
 cc=[os.environ.get('CC','cc'),'-std=c11','-D_POSIX_C_SOURCE=200809L','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie']
 src.write_text(ctor+r'''
#include <assert.h>
#include <string.h>
int main(int argc,char **argv){assert(argc==2);assert(!strcmp(getenv("PVR_DARWIN_SIPF1"),"1"));if(!strcmp(argv[1],"absent"))assert(!getenv("PVR_DARWIN_TQ_CLIP"));else assert(!strcmp(getenv("PVR_DARWIN_TQ_CLIP"),argv[1]));assert(!getenv("PVR_PROBE_TQ_FIRST")&&!getenv("PVR_PROBE_UI_BATCH")&&!getenv("PVR_DARWIN_SIPF1_DUMP"));}
''')
 subprocess.run(cc+[str(src),'-o',str(exe)],check=True)
 for flag in ('absent','0','1','01'):
  env=dict(os.environ)
  for name in ('PVR_DARWIN_TQ_CLIP','PVR_PROBE_TQ_FIRST','PVR_PROBE_UI_BATCH','PVR_DARWIN_SIPF1_DUMP'):env.pop(name,None)
  env['PVR_DARWIN_SIPF1']='0'
  if flag!='absent':env['PVR_DARWIN_TQ_CLIP']=flag
  subprocess.run([str(exe),flag],env=env,check=True,timeout=30)
 src.write_text(ctor.replace('setenv(', 'fail_setenv(').replace('#include <stdio.h>','#include <stdio.h>\nstatic int fail_setenv(const char*a,const char*b,int c){(void)a;(void)b;(void)c;return -1;}')+'\nint main(void){return 99;}\n')
 subprocess.run(cc+[str(src),'-o',str(exe)],check=True)
 r=subprocess.run([str(exe)],capture_output=True,text=True,timeout=30);assert r.returncode==1 and 'cannot enable SIPF1 encoding' in r.stderr
r=subprocess.run([sys.executable,str(builder),'--check','--sipf1-encoding'],capture_output=True,text=True)
assert r.returncode==2 and '--sipf1-encoding requires --vulkan' in r.stderr
print('PASS actual encoding constructor: sets SIPF1 only, preserves four routing states, failed-setenv exit, Vulkan CLI gate; ASan/UBSan, no GPU execution')
