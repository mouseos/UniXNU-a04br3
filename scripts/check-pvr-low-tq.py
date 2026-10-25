#!/usr/bin/env python3
"""Actual low-TQ policy, heap-query loop and range checks; no device execution."""
from pathlib import Path
import os
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parents[1]
s = (ROOT/'package/powervr/NativePowerVRGpu.cpp').read_text()
def function(sig):
    a=s.index(sig); b=s.index('{',a); e=b+1; d=1
    while d:
        d+=(s[e]=='{')-(s[e]=='}');e+=1
    return s[a:e]
heaps=s[s.index('const drm_pvr_heap kHeaps['):s.index('static_assert(DRM_PVR_HEAP_GENERAL')]
policy=s[s.index('bool gTqLowEnabled;'):s.index('// pvr_find_heap_containing()')]
a=s.index('      uint64_t dest = query.heaps.array;'); b=s.index('\n    }',a)
query=s[a:b]
publish=function('extern "C" int native_powervr_publish(')
assert publish.index('loadDeviceInfo()') < publish.index('initTqLowPolicy()') < publish.index('service->registerService()')
assert s.count('initTqLowPolicy();')==1
assert 'drm_pvr_heap heap = effective_heap(i);' in query
h=r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <cstdarg>
#define SZ_2M 0x200000ULL
#define SZ_256M 0x10000000ULL
#define SZ_4G 0x100000000ULL
#define SZ_16G 0x400000000ULL
#define SZ_128G 0x2000000000ULL
#include "linux/pvr_rogue_heap_config.h"
enum {DRM_PVR_HEAP_GENERAL,DRM_PVR_HEAP_PDS_CODE_DATA,DRM_PVR_HEAP_USC_CODE,DRM_PVR_HEAP_RGNHDR,DRM_PVR_HEAP_VIS_TEST,DRM_PVR_HEAP_TRANSFER_FRAG,DRM_PVR_HEAP_COUNT};
struct drm_pvr_heap {uint64_t base,size;uint32_t flags,page_size_log2;};
struct Dev {bool quirk;};static Dev gDev;
#define PVR_HAS_QUIRK(p,n) ((p)->quirk)
static uint64_t gGpuId,gDeviceAddrBits;static bool supplied;static uint32_t arg,reads,logs;
bool PE_parse_boot_argn(const char *key,void *p,unsigned n){assert(!strcmp(key,"a04br3-gpu-tq-low")&&n==4);++reads;memcpy(p,&arg,4);return supplied;}
void IOLog(const char *fmt,...){char b[512];va_list a;va_start(a,fmt);vsnprintf(b,sizeof(b),fmt,a);va_end(a);assert(strstr(b,"experimental TQ low policy enabled"));++logs;}
'''+heaps+policy+function('bool find_heap_containing(')+r'''
static int queryHeaps(drm_pvr_heap *out){
struct {struct {uint64_t array;uint32_t count,stride;} heaps;} query={{(uint64_t)(uintptr_t)out,DRM_PVR_HEAP_COUNT,sizeof(drm_pvr_heap)}};
Dev *pvr_dev=&gDev;int err=0;
#define PVR_UOBJ_SET(dst,stride,value) (memcpy((void*)(uintptr_t)(dst),&(value),sizeof(value)),0)
'''+query+r'''
return err;}
static constexpr uint64_t target=(22ULL<<48)|(40ULL<<32)|(54ULL<<16)|30;
static void run(bool present,uint32_t flag,uint64_t gpu,uint64_t bits,bool enabled){
supplied=present;arg=flag;gGpuId=gpu;gDeviceAddrBits=bits;gTqLowInitialized=false;gTqLowEnabled=false;reads=logs=0;
initTqLowPolicy();assert(gTqLowEnabled==enabled&&reads==1&&logs==1);
arg=flag^1;gGpuId=0;initTqLowPolicy();assert(gTqLowEnabled==enabled&&reads==1&&logs==1);
for(bool quirk:{false,true}){gDev.quirk=quirk;drm_pvr_heap advertised[6];assert(!queryHeaps(advertised));
for(unsigned i=0;i<6;i++){auto h=effective_heap(i);auto original=kHeaps[i];
if(enabled&&i==DRM_PVR_HEAP_TRANSFER_FRAG){assert(h.base==0x10000&&h.size==SZ_16G-0x10000&&h.base+h.size==SZ_16G);}
else assert(!memcmp(&h,&original,sizeof(h)));
if(i==DRM_PVR_HEAP_RGNHDR&&!quirk){assert(!advertised[i].size);assert(!find_heap_containing(h.base,4096));continue;}
assert(!memcmp(&h,&advertised[i],sizeof(h)));assert(find_heap_containing(h.base,h.size));assert(find_heap_containing(h.base+h.size-4096,4096));assert(!find_heap_containing(h.base+h.size,4096));assert(!find_heap_containing(h.base,h.size+1));}
assert(!find_heap_containing(0,4096));assert(!find_heap_containing(0xffff,1));assert(!find_heap_containing(0x10000,0));assert(!find_heap_containing(UINT64_MAX-2,4));
assert(find_heap_containing(0x10000,4096)==enabled);assert(find_heap_containing(ROGUE_TRANSFER_FRAG_HEAP_BASE,4096)==!enabled);
assert(!find_heap_containing(SZ_16G-4096,8192));}
}
int main(){run(false,1,target,40,false);for(uint32_t flag:{0u,2u,~0u})run(true,flag,target,40,false);
for(unsigned shift:{0u,16u,32u,48u})run(true,1,target^(1ULL<<shift),40,false);
run(true,1,target,33,false);run(true,1,target,0,false);run(true,1,target,34,true);run(true,1,target,40,true);
puts("PASS 12 actual-policy scenarios, cached gate, exact BVNC/VA, query/range agreement, all heaps/quirks, endpoints/zero/overflow; no GPU test");}
'''
h=h.replace('#include <cstdarg>','#include <cstdarg>\n#include <initializer_list>')
with tempfile.TemporaryDirectory(prefix='pvr-low-tq-') as t:
    p=Path(t);(p/'linux').mkdir();(p/'linux/sizes.h').write_text('')
    (p/'t.cpp').write_text(h)
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(p),'-I'+str(ROOT/'package/powervr'),str(p/'t.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True,timeout=30)
