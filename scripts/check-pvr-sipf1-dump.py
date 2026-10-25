#!/usr/bin/env python3
"""Actual canonical snapshot helpers: guarded bounds, exact gate and atomic cap."""
from pathlib import Path
import os
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
p=(ROOT/'patches/mesa/pvr-darwin-winsys.patch').read_text()
# Helpers are entirely added lines in this canonical patch.
s='\n'.join(x[1:] for x in p.splitlines() if x.startswith('+') and not x.startswith('+++'))
def function(sig):
 a=s.index(sig);b=s.index('{',a);e=b+1;d=1
 while d:
  d+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[a:e]
h=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
static inline void pvr_darwin_sipf1_log(const char *text) {(void)text;}
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <pthread.h>
#define UNUSED __attribute__((unused))
'''+function('static bool pvr_darwin_sipf1_dump_begin(')+'\n'+function('static void pvr_darwin_sipf1_dump_bytes(')+'\n'+function('static void pvr_darwin_sipf1_dump_primitive(')+r'''
static unsigned accepted;
static void *worker(void *unused){(void)unused;if(pvr_darwin_sipf1_dump_begin(true))__atomic_fetch_add(&accepted,1,__ATOMIC_RELAXED);return NULL;}
int main(void){
assert(!unsetenv("PVR_DARWIN_SIPF1_DUMP"));assert(!pvr_darwin_sipf1_dump_begin(true));
const char *bad[]={"0","2","01","true",""};for(unsigned i=0;i<5;i++){assert(!setenv("PVR_DARWIN_SIPF1_DUMP",bad[i],1));assert(!pvr_darwin_sipf1_dump_begin(true));}
assert(!setenv("PVR_DARWIN_SIPF1_DUMP","1",1));assert(!pvr_darwin_sipf1_dump_begin(false));
pthread_t t[16];for(unsigned i=0;i<16;i++)assert(!pthread_create(&t[i],NULL,worker,NULL));for(unsigned i=0;i<16;i++)assert(!pthread_join(t[i],NULL));
#ifdef PVR_WINSYS_DARWIN
assert(accepted==8);
#else
assert(!accepted);
#endif
assert(!pvr_darwin_sipf1_dump_begin(true));
size_t page=sysconf(_SC_PAGESIZE);void *guard=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(guard!=MAP_FAILED);
/* Any accidental access faults: every call below must reject before reading. */
pvr_darwin_sipf1_dump_bytes(false,guard,16,0,8,"off");
pvr_darwin_sipf1_dump_bytes(true,guard,16,17,8,"offset");
pvr_darwin_sipf1_dump_bytes(true,guard,16,16,1,"end");
pvr_darwin_sipf1_dump_bytes(true,guard,16,8,9,"cross");
pvr_darwin_sipf1_dump_bytes(true,guard,16,UINT64_MAX,8,"overflow");
pvr_darwin_sipf1_dump_bytes(true,guard,32,0,17,"cap");
pvr_darwin_sipf1_dump_bytes(true,guard,16,0,0,"empty");
pvr_darwin_sipf1_dump_bytes(true,NULL,16,0,8,"null");
unsigned char bytes[16]={0x2a,3,0,0,0x21,0x40};
pvr_darwin_sipf1_dump_bytes(true,bytes,16,0,8,"record");
pvr_darwin_sipf1_dump_bytes(true,bytes,16,0,16,"header");
pvr_darwin_sipf1_dump_primitive(true,guard,84,0,83);
pvr_darwin_sipf1_dump_primitive(true,guard,83,0,84);
pvr_darwin_sipf1_dump_primitive(true,guard,84,UINT64_MAX,84);
void *two=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(two!=MAP_FAILED);assert(!mprotect((char*)two+page,page,PROT_NONE));
unsigned char *prim=(unsigned char*)two+page-84;for(unsigned i=0;i<84;i++)prim[i]=(unsigned char)i;
pvr_darwin_sipf1_dump_primitive(true,prim,84,0,84);assert(!munmap(two,page*2));
assert(!munmap(guard,page));puts("PASS actual dump helpers: exact gate, concurrent eight-stream cap, guarded bounds and initialized byte output");}
'''
with tempfile.TemporaryDirectory(prefix='sipf1-dump-') as t:
 d=Path(t);(d/'t.c').write_text(h)
 for darwin in (False,True):
  cmd=[os.environ.get('CC','cc'),'-std=gnu11','-Wall','-Wextra','-Werror','-pthread','-fsanitize=address,undefined','-fno-pie','-no-pie']
  if darwin:cmd+=['-DPVR_WINSYS_DARWIN']
  subprocess.run(cmd+[str(d/'t.c'),'-o',str(d/'test')],check=True)
  r=subprocess.run([str(d/'test')],check=True,capture_output=True,text=True,timeout=30)
  expected='pvr SIPF1 record offset=0 bytes=2a03000021400000\npvr SIPF1 header offset=0 bytes=2a030000214000000000000000000000\n' if darwin else ''
  if darwin:
   for label,offset,length in [('first-index',16,8),('first-vertex01',24,16),('first-vertex23',40,16),('first-pds-dw0-3',56,16),('first-pds-dw4-6',72,12)]:
    expected+=f'pvr SIPF1 {label} offset={offset:x} bytes='+bytes(range(offset,offset+length)).hex()+'\n'
  assert r.stderr==expected,r.stderr
  print(('Darwin: ' if darwin else 'generic: ')+r.stdout.strip())
