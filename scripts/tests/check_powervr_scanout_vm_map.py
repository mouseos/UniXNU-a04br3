#!/usr/bin/env python3
"""Run actual VM SG helpers and map routing with ASan/UBSan and bounded mocks.
No SDK, GPU or other checkout is needed. The only host adaptation replaces
AArch64 DSB with a recorded barrier; MMU/allocator/cache operations are mocks.
"""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FW = (ROOT / 'package/powervr/NativePowerVR.cpp').read_text()
GPU = (ROOT / 'package/powervr/NativePowerVRGpu.cpp').read_text()


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    end, depth = brace + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


h = r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <cerrno>
#include <cstdio>
using u32=uint32_t;
constexpr int GFP_KERNEL=0,DMA_BIDIRECTIONAL=0;
constexpr uint64_t DRM_PVR_BO_BYPASS_DEVICE_CACHE=1;
struct pvr_mmu_context{};
struct native_powervr_vm{pvr_mmu_context *mmu;};
struct native_powervr_sg{uint64_t phys,size;};
struct scatterlist{uint64_t dma_address;unsigned dma_length;};
struct sg_table{scatterlist *sgl;unsigned nents,orig_nents;};
struct pvr_mmu_op_context{};
static int gPvr;
static pvr_mmu_op_context op;
static unsigned allocs,frees,creates,maps,destroys,flushes,dataSyncs,tableSyncs,barriers,fixtures;
static int errorStage;
static uint64_t expectedVA=0xda00040000ULL,expectedOffset=4096,expectedSize=4096,expectedFlags;
static native_powervr_vm *expectedVM;
static const native_powervr_sg spans[]={{0xbe9c0000,8192},{0xbe9c2000,4096}};
static std::string events;
static void *kcalloc(size_t n,size_t size,int){++allocs;events+='A';return errorStage==1?nullptr:calloc(n,size);}
static void kfree(void *p){assert(p);++frees;events+='F';free(p);}
static void testBarrier(){++barriers;events+='B';}
static void dma_sync_sg_for_device(void *,scatterlist *s,int n,int){
 if(s[0].dma_address==0x60000000){++tableSyncs;events+='T';return;}
 ++dataSyncs;events+='D';assert(n==2);for(int i=0;i<n;++i)assert(s[i].dma_address==spans[i].phys&&s[i].dma_length==spans[i].size);
}
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define PTR_ERR(p) ((intptr_t)(p))
static pvr_mmu_op_context *pvr_mmu_op_context_create(pvr_mmu_context *m,sg_table *s,uint64_t va,uint64_t offset,uint64_t size){
 ++creates;events+='C';assert(m==expectedVM->mmu&&va==expectedVA&&offset==expectedOffset&&size==expectedSize);
 assert(s->nents==2&&s->orig_nents==2);for(unsigned i=0;i<2;++i)assert(s->sgl[i].dma_address==spans[i].phys&&s->sgl[i].dma_length==spans[i].size);
 return errorStage==2?(pvr_mmu_op_context*)(intptr_t)-21:&op;
}
static int pvr_mmu_map(pvr_mmu_op_context *p,uint64_t size,uint64_t flags,uint64_t va){
 ++maps;events+='M';assert(p==&op&&size==expectedSize&&flags==expectedFlags&&va==expectedVA);
 // Model the separate owned-RAM page-table synchronization, never the scanout.
 scatterlist table{0x60000000,4096};dma_sync_sg_for_device(nullptr,&table,1,DMA_BIDIRECTIONAL);
 return errorStage==3?-22:0;
}
static void pvr_mmu_op_context_destroy(pvr_mmu_op_context *p){assert(p==&op);++destroys;events+='X';}
static int pvr_mmu_flush_exec(int *p,bool wait){assert(p==&gPvr&&wait);++flushes;events+='Q';return errorStage==4?-23:0;}
'''
h += function(FW, 'static int vmMapSg(').replace('__asm__ volatile("dsb sy" ::: "memory");', 'testBarrier();') + '\n'
for signature in ('extern "C" int native_powervr_vm_map_sg(',
                  'extern "C" int native_powervr_vm_map_scanout_sg('):
    h += function(FW, signature) + '\n'
h += r'''
struct native_powervr_bo{const native_powervr_sg *sg;uint32_t nents;uint64_t flags;void *hostMap;unsigned refs=1;};
struct Mapping {uint64_t addr,range,offset;native_powervr_bo *bo;};
struct native_powervr_vm_context{native_powervr_vm *vm;bool quarantined=false;Mapping entries[4]{};Mapping *maps=entries;uint32_t count=0;};
static int vmReserve(native_powervr_vm_context *,unsigned n){assert(n==2);return 0;}
static uint32_t vmFirstEndingAfter(const native_powervr_vm_context *c,uint64_t a){unsigned i=0;while(i<c->count&&c->maps[i].addr+c->maps[i].range<=a)++i;return i;}
static void vmInsertAt(native_powervr_vm_context *c,unsigned i,Mapping m){assert(i==c->count&&c->count<4);c->maps[c->count++]=m;}
static void vmRemoveAt(native_powervr_vm_context *,unsigned){assert(false);}
static native_powervr_bo *native_powervr_bo_get(native_powervr_bo *b){++b->refs;return b;}
static void native_powervr_bo_put(native_powervr_bo *){assert(false);}
static int native_powervr_vm_unmap(native_powervr_vm *,uint64_t,uint64_t){assert(false);return -1;}
static void IOLog(const char *,...){}
'''
h += function(GPU, 'int vmMapLocked(') + '\n'
h += r'''
using MapFn=int (*)(native_powervr_vm*,uint64_t,const native_powervr_sg*,uint32_t,uint64_t,uint64_t,uint64_t);
static void reset(){allocs=frees=creates=maps=destroys=flushes=dataSyncs=tableSyncs=barriers=0;errorStage=0;events.clear();}
static int call(MapFn fn,uint64_t flags){expectedFlags=flags;return fn(expectedVM,expectedVA,spans,2,expectedOffset,expectedSize,flags);}
int main(){
 pvr_mmu_context mmu;native_powervr_vm vm{&mmu};expectedVM=&vm;
 for(bool scanout:{false,true})for(unsigned stage=0;stage<=4;++stage){
  reset();errorStage=stage;uint64_t flags=scanout?DRM_PVR_BO_BYPASS_DEVICE_CACHE:0;
  int r=call(scanout?native_powervr_vm_map_scanout_sg:native_powervr_vm_map_sg,flags);
  int expected=stage==1?-ENOMEM:stage==2?-21:stage==3?-22:stage==4?-23:0;assert(r==expected);
  assert(allocs==1&&frees==(stage==1?0u:1u));
  assert(dataSyncs==(!scanout&&stage!=1?1u:0u)&&barriers==(scanout&&stage!=1?1u:0u));
  assert(creates==(stage==1?0u:1u)&&maps==(stage==1||stage==2?0u:1u)&&destroys==maps&&tableSyncs==maps);
  assert(flushes==(stage==0||stage==4?1u:0u));
  if(!stage)assert(events==std::string(scanout?"ABCMTXFQ":"ADCMTXFQ"));
  ++fixtures;
 }
 // Neither address range nor a caller flag selects the cache bypass route.
 reset();assert(!call(native_powervr_vm_map_sg,DRM_PVR_BO_BYPASS_DEVICE_CACHE)&&dataSyncs==1&&!barriers);++fixtures;
 reset();assert(call(native_powervr_vm_map_scanout_sg,0)==-EINVAL&&!allocs&&!dataSyncs&&!barriers);++fixtures;
 for(MapFn fn:{native_powervr_vm_map_sg,native_powervr_vm_map_scanout_sg}){
  reset();assert(fn(nullptr,expectedVA,spans,2,expectedOffset,expectedSize,1)==-EINVAL&&!allocs);++fixtures;
  reset();assert(fn(&vm,expectedVA,nullptr,2,expectedOffset,expectedSize,1)==-EINVAL&&!allocs);++fixtures;
  reset();assert(fn(&vm,expectedVA,spans,0,expectedOffset,expectedSize,1)==-EINVAL&&!allocs);++fixtures;
 }
 for(bool authenticated:{false,true})for(bool bypass:{false,true})for(bool failMap:{false,true}){
  reset();errorStage=failMap?3:0;expectedFlags=bypass?DRM_PVR_BO_BYPASS_DEVICE_CACHE:0;
  native_powervr_bo bo{spans,2,expectedFlags,authenticated?(void*)1:nullptr};native_powervr_vm_context ctx{&vm};
  int r=vmMapLocked(&ctx,&bo,expectedOffset,expectedVA,expectedSize);
  int expected=authenticated&&!bypass?-EINVAL:failMap?-22:0;assert(r==expected);
  assert(ctx.count==(r?0u:1u)&&bo.refs==(r?1u:2u));
  assert(dataSyncs==(!authenticated?1u:0u));
  assert(barriers==(authenticated&&bypass?1u:0u));
  if(!r){assert(ctx.maps[0].addr==expectedVA&&ctx.maps[0].range==expectedSize&&ctx.maps[0].offset==expectedOffset&&ctx.maps[0].bo==&bo);}
  ++fixtures;
 }
 // A quarantined VM rejects both ordinary and scanout mapping before any side effect.
 for(bool authenticated:{false,true}){
  reset(); native_powervr_bo bo{spans,2,DRM_PVR_BO_BYPASS_DEVICE_CACHE,authenticated?(void*)1:nullptr};
  native_powervr_vm_context ctx{&vm};ctx.quarantined=true;
  assert(vmMapLocked(&ctx,&bo,expectedOffset,expectedVA,expectedSize)==-EIO);
  assert(ctx.count==0&&bo.refs==1&&!allocs&&!creates&&!maps&&!flushes&&!dataSyncs&&!barriers);++fixtures;
 }
 printf("PASS %u actual-function cases: regular DMA retained, authenticated scanout barrier-only, BYPASS required, MMU backing sync/flush retained, args/failures/refcounts/routing\n",fixtures);
}
'''
with tempfile.TemporaryDirectory(prefix='powervr-scanout-vm-') as tmp:
    path = Path(tmp)
    (path / 'fixture.cpp').write_text(h)
    print('FW_sha256', hashlib.sha256(FW.encode()).hexdigest(), flush=True)
    print('GPU_sha256', hashlib.sha256(GPU.encode()).hexdigest(), flush=True)
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-g', str(path / 'fixture.cpp'),
                    '-o', str(path / 'fixture')], check=True)
    subprocess.run([str(path / 'fixture')], check=True, timeout=30)
