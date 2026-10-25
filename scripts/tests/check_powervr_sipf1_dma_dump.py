#!/usr/bin/env python3
"""Actual CPU range helper: initialized lengths, guard-page bounds, generic no-op."""
from pathlib import Path
import os, subprocess, tempfile
R=Path(__file__).resolve().parents[2]
patch=(R/'patches/mesa/pvr-darwin-winsys.patch').read_text()
# Exercise shipped code without requiring an untracked Mesa checkout.
s='\n'.join(line[1:] for line in patch.splitlines()
            if line.startswith(('+',' ')) and not line.startswith('+++'))
def function(sig):
 a=s.index(sig); b=s.index('{',a);e=b+1;depth=1
 while depth:
  depth+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[a:e]
pre=r'''#define _GNU_SOURCE
#include <stdint.h>
#include <string.h>
#include <stdbool.h>
#include <stdio.h>
#include <assert.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>
#define UNUSED __attribute__((unused))
static void pvr_darwin_sipf1_log(const char *text){(void)text;}
'''
main=r'''
int main(void){size_t page=sysconf(_SC_PAGESIZE);unsigned char *p=mmap(0,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(p!=MAP_FAILED);assert(!mprotect(p+page,page,PROT_NONE));unsigned char *end=p+page;
pvr_darwin_sipf1_dump_range(false,end,0,100,0,100,"off");
pvr_darwin_sipf1_dump_range(true,NULL,0,100,0,100,"null");
pvr_darwin_sipf1_dump_range(true,end,0,0,0,10,"empty");
pvr_darwin_sipf1_dump_range(true,end,0,10,UINT64_MAX,10,"overflow");
pvr_darwin_sipf1_dump_range(true,end,0,10,10,10,"end");
pvr_darwin_sipf1_dump_range(true,end,0,10,0,0,"zero");
for(int i=0;i<128;i++)end[-128+i]=(unsigned char)i;
pvr_darwin_sipf1_dump_range(true,end-12,0x10000,12,0,12,"unitex");
pvr_darwin_sipf1_dump_range(true,end-17,0x20000,17,1,UINT64_MAX,"clamp");
pvr_darwin_sipf1_dump_range(true,end-128,0x30000,128,0,256,"cap");
assert(!munmap(p,page*2));}
'''
with tempfile.TemporaryDirectory(prefix='pvr-dma-dump-') as t:
 p=Path(t);(p/'t.c').write_text(pre+function('static inline bool pvr_darwin_sipf1_touch_only(')+function('static void pvr_darwin_sipf1_dump_bytes(')+function('static void pvr_darwin_sipf1_dump_range(')+main)
 for darwin in (False,True):
  subprocess.run([os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined','-fno-pie','-no-pie',*(['-DPVR_WINSYS_DARWIN'] if darwin else []),str(p/'t.c'),'-o',str(p/'t')],check=True)
  r=subprocess.run([str(p/'t')],check=True,capture_output=True,text=True)
  if not darwin:assert not r.stderr;continue
  lines=r.stderr.splitlines();assert len(lines)==13,lines
  assert 'initialized=12 captured=12' in lines[0] and lines[1].endswith('bytes=7475767778797a7b7c7d7e7f')
  assert 'captured=16' in lines[2] and 'offset=1 bytes=707172737475767778797a7b7c7d7e7f' in lines[3]
  assert 'initialized=256 captured=128' in lines[4]
  assert sum('bytes=' in x for x in lines)==10
# Both paths supply dump explicitly; only CLIP uses the existing per-stream selection.
assert 'prep_data, false);' in s
assert '&unitex_pds_prog,\n                                 prep_data,\n                                 dump);' in s
assert 'pvr_bo->size, 0, 12, "unitex-texture-data"' in s
print('PASS actual range/byte helpers: off/null/zero/end/overflow, initialized 12B, BO clamp, 128B cap, 16B chunks, guard pages, generic no-op; ASan/UBSan')
