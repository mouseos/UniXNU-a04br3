#!/usr/bin/env python3
"""Shipped bounded CPU TOUCH: exact gates, no formatting/logging, guard pages."""
from pathlib import Path
import os, runpy, subprocess, sys, tempfile
R=Path(__file__).resolve().parents[2]
patch=(R/'patches/mesa/pvr-darwin-winsys.patch').read_text()
s='\n'.join(x[1:] for x in patch.splitlines() if x.startswith(('+',' ')) and not x.startswith('+++'))
def fun(sig):
 a=s.index(sig);e=s.index('{',a)+1;d=1
 while d:d+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[a:e]
head=r'''#define _GNU_SOURCE
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <pthread.h>
#define UNUSED __attribute__((unused))
static void pvr_darwin_sipf1_log(const char*t){(void)t;abort();}
static int forbidden_format(char*p,size_t n,const char*f,...){(void)p;(void)n;(void)f;abort();}
static int forbidden_log(FILE*p,const char*f,...){(void)p;(void)f;abort();}
#define snprintf forbidden_format
#define fprintf forbidden_log
'''
helpers='\n'.join(fun(f) for f in ('static inline bool pvr_darwin_sipf1_touch_only(','static bool pvr_darwin_sipf1_dump_begin(','static void pvr_darwin_sipf1_dump_bytes(','static void pvr_darwin_sipf1_dump_range(','static void pvr_darwin_sipf1_dump_primitive('))
main=r'''
static unsigned accepted;
static void *worker(void*x){(void)x;if(pvr_darwin_sipf1_dump_begin(true))__atomic_fetch_add(&accepted,1,__ATOMIC_RELAXED);return NULL;}
int main(void){unsetenv("PVR_DARWIN_SIPF1_DUMP");unsetenv("PVR_DARWIN_SIPF1_TOUCH");assert(!pvr_darwin_sipf1_dump_begin(true));const char*bad[]={"","0","01","2","true"};for(unsigned i=0;i<5;i++){setenv("PVR_DARWIN_SIPF1_TOUCH",bad[i],1);assert(!pvr_darwin_sipf1_dump_begin(true));}
setenv("PVR_DARWIN_SIPF1_TOUCH","1",1);assert(!pvr_darwin_sipf1_dump_begin(false));
#ifdef PVR_WINSYS_DARWIN
assert(pvr_darwin_sipf1_touch_only());setenv("PVR_DARWIN_SIPF1_DUMP","1",1);assert(!pvr_darwin_sipf1_touch_only());unsetenv("PVR_DARWIN_SIPF1_DUMP");
#endif
pthread_t t[16];for(int i=0;i<16;i++)assert(!pthread_create(t+i,NULL,worker,NULL));for(int i=0;i<16;i++)pthread_join(t[i],NULL);
#ifdef PVR_WINSYS_DARWIN
assert(accepted==8);
#else
assert(!accepted);
#endif
assert(!pvr_darwin_sipf1_dump_begin(true));
size_t page=sysconf(_SC_PAGESIZE);uint8_t*p=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(p!=MAP_FAILED);memset(p,0xa5,page);assert(!mprotect(p+page,page,PROT_NONE));uint8_t*end=p+page;assert(!mprotect(p,page,PROT_READ));
pvr_darwin_sipf1_dump_bytes(false,end,16,0,16,"off");pvr_darwin_sipf1_dump_bytes(true,end,16,UINT64_MAX,1,"overflow");pvr_darwin_sipf1_dump_bytes(true,end,16,0,17,"oversize");pvr_darwin_sipf1_dump_bytes(true,end,16,16,1,"end");
pvr_darwin_sipf1_dump_bytes(true,end-12,12,0,12,"nop-pds");pvr_darwin_sipf1_dump_range(true,end-17,0,17,1,UINT64_MAX,"dma");pvr_darwin_sipf1_dump_range(true,end-128,0,128,0,256,"cap");pvr_darwin_sipf1_dump_primitive(true,end-84,84,0,84);assert(!munmap(p,page*2));}
'''
with tempfile.TemporaryDirectory(prefix='pvr-touch-') as t:
 p=Path(t);(p/'t.c').write_text(head+helpers+main)
 for darwin in (False,True):
  subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-pthread','-fsanitize=address,undefined','-fno-pie','-no-pie',*(['-DPVR_WINSYS_DARWIN'] if darwin else []),str(p/'t.c'),'-o',str(p/'t')],check=True)
  r=subprocess.run([str(p/'t')],capture_output=True,text=True,check=True);assert not r.stdout and not r.stderr
 b=R/'scripts/build-a04br3-powervr-probe.py';ctor=runpy.run_path(str(b))['sipf1_touch_probe_source']()
 (p/'ctor.c').write_text(ctor+'\n#include <assert.h>\n#include <string.h>\nint main(void){assert(!strcmp(getenv("PVR_DARWIN_SIPF1_TOUCH"),"1"));assert(!getenv("PVR_DARWIN_SIPF1_DUMP")&&!getenv("PVR_DARWIN_SUBMIT_BARRIER")&&!getenv("PVR_DARWIN_SERIAL_TRANSFER"));}\n')
 env=dict(os.environ)
 for key in ('PVR_DARWIN_SIPF1_DUMP','PVR_DARWIN_SUBMIT_BARRIER','PVR_DARWIN_SERIAL_TRANSFER'):env.pop(key,None)
 subprocess.run(['cc','-D_POSIX_C_SOURCE=200809L',str(p/'ctor.c'),'-o',str(p/'ctor')],check=True);subprocess.run([str(p/'ctor')],env=env,check=True)
 failed=ctor.replace('setenv(', 'fail_setenv(').replace('#include <stdio.h>','#include <stdio.h>\nstatic int fail_setenv(const char*a,const char*b,int c){(void)a;(void)b;(void)c;return -1;}')
 (p/'ctor.c').write_text(failed+'\nint main(void){return 99;}\n');subprocess.run(['cc','-D_POSIX_C_SOURCE=200809L',str(p/'ctor.c'),'-o',str(p/'ctor')],check=True)
 r=subprocess.run([str(p/'ctor')],capture_output=True,text=True);assert r.returncode==1 and 'cannot enable SIPF1 touch comparison' in r.stderr
 r=subprocess.run([sys.executable,str(b),'--check','--sipf1-touch'],capture_output=True,text=True);assert r.returncode==2 and '--sipf1-touch requires --vulkan' in r.stderr
# Metadata formatting must be conditional; range reads and NOP mapping remain outside it.
for label in ('stream bo=','nop bo=','final regs bo='):
 a=s.index('"pvr SIPF1 '+label);prefix=s[max(0,a-220):a];assert 'if (!pvr_darwin_sipf1_touch_only())' in prefix
assert 'const volatile uint8_t *touch = bytes;' in helpers and 'volatile uint8_t consumed = value;' in helpers
print('PASS canonical TOUCH helpers: strict/default/combined gates, concurrent cap8, read-only guard pages, bounds/overflow/128B cap, formatting+logging forbidden, empty stdout/stderr, independent ctor/failure/CLI; ASan/UBSan')
