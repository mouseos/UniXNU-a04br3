#!/usr/bin/env python3
"""Check actual byte helper's marker-only transport and owner CLI/constructor."""
from pathlib import Path
import os,runpy,subprocess,sys,tempfile
R=Path(__file__).resolve().parents[2]
s='\n'.join(l[1:] for l in (R/'patches/mesa/pvr-darwin-winsys.patch').read_text().splitlines() if l.startswith('+') and not l.startswith('+++'))
a=s.index('static void pvr_darwin_sipf1_dump_bytes(');e=s.index('{',a)+1;n=1
while n:n+=(s[e]=='{')-(s[e]=='}');e+=1
helper=s[a:e]
assert s.count('const char *marker_only = getenv("PVR_DARWIN_SIPF1_MARKER_ONLY");')==6
pre=r'''#define _GNU_SOURCE
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define UNUSED __attribute__((unused))
static unsigned markers;
static bool pvr_darwin_sipf1_touch_only(void){return false;}
static void pvr_darwin_sipf1_log(const char*t){assert(!strcmp(t,"pvr SIPF1 trial offset=0 bytes=00112233"));markers++;}
'''
main=r'''
int main(int argc,char**argv){assert(argc==2);if(strcmp(argv[1],"absent"))setenv("PVR_DARWIN_SIPF1_MARKER_ONLY",argv[1],1);else unsetenv("PVR_DARWIN_SIPF1_MARKER_ONLY");const uint8_t b[]={0,0x11,0x22,0x33};pvr_darwin_sipf1_dump_bytes(true,b,sizeof(b),0,sizeof(b),"trial");assert(markers==1);}
'''
with tempfile.TemporaryDirectory(prefix='pvr-marker-only-') as t:
 p=Path(t);(p/'x.c').write_text(pre+helper+main)
 subprocess.run(['cc','-DPVR_WINSYS_DARWIN','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(p/'x.c'),'-o',str(p/'x')],check=True)
 for value in ['absent','','0','01','2','true','1']:
  r=subprocess.run([str(p/'x'),value],capture_output=True,text=True,check=True)
  assert r.stderr==('' if value=='1' else 'pvr SIPF1 trial offset=0 bytes=00112233\n'),r.stderr
 b=R/'scripts/build-a04br3-powervr-probe.py'
 ctor=runpy.run_path(str(b))['sipf1_marker_only_probe_source']()
 (p/'ctor.c').write_text(ctor+'\n#include <assert.h>\n#include <string.h>\nint main(void){assert(!strcmp(getenv("PVR_DARWIN_SIPF1_MARKER_ONLY"),"1"));assert(!getenv("PVR_DARWIN_SIPF1_STDERR_ONLY"));}\n')
 env=dict(os.environ);env.pop('PVR_DARWIN_SIPF1_STDERR_ONLY',None)
 subprocess.run(['cc','-D_POSIX_C_SOURCE=200809L',str(p/'ctor.c'),'-o',str(p/'ctor')],check=True);subprocess.run([str(p/'ctor')],env=env,check=True)
 for flags,error in [(['--sipf1-marker-only'],'requires --vulkan'),(['--vulkan','--sipf1-marker-only'],'requires --sipf1-dump'),(['--vulkan','--sipf1-dump','--sipf1-marker-only','--sipf1-stderr-only'],'choose one')]:
  r=subprocess.run([sys.executable,str(b),'--check',*flags],capture_output=True,text=True);assert r.returncode==2 and error in r.stderr,r.stderr
print('PASS canonical byte helper: strict/default marker-only, preserved marker text/call, suppressed stderr only at1; six metadata gates, independent ctor, conflicting/missing CLI; ASan/UBSan')
