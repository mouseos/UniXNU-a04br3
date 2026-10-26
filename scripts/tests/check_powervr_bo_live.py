#!/usr/bin/env python3
"""Exercise shipped BO accounting with real alloc/import/get/put/destroy bodies."""
from pathlib import Path
import os
import runpy
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
# Reuse the checked allocator/mapping mocks, including its existing regression run.
fixture = runpy.run_path(str(ROOT / 'scripts/tests/check_powervr_bo_wc.py'))
function = fixture['function']
gpu = fixture['GPU']
h = fixture['h'].split('static void policy(')[0]
h = h.replace('#include <initializer_list>', '#include <initializer_list>\n#include <thread>\n#include <vector>\n#include <cstdio>')
h = h.replace('static bool mapSupplied;', 'static bool liveSupplied;static unsigned liveArg;\nstatic bool mapSupplied;')
h = h.replace('assert(!held&&n==4);++parses;', '''assert(!held&&n==4);++parses;
 if(!strcmp(k,"a04br3-stacklog")){memcpy(out,&liveArg,4);return liveSupplied;}''')
h = h.replace('void IOLog(const char*,...){assert(false);}', 'void IOLog(const char*,...){}')
h = h.replace('struct Host{int complete(){return 0;}void release(){assert(false);}};\nstruct HostMap{void release(){assert(false);}};', r'''
using task_t=void*;
constexpr unsigned kIODirectionOut=2;
constexpr int KERN_PROTECTION_FAILURE=2;
constexpr uint64_t kDevicePageMask=~(kDevicePageSize-1);
static bool failPrepare,failHost,failScanout,scanout;
static unsigned hostLive,hostMapLive,completed;
struct Host{
 unsigned refs=1;uint64_t size;explicit Host(uint64_t n):size(n){hostLive++;}
 int prepare(){return failPrepare?1:0;}int complete(){completed++;return 0;}
 uint64_t getLength(){return shortMd?size-1:size;}
 uint64_t getPhysicalSegment(uint64_t off,IOByteCount*len,unsigned){*len=size-off;return badSg?0:0x88000000ULL+off;}
 void retain(){refs++;}void release(){assert(refs);if(!--refs){hostLive--;delete this;}}
};
struct HostMap{
 Host*host;explicit HostMap(uint64_t size):host(new Host(size)){hostMapLive++;}
 Host*getMemoryDescriptor(){return host;}
 uint64_t getPhysicalSegment(uint64_t off,IOByteCount*len,unsigned map){return host->getPhysicalSegment(off,len,map);}
 void release(){host->release();hostMapLive--;delete this;}
};
int scanoutHostRange(task_t,uint64_t,uint64_t size,HostMap**out){*out=nullptr;if(failScanout)return -EFAULT;if(scanout)*out=new HostMap(size);return 0;}
Host*hostRange(task_t,uint64_t,uint64_t size,unsigned){return failHost?nullptr:new Host(size);}
constexpr unsigned DRM_PVR_BO_BYPASS_DEVICE_CACHE=8;
''')
h += '\n' + function('void initBoLivePolicy()') + '\n'
h += function('void boLiveCopy(') + '\n'
h += function('extern "C" void native_powervr_bo_live_snapshot(') + '\n'
h += 'int proc_selfpid(){return 56;}\n'
h += function('int hostImportFailure(') + '\n'
h += function('int boImportHost(') + '\n'
h += 'void refGet(uint32_t*p){__atomic_add_fetch(p,1u,__ATOMIC_RELAXED);}\n'
h += 'bool refPut(uint32_t*p){return __atomic_sub_fetch(p,1u,__ATOMIC_ACQ_REL)==0;}\n'
h += function('extern "C" struct native_powervr_bo *native_powervr_bo_get(') + '\n'
h += function('extern "C" void native_powervr_bo_put(') + '\n'
h += r'''
static native_powervr_bo_live_record snapshot(){native_powervr_bo_live_record r;memset(&r,0xff,sizeof r);native_powervr_bo_live_snapshot(&r);return r;}
static void clear(){assert(!gBoLiveNormal.count&&!gBoLiveHost.count);gBoLiveNormal={};gBoLiveHost={};}
int main(){
 native_powervr_bo_live_snapshot(nullptr);
 for(bool supplied:{false,true})for(unsigned arg:{0u,1u,2u,~0u}){
  clear();liveSupplied=supplied;liveArg=arg;unsigned before=parses;initBoLivePolicy();assert(parses==before+1);
  bool enabled=supplied&&arg==1;auto r=snapshot();assert(bool(r.enabled)==enabled&&!r.normal.count&&!r.host_import.count);
  native_powervr_bo*bo=nullptr;before=parses;assert(!boCreate(12288,0,&bo));assert(parses==before);
  r=snapshot();assert(r.normal.count==unsigned(enabled)&&r.normal.bytes==(enabled?12288u:0u));
  boLiveAdd(bo,false);r=snapshot();assert(r.normal.count==unsigned(enabled)); // no double accounting
  native_powervr_bo_get(bo);native_powervr_bo_put(bo);assert(snapshot().normal.count==unsigned(enabled));
  native_powervr_bo_put(bo);assert(!snapshot().normal.count&&!snapshot().normal.bytes);
 }
 liveSupplied=true;liveArg=1;initBoLivePolicy();clear();
 // Real mapping keeps backing after final BO destruction; object bytes must be zero.
 native_powervr_bo*bo=nullptr;assert(!boCreate(12288,DRM_PVR_BO_ALLOW_CPU_USERSPACE_ACCESS,&bo));
 NativePowerVRUserClient client;client.bos_=bo;gBoMapEnabled=true;IOMemoryDescriptor*memory=nullptr;IOOptionBits options;
 assert(client.clientMemoryForType(7,&options,&memory)==kIOReturnSuccess);
 auto*retained=bo->md;native_powervr_bo_put(bo);assert(!snapshot().normal.bytes&&mdLive==1);retained->release();
 // Failed partial objects must not subtract from another live allocation.
 native_powervr_bo*survivor=nullptr;assert(!boCreate(12288,0,&survivor));
 for(unsigned fault=0;fault<6;fault++){
  allocations=0;failAt=fault<2?fault+1:0;failMd=fault==2;shortMd=fault==3;failCpu=fault==4;badSg=fault==5;
  bo=nullptr;assert(boCreate(12288,0,&bo)==-ENOMEM&&!bo);assert(snapshot().normal.count==1&&snapshot().normal.bytes==12288);
  assert(mdLive==1&&heapLive==2);
 }
 failAt=0;failMd=shortMd=failCpu=badSg=false;
 native_powervr_bo_put(survivor);assert(!mdLive&&!heapLive);clear();
 // Host import success/failures use the actual import body; scanout references are not normal BO RAM.
 for(bool isScanout:{false,true}){
  scanout=isScanout;assert(!boImportHost(nullptr,0x10000,8192,0,&bo));
  auto r=snapshot();assert(r.host_import.count==1&&r.host_import.bytes==8192&&!r.normal.bytes);
  native_powervr_bo_get(bo);native_powervr_bo_put(bo);assert(snapshot().host_import.count==1);
  native_powervr_bo_put(bo);assert(!snapshot().host_import.bytes&&!hostLive&&!hostMapLive&&!heapLive);
 }
 assert(completed==1);scanout=false;
 for(unsigned failure=0;failure<6;failure++){
  failHost=failure==0;failPrepare=failure==1;shortMd=failure==2;badSg=failure==3;failScanout=failure==4;
  allocations=0;failAt=failure==5?2:0;bo=nullptr;
  assert(boImportHost(nullptr,0x10000,8192,0,&bo)<0&&!bo);
  assert(!snapshot().host_import.bytes&&!snapshot().host_import.count&&!hostLive&&!hostMapLive&&!heapLive);
 }
 failHost=failPrepare=shortMd=badSg=failScanout=false;failAt=0;clear();
 // Deterministic peaks and immutable import category, independent of descriptor pointers.
 native_powervr_bo a{},b{},c{};a.size=4096;b.size=8192;c.size=16384;
 boLiveAdd(&a,false);boLiveAdd(&b,false);boLiveAdd(&c,true);
 auto peaks=snapshot();assert(peaks.normal.count==2&&peaks.normal.bytes==12288&&peaks.normal.peak_count==2&&peaks.normal.peak_bytes==12288);
 assert(peaks.host_import.count==1&&peaks.host_import.bytes==16384&&peaks.host_import.peak_bytes==16384);
 boLiveAdd(&a,true); // Already counted: never reclassify or double count.
 boLiveRemove(&a);boLiveRemove(&b);boLiveRemove(&c);peaks=snapshot();
 assert(!peaks.normal.bytes&&!peaks.host_import.bytes&&peaks.normal.peak_bytes==12288);clear();
 // Actual atomic helpers concurrently update independent objects; snapshot is intentionally approximate.
 std::vector<std::thread> threads;
 for(unsigned t=0;t<8;t++)threads.emplace_back([t]{for(unsigned i=0;i<10000;i++){
  native_powervr_bo local{};local.size=4096;boLiveAdd(&local,t&1);boLiveRemove(&local);boLiveRemove(&local);
 }});
 for(auto&t:threads){t.join();}
 auto r=snapshot();
 assert(!r.normal.count&&!r.normal.bytes&&!r.host_import.count&&!r.host_import.bytes);
 assert(r.normal.peak_count>=1&&r.normal.peak_count<=4&&r.normal.peak_bytes>=4096&&r.normal.peak_bytes<=4*4096);
 assert(r.host_import.peak_count>=1&&r.host_import.peak_count<=4&&r.host_import.peak_bytes>=4096&&r.host_import.peak_bytes<=4*4096);
 puts("PASS actual BO live helpers/allocator/import/ref teardown: strict gate, failure exclusion, host separation, mapping-retained backing, peaks, concurrent atomics (ASan/UBSan)");
}
'''
# Ensure counting happens only at both successful allocation exits, never per handle/ref.
for signature, call in [('int boCreate(', 'boLiveAdd(bo, false);'), ('int boImportHost(', 'boLiveAdd(bo, true);')]:
    body=function(signature)
    assert body.count('boLiveAdd(')==1 and body.index(call)<body.index('*out = bo;')
assert 'boLiveRemove(bo);' in function('void boDestroy(')
assert 'boLive' not in function('extern "C" struct native_powervr_bo *native_powervr_bo_get(')
assert 'boLive' not in function('int NativePowerVRUserClient::gemClose(')
assert function('extern "C" int native_powervr_publish(').index('initBoLivePolicy();') < function('extern "C" int native_powervr_publish(').index('new NativePowerVRGpu')
for signature in ('void boLiveAdd(', 'void boLiveRemove(', 'void boLivePeak(', 'void boLiveCopy('):
    assert not any(token in function(signature) for token in ('IOLog(', 'IOLock', 'PE_parse_boot_argn', 'kzalloc'))
with tempfile.TemporaryDirectory(prefix='pvr-bo-live-') as directory:
    source=Path(directory)/'test.cpp';source.write_text(h)
    binary=source.with_suffix('')
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                    '-fsanitize=address,undefined','-fno-pie','-no-pie','-g','-I',str(ROOT/'package/powervr'),str(source),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True,timeout=30)
