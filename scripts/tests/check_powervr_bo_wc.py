#!/usr/bin/env python3
"""実allocator/view/free関数のフラグ・参照寿命・失敗解放をASan/UBSanで検証する。"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
GPU = (ROOT / 'package/powervr/NativePowerVRGpu.cpp').read_text()


def function(signature):
    start = GPU.index(signature)
    brace = GPU.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (GPU[end] == '{') - (GPU[end] == '}')
        end += 1
    return GPU[start:end]


h = r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <initializer_list>
#include "native_powervr_pte.h"
#include "native_powervr_bo_stats.h"
using IOOptionBits=unsigned;using IOByteCount=uint64_t;using vm_offset_t=uintptr_t;
constexpr unsigned kIODirectionInOut=3,kIOMapWriteCombineCache=4u<<8,
 kIOWriteCombineCache=4,kIOMemoryBufferCacheShift=28,kIOMemoryTypePhysical64=0x20,kIOMemoryMapperNone=1;
constexpr uint64_t kDevicePageSize=4096,kBoPhysicalMask=0xfffff000ULL,SZ_1G=1ULL<<30;
using IOReturn=int;using UInt32=uint32_t;
constexpr int kIOReturnSuccess=0,kIOReturnBadArgument=1,kIOReturnNotReady=2,kIOReturnNotFound=3,kIOReturnNoMemory=4;
constexpr unsigned kIOMapAnywhere=1,kIOMapDefaultCache=0,kIOMapInhibitCache=2;
constexpr unsigned NativePowerVRMetalScanoutType=99,DRM_PVR_BO_ALLOW_CPU_USERSPACE_ACCESS=0x1000;
static bool mapSupplied;static unsigned mapArg;
void *kernel_task=(void*)1;
static bool pteSupplied=true; static unsigned pteArg=1;
static bool supplied,held,failMd,shortMd,badSg,failCpu,failView;
void IOLockLock(bool*l){assert(!*l&&!held);*l=held=true;}
void IOLockUnlock(bool*l){assert(*l&&held);*l=held=false;}
static unsigned arg,parses,allocations,failAt,heapLive,mdLive,viewLive,allocOptions,barriers,flushes;
bool PE_parse_boot_argn(const char*k,void*out,size_t n){
 assert(!held&&n==4);++parses;
 if(!strcmp(k,"a04br3-gpu-pte")){memcpy(out,&pteArg,4);return pteSupplied;}
 if(!strcmp(k,"a04br3-gpu-bo-map")){memcpy(out,&mapArg,4);return mapSupplied;}
 assert(!strcmp(k,"a04br3-gpu-bo-wc"));memcpy(out,&arg,4);return supplied;}
void *kzallocBytes(size_t n){if(++allocations==failAt)return nullptr;void*p=calloc(1,n);if(p)++heapLive;return p;}
void kfreeBytes(void*p,size_t){if(p){assert(heapLive);--heapLive;free(p);}}
void IOLog(const char*,...){assert(false);}
void flush_dcache(vm_offset_t,unsigned n,int physical){assert(n&&physical==1);++flushes;}
struct IOAddressRange{uint64_t address,length;};
struct native_powervr_sg{uint64_t phys,size;};
class IOMemoryDescriptor{
 protected:IOOptionBits _flags=0;
 public:virtual ~IOMemoryDescriptor()=default;
};
class IOBufferMemoryDescriptor:public IOMemoryDescriptor{
 public:unsigned refs=1;uint64_t size;void *bytes;
 explicit IOBufferMemoryDescriptor(uint64_t n):size(n),bytes(calloc(1,n)){_flags=0x13579bdf; ++mdLive;}
 ~IOBufferMemoryDescriptor(){free(bytes);assert(mdLive);--mdLive;}
 static IOBufferMemoryDescriptor *inTaskWithPhysicalMask(void*t,IOOptionBits options,uint64_t n,uint64_t mask){
  assert(t==kernel_task&&mask==kBoPhysicalMask);allocOptions=options;
  return failMd?nullptr:new IOBufferMemoryDescriptor(n);}
 uint64_t getLength(){return shortMd?size-1:size;}
 void *getBytesNoCopy(){return failCpu?nullptr:bytes;}
 uint64_t getPhysicalSegment(uint64_t off,IOByteCount*len,unsigned mapper){
  assert(mapper==kIOMemoryMapperNone&&off<size);*len=4096;
  return badSg?0:(off<8192?0x80000000ULL+off:0x90000000ULL+off-8192);}
 void retain(){++refs;}void release(){assert(refs);if(!--refs)delete this;}
};
class IOGeneralMemoryDescriptor:public IOMemoryDescriptor{
 public:unsigned refs=1;
 virtual ~IOGeneralMemoryDescriptor()=default;
 bool initWithOptions(IOAddressRange*r,unsigned count,unsigned off,void*t,IOOptionBits opts,void*mapper){
  assert(r&&count==2&&!off&&!t&&!mapper);
  assert(opts==(kIOMemoryTypePhysical64|kIODirectionInOut|(kIOWriteCombineCache<<kIOMemoryBufferCacheShift)));
  return !failView;}
 virtual void free(){}void retain(){++refs;}void release(){assert(refs);if(!--refs){free();delete this;}}
};
class NativePowerVRBoView:public IOGeneralMemoryDescriptor{
 IOBufferMemoryDescriptor *backing_=nullptr;
 public:NativePowerVRBoView(){++viewLive;}~NativePowerVRBoView(){assert(viewLive);--viewLive;}
 static NativePowerVRBoView *withBacking(IOBufferMemoryDescriptor*,const native_powervr_sg*,uint32_t,uint64_t);
 void free() override;
};
struct Host{int complete(){return 0;}void release(){assert(false);}};
struct HostMap{void release(){assert(false);}};
struct native_powervr_bo{
 uint32_t refs;uint64_t size,flags;IOBufferMemoryDescriptor*md;void*cpu;native_powervr_sg*sg;
 uint32_t nents,sgCapacity;NativePowerVRBoView*view;Host*host;bool hostPrepared;bool liveAccounted,liveHostImport;HostMap*hostMap;
};
bool gBoPolicyCaptureEnabled;
bool gBoMapEnabled;
bool gBoWcEnabled,gBoWcInitialized,gBoWcSupplied;uint32_t gBoWcRequested;
'''
h += r'''
static IOMemoryDescriptor *(*native_gop_handoff_memory)()=nullptr;
static void *handleLookup(void*p,UInt32 type){return type==7?p:nullptr;}
class NativePowerVRUserClient {public:
 bool mutex=false;bool *lock_=&mutex;bool closing_=false,handoffOnly_=false;void *bos_=nullptr;
 IOReturn clientMemoryForType(UInt32,IOOptionBits*,IOMemoryDescriptor**);
};
'''
h += function('IOReturn NativePowerVRUserClient::clientMemoryForType(') + '\n'
h += function('class BoDescriptorFlags') + ';\n'
h += 'native_powervr_bo_policy_record gFirstBoPolicy; uint32_t gFirstBoPolicyState;\n'
for signature in ('void recordBoAllocation(', 'void copyBoAllocationPolicy('):
    h += function(signature) + '\n'
h += 'bool gBoLiveEnabled; native_powervr_bo_live_class gBoLiveNormal, gBoLiveHost;\n'
for signature in ('void boLivePeak(', 'void boLiveAdd(', 'void boLiveRemove('):
    h += function(signature) + '\n'
for signature in ('void initBoWcPolicy()',
                  'NativePowerVRBoView *NativePowerVRBoView::withBacking(',
                  'void NativePowerVRBoView::free()', 'void boDestroy('):
    h += function(signature) + '\n'
h += function('int boCreate(').replace('__asm__ volatile("dsb sy" ::: "memory");', '++barriers;')
h += r'''
static void policy(bool present,unsigned value){
 supplied=present;arg=value;gBoWcInitialized=gBoWcEnabled=false;held=false;
 unsigned old=parses;initBoWcPolicy();assert(parses==old+3);
 supplied=!present;arg=value^1;held=true;initBoWcPolicy();assert(parses==old+3);held=false;
}
static void reset(){allocations=failAt=0;failMd=shortMd=badSg=failCpu=failView=false;}
int main(){
 for(bool mp:{false,true})for(unsigned mv:{0u,1u,2u,~0u})
 for(bool present:{false,true})for(unsigned value:{0u,1u,2u,~0u}){
  mapSupplied=mp;mapArg=mv;
  policy(present,value);assert(gBoMapEnabled==(mp&&mv==1));bool wc=present&&value==1;assert(gBoWcEnabled==wc);reset();
  gFirstBoPolicyState=0;
  native_powervr_bo*bo=nullptr;unsigned before=parses;
  held=true;assert(!boCreate(12288,0x1234,&bo));held=false;
  assert(parses==before&&allocOptions==(kIODirectionInOut|(wc?kIOMapWriteCombineCache:0)));
  native_powervr_bo_policy_record observed{};copyBoAllocationPolicy(&observed);
  assert(observed.ready&&observed.initialized&&bool(observed.enabled)==wc);
  assert(bool(observed.supplied)==present&&observed.requested==value);
  assert(observed.allocation_options==allocOptions&&observed.descriptor_present&&observed.descriptor_flags==0x13579bdf&&observed.size==12288);
  recordBoAllocation(nullptr,1,0);native_powervr_bo_policy_record same{};copyBoAllocationPolicy(&same);
  assert(!memcmp(&observed,&same,sizeof(same))); // the first record is immutable
  assert(bo->flags==0x1234&&bo->size==12288&&bo->nents==2&&bo->sg[0].size==8192&&bo->sg[1].size==4096);
  // Actual UserClient result owns a reference after BO teardown in either mode.
  NativePowerVRUserClient client;client.bos_=bo;
  IOMemoryDescriptor *memory=nullptr;IOOptionBits options=0;
  assert(client.clientMemoryForType(8,&options,&memory)==kIOReturnNotFound&&!memory);
  uint64_t savedFlags=bo->flags;bo->flags=0;
  assert(client.clientMemoryForType(7,&options,&memory)==kIOReturnBadArgument&&!memory);
  bo->flags=savedFlags;client.closing_=true;
  assert(client.clientMemoryForType(7,&options,&memory)==kIOReturnNotReady&&!memory);client.closing_=false;
  client.handoffOnly_=true;
  assert(client.clientMemoryForType(7,&options,&memory)==kIOReturnBadArgument&&!memory);
  assert(client.clientMemoryForType(NativePowerVRMetalScanoutType,&options,&memory)==kIOReturnNotReady&&!memory);
  client.handoffOnly_=false;
  failView=gBoMapEnabled; // Direct sharing must not allocate a physical view.
  assert(client.clientMemoryForType(7,&options,&memory)==kIOReturnSuccess);
  failView=false;
  if(gBoMapEnabled){assert(memory==bo->md&&!bo->view&&bo->md->refs==2);
   assert(options==(kIOMapAnywhere|kIOMapDefaultCache));auto *md=bo->md;
   boDestroy(bo);assert(mdLive==1&&!viewLive&&!heapLive);md->release();
  }else{assert(memory==bo->view&&bo->md->refs==2&&bo->view->refs==2);
   assert(options==(kIOMapAnywhere|kIOMapWriteCombineCache));auto *view=bo->view;
   boDestroy(bo);assert(mdLive==1&&viewLive==1&&!heapLive);view->release();}
  assert(!mdLive&&!viewLive&&!heapLive);
  // Even with the test flag, a host BO must not enter the new direct path.
  assert(!boCreate(12288,0x1234,&bo));client.bos_=bo;Host host;bo->host=&host;
  assert(client.clientMemoryForType(7,&options,&memory)==kIOReturnSuccess&&memory==bo->view);
  assert(options==(kIOMapAnywhere|kIOMapWriteCombineCache));bo->view->release();bo->host=nullptr;
  boDestroy(bo);assert(!mdLive&&!viewLive&&!heapLive);
  assert(!boCreate(12288,0x1234,&bo));
  // Exercise actual view retention/free: user mapping survives BO destruction.
  bo->view=NativePowerVRBoView::withBacking(bo->md,bo->sg,bo->nents,bo->size);
  assert(bo->view&&bo->md->refs==2);auto*map=bo->view;map->retain();
  boDestroy(bo);assert(mdLive==1&&viewLive==1&&!heapLive);
  map->release();assert(!mdLive&&!viewLive&&!heapLive);
  // Both allocation failures and partial descriptor/SG failures release everything.
  for(unsigned fault=0;fault<6;++fault){reset();
   failAt=fault<2?fault+1:0;failMd=fault==2;shortMd=fault==3;failCpu=fault==4;badSg=fault==5;
   bo=nullptr;assert(boCreate(12288,0,&bo)==-ENOMEM&&!bo&&!mdLive&&!viewLive&&!heapLive);}
  reset();bo=nullptr;assert(!boCreate(12288,0,&bo));failView=true;
  bo->view=NativePowerVRBoView::withBacking(bo->md,bo->sg,bo->nents,bo->size);
  assert(!bo->view&&bo->md->refs==1);boDestroy(bo);assert(!mdLive&&!viewLive&&!heapLive);
 }
 assert(barriers&&flushes);
 // Disabled/absent/non-one PTE flags leave the singleton entirely untouched.
 for(bool present:{false,true})for(unsigned value:{0u,1u,2u,~0u}){
  pteSupplied=present;pteArg=value;policy(true,1);reset();gFirstBoPolicyState=0;
  native_powervr_bo*bo=nullptr;held=true;assert(!boCreate(12288,0,&bo));held=false;
  native_powervr_bo_policy_record r{};copyBoAllocationPolicy(&r);
  bool capture=present&&value==1;assert(bool(r.ready)==capture);
  assert(gFirstBoPolicyState==(capture?2u:0u));
  boDestroy(bo);assert(!mdLive&&!viewLive&&!heapLive);
 }
 // Before initialization the capture gate is default-off too.
 gFirstBoPolicyState=0;gBoPolicyCaptureEnabled=false;gBoWcInitialized=false;reset();
 native_powervr_bo*early=nullptr;assert(!boCreate(12288,0,&early));
 native_powervr_bo_policy_record r{};copyBoAllocationPolicy(&r);assert(!r.ready&&!gFirstBoPolicyState);
 boDestroy(early);assert(!mdLive&&!viewLive&&!heapLive);
}
'''

publish = function('extern "C" int native_powervr_publish(')
assert publish.index('initBoWcPolicy();') < publish.index('new NativePowerVRGpu')
assert 'IOLock' not in function('void initBoWcPolicy()')
assert 'PE_parse_boot_argn' not in function('int boCreate(')
assert 'gBoWcEnabled' not in function('int boImportHost(')
assert 'gBoMapEnabled' not in function('int boImportHost(')
with tempfile.TemporaryDirectory(prefix='pvr-bo-wc-') as directory:
    source = Path(directory) / 'fixture.cpp'
    source.write_text(h)
    binary = source.with_suffix('')
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-g',
                    '-I', str(ROOT / 'package/powervr'), str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=30)
print('PASS actual BO allocator/UserClient/view/free: independent WC/map gates, exact descriptors, host exclusion, retained lifetime and cleanup (ASan/UBSan)')
print('API mocks do not prove real PTE attributes or XNU page-cache restoration; actual kernel build/PTE test remains required.')
