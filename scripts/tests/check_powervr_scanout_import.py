#!/usr/bin/env python3
"""Portable ASan/UBSan fixtures for actual reserved-GOP import functions.
No GPU or SDK access. Optional --xnu-source uses pinned XNU's actual
copyCompatible function and OSPtr header instead of the default API model.
"""
from pathlib import Path
import subprocess, hashlib
root=Path(__file__).resolve().parents[2]
s=(root/'package/powervr/NativePowerVRGpu.cpp').read_text()
import argparse, tempfile
parser=argparse.ArgumentParser(description='Actual scanout import helpers with bounded XNU mapping mocks; optional real XNU compatibility code/header')
parser.add_argument('--xnu-source', type=Path)
args=parser.parse_args()
xnu=args.xnu_source
x=(xnu/'iokit/Kernel/IOMemoryDescriptor.cpp').read_text() if xnu else None
def fn(s,sig):
 st=s.index(sig);b=s.index('{',st);e=b+1;n=1
 while n:n+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[st:e]
h=r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <vector>
#include <string>
#include <cstdarg>
#include <cerrno>
#ifdef REAL_XNU_OSPTR
#include <libkern/c++/OSPtr.h>
#else
template<class T> using OSPtr=T*;
#endif
using task_t=unsigned;using IOOptionBits=unsigned;using IOByteCount=uint64_t;using IODirection=unsigned;using IOReturn=int;
using mach_vm_address_t=uint64_t;using mach_vm_size_t=uint64_t;
constexpr unsigned kIOMapReference=1,kIOMapInhibitCache=2,kIOMapAnywhere=4,kIOMapReadOnly=8,kIOMapGuardedMask=16,kIOMapCacheMask=34,kIOMapDefaultCache=0,kIOMemoryMapperNone=0,kIODirectionInOut=3,kIOMemoryPersistent=4;
constexpr int kIOReturnSuccess=0,KERN_PROTECTION_FAILURE=2;
constexpr unsigned kIODirectionOut=2;
constexpr uint64_t DRM_PVR_BO_BYPASS_DEVICE_CACHE=1,kDevicePageSize=4096,kDevicePageMask=~4095ULL;
static unsigned prepared,completed,mapsCreated,fixtures,liveMaps,allocs,failAlloc,mapAttempts,failMapAttempt;static bool nullDescriptor=false,failPhysical=false;
static bool gBoLiveEnabled, failRange, wrongLength;
static int prepareStatus,roPrepareStatus;
static bool protectHostRange,failReadOnlyRange;
static uint64_t requestedAddress,requestedSize;
static std::vector<unsigned> requestedDirections;
static uint64_t ramPhys=0x60000000;
static std::vector<std::string> failureLines,roLines;
static unsigned pidReads;
static int proc_selfpid(){++pidReads;return 56;}
static unsigned get_task_map(task_t t){return t;}
struct IOMemoryMap;struct IOMemoryDescriptor;
struct Ref {IOMemoryMap *p=nullptr;void reset(IOMemoryMap*,int);};constexpr int OSRetain=0;
struct IOMemoryMap {unsigned refs=1;task_t task=1;uint64_t fAddress=0,fOffset=0,fLength=0;unsigned fAddressMap=1,fOptions=kIOMapInhibitCache;Ref fSuperMap;IOMemoryDescriptor *md=nullptr;
 task_t getAddressTask(){return task;}uint64_t getVirtualAddress(){return fAddress;}uint64_t getLength(){return fLength;}unsigned getMapOptions(){return fOptions;}
 IOMemoryDescriptor *getMemoryDescriptor(){return nullDescriptor?nullptr:md;}void retain(){++refs;}void release();
 uint64_t getPhysicalSegment(uint64_t off,uint64_t *len,unsigned);IOMemoryMap *copyCompatible(IOMemoryMap*);
};
struct IOMemoryDescriptor {unsigned refs=1;bool device=true;uint64_t len=16384,phys=0xbe9c0000;unsigned direction=0;std::vector<IOMemoryMap*> registry;
 void retain(){++refs;}void release(){assert(refs);--refs;}uint64_t getLength(){return len+(wrongLength?4096:0);}
 IOReturn prepare(){++prepared;assert(!device);return direction==kIODirectionOut?roPrepareStatus:(protectHostRange?KERN_PROTECTION_FAILURE:prepareStatus);}IOReturn complete(){++completed;assert(!device);return 0;}
 uint64_t getPhysicalSegment(uint64_t off,uint64_t *n,unsigned){*n=len-off;return failPhysical?0:phys+off;}
 OSPtr<IOMemoryMap> createMappingInTask(task_t intoTask,mach_vm_address_t atAddress,IOOptionBits options,mach_vm_size_t offset=0,mach_vm_size_t length=0);
 static IOMemoryDescriptor *withAddressRange(uint64_t address,uint64_t n,unsigned options,task_t){requestedDirections.push_back(options&3);requestedAddress=address;requestedSize=n;if(failRange||((options&3)==kIODirectionOut&&failReadOnlyRange))return nullptr;static IOMemoryDescriptor ram;ram.device=false;ram.len=n;ram.phys=ramPhys;ram.direction=options&3;ram.retain();return &ram;}
};
void Ref::reset(IOMemoryMap *q,int){p=q;p->retain();}
void IOMemoryMap::release(){assert(refs);if(!--refs){if(fSuperMap.p)fSuperMap.p->release();if(md)md->release();--liveMaps;delete this;}}
uint64_t IOMemoryMap::getPhysicalSegment(uint64_t off,uint64_t *n,unsigned o){if(failPhysical)return 0;assert(off<fLength);uint64_t p=md->getPhysicalSegment(fOffset+off,n,o);return p;}
'''
# The default matching model follows pinned XNU copyCompatible: task map,
# access/cache flags, fixed parent VA, and descriptor-offset bounds.
# --xnu-source replaces it with that actual function and includes real OSPtr.h.
h += ('IOMemoryMap *\n'+fn(x,'IOMemoryMap::copyCompatible(')+'\n') if x else r'''
IOMemoryMap *IOMemoryMap::copyCompatible(IOMemoryMap *n){
 if(!n->task||!fAddressMap||fAddressMap!=get_task_map(n->task))return nullptr;
 if((fOptions^n->fOptions)&(kIOMapReadOnly|kIOMapGuardedMask))return nullptr;
 if((n->fOptions&kIOMapCacheMask)!=kIOMapDefaultCache&&((fOptions^n->fOptions)&kIOMapCacheMask))return nullptr;
 if(!(n->fOptions&kIOMapAnywhere)&&fAddress!=n->fAddress)return nullptr;
 if(n->fOffset<fOffset)return nullptr;auto delta=n->fOffset-fOffset;
 if(delta>fLength||n->fLength>fLength-delta)return nullptr;
 if(fLength==n->fLength&&!delta){retain();return this;}
 n->fSuperMap.reset(this,OSRetain);n->fOffset=fOffset+delta;n->fAddress=fAddress+delta;return n;
}
'''
h+=r'''
OSPtr<IOMemoryMap> IOMemoryDescriptor::createMappingInTask(task_t t,uint64_t addr,unsigned opt,uint64_t off,uint64_t length){
 ++mapAttempts;if(failMapAttempt==mapAttempts)return nullptr;
 if(!length)length=len;
 auto *request=new IOMemoryMap; ++liveMaps;request->task=t;request->fAddressMap=t;request->fAddress=addr;request->fOffset=off;request->fLength=length;request->fOptions=opt;
 for(auto *existing:registry){if(auto *found=existing->copyCompatible(request)){if(found!=request)request->release();else{found->md=this;retain();}return found;}}
 if(!(opt&kIOMapReference))++mapsCreated;
 request->release();return nullptr;
}
static IOMemoryDescriptor scan;
static IOMemoryDescriptor *handoff(){scan.retain();return &scan;}
static auto native_gop_handoff_memory=&handoff;
struct native_powervr_sg{uint64_t phys,size;};
struct NativePowerVRBoView {void release(){assert(false);}};
using IOBufferMemoryDescriptor=IOMemoryDescriptor;
static void *kzallocBytes(size_t n){++allocs;return failAlloc==allocs?nullptr:calloc(1,n);}static void kfreeBytes(void *p,size_t){free(p);}
static void IOLog(const char *format,...){
 bool ro=strstr(format,"host import RO prepare_out")!=nullptr;
 if(!ro&&!strstr(format,"host import FAIL"))return;
 char line[320];va_list args;va_start(args,format);vsnprintf(line,sizeof line,format,args);va_end(args);
 (ro?roLines:failureLines).emplace_back(line);
}
'''
h+=(root/'package/powervr/native_powervr_bo_flags.h').read_text()+'\n'
h+=fn(s,'struct native_powervr_bo {')+';\n'
h+='void boLiveRemove(native_powervr_bo *) {}\nvoid boLiveAdd(native_powervr_bo *, bool) {}\n'
for signature in ('void boDestroy(', 'IOMemoryDescriptor *hostRange(', 'int scanoutHostRange(', 'int hostImportFailure(', 'int boImportHost('):h+=fn(s,signature)+'\n'
h+=r'''
static void lookupFails(task_t task,uint64_t address,uint64_t size,int expected){IOMemoryMap *out=(IOMemoryMap*)1;assert(scanoutHostRange(task,address,size,&out)==expected&&!out);++fixtures;}
int main(){
 auto *whole=new IOMemoryMap; ++liveMaps;whole->md=&scan;scan.retain();whole->fAddress=0x100000;whole->fLength=scan.len;scan.registry={whole};
 for(unsigned variant=0;variant<4;++variant){uint64_t off=variant<2?0:variant==2?4096:12288;unsigned before=prepared;unsigned refs=scan.refs;
  native_powervr_bo *b=nullptr;uint64_t size=variant?4096:scan.len;
  assert(!boImportHost(1,0x100000+off,size,0,&b));assert(b&&!b->hostPrepared&&b->hostMap&&prepared==before&&b->flags==DRM_PVR_BO_BYPASS_DEVICE_CACHE);
  assert(b->sgCapacity==size/4096&&b->nents==1&&b->sg[0].phys==scan.phys+off&&b->sg[0].size==size);
  boDestroy(b);assert(scan.refs==refs&&whole->refs==1&&liveMaps==1&&!completed);++fixtures;
 }
 // Known GOP mismatches must never enter normal user-memory prepare.
 for(unsigned kind=0;kind<12;++kind){unsigned opts=whole->fOptions;uint64_t address=0x100000,size=scan.len;
  if(kind==0)size+=4096;if(kind==1){address+=4096;}if(kind==2){address-=4096;size=8192;}
  unsigned attribute=kind>=6?3+(kind%3):kind;if(kind>=6){size=4096;if(kind>=9)address+=4096;}
  if(attribute==3)whole->fOptions|=kIOMapReadOnly;if(attribute==4)whole->fOptions=32;if(attribute==5)whole->fOptions=kIOMapReadOnly|32;
  unsigned before=prepared,refs=scan.refs;lookupFails(1,address,size,-EFAULT);native_powervr_bo *b=nullptr;
  assert(boImportHost(1,address,size,0,&b)==-EFAULT&&!b&&prepared==before&&scan.refs==refs&&whole->refs==1&&liveMaps==1);++fixtures;whole->fOptions=opts;
 }
 lookupFails(2,0x100000,scan.len,-ENOENT);lookupFails(1,0x200000,4096,-ENOENT);
 whole->fAddressMap=0;lookupFails(1,0x100000,scan.len,-ENOENT);whole->fAddressMap=1;
 lookupFails(1,UINT64_MAX-4095,8192,-EFAULT);lookupFails(1,0x100000,0,-EFAULT);
 // BO and SG allocation errors must not prepare GOP or leak map/descriptor refs.
 for(unsigned allocation:{1u,2u}){unsigned before=prepared,refs=scan.refs;allocs=0;failAlloc=allocation;native_powervr_bo *b=nullptr;
  assert(boImportHost(1,0x100000,scan.len,0,&b)==-ENOMEM&&!b&&prepared==before&&scan.refs==refs&&whole->refs==1&&liveMaps==1);failAlloc=0;++fixtures;
 }
 for(unsigned error=0;error<2;++error){unsigned before=prepared,refs=scan.refs;nullDescriptor=error==0;failPhysical=error==1;native_powervr_bo *b=nullptr;
  assert(boImportHost(1,0x100000,scan.len,0,&b)==-EFAULT&&!b&&prepared==before&&scan.refs==refs&&whole->refs==1&&liveMaps==1);nullDescriptor=failPhysical=false;++fixtures;
 }
 // If the first reference allocation fails, a one-page reference still recognizes GOP.
 for(unsigned failed:{1u,2u}){mapAttempts=0;failMapAttempt=failed;lookupFails(1,0x100000,scan.len,-EFAULT);failMapAttempt=0;}
 mapAttempts=0;failMapAttempt=4;lookupFails(1,0x101000,4096,-EFAULT);failMapAttempt=0;
 native_powervr_bo *ram=nullptr;assert(!boImportHost(1,0x300000,4096,0,&ram));assert(ram->hostPrepared&&!ram->hostMap&&prepared==1);boDestroy(ram);assert(completed==1);++fixtures;
 // The BO keeps the issued map/descriptor alive after the original owner releases it.
 native_powervr_bo *last=nullptr;assert(!boImportHost(1,0x101000,4096,0,&last));scan.registry.clear();whole->release();assert(liveMaps==2);boDestroy(last);assert(scan.refs==1&&!liveMaps);++fixtures;
 // Exact RO fallback preserves the caller range and makes BO policy intrinsic.
 protectHostRange=true;requestedDirections.clear();native_powervr_bo *readonly=nullptr;
 unsigned prepareBefore=prepared,completeBefore=completed;
 assert(!boImportHost(1,0x105e6d000,0xf000,0,&readonly));
 assert((requestedDirections==std::vector<unsigned>{kIODirectionInOut,kIODirectionOut}));
 assert(requestedAddress==0x105e6d000 && requestedSize==0xf000);
 assert(readonly->hostPrepared && (readonly->flags&NATIVE_POWERVR_BO_READ_ONLY));
 assert(prepared==prepareBefore+2 && completed==completeBefore);
 boDestroy(readonly);assert(completed==completeBefore+1);++fixtures;
 roPrepareStatus=KERN_PROTECTION_FAILURE;readonly=nullptr;prepareBefore=prepared;completeBefore=completed;
 assert(boImportHost(1,0x300000,4096,0,&readonly)==-EFAULT&&!readonly);
 assert(prepared==prepareBefore+2&&completed==completeBefore);++fixtures;
 roPrepareStatus=0;failReadOnlyRange=true;readonly=nullptr;
 assert(boImportHost(1,0x300000,4096,0,&readonly)==-ENOMEM&&!readonly);++fixtures;
 failReadOnlyRange=false;protectHostRange=false;
 prepareStatus=0xe00002c7;requestedDirections.clear();readonly=nullptr;
 assert(boImportHost(1,0x300000,4096,0,&readonly)==-EFAULT&&!readonly);
 assert((requestedDirections==std::vector<unsigned>{kIODirectionInOut}));prepareStatus=0;++fixtures;
 assert(!mapsCreated && failureLines.empty() && !pidReads); // Default-off, even for exercised failures.
 gBoLiveEnabled=true;
 auto failed=[&](const char *stage,int expected){
  auto count=failureLines.size();native_powervr_bo *b=nullptr;
  assert(boImportHost(1,0x300000,4096,0,&b)==expected&&!b);
  assert(failureLines.size()==count+1);
  assert(failureLines.back().find(stage)!=std::string::npos);
  assert(failureLines.back().find("pid 56 err ")!=std::string::npos);
  assert(failureLines.back().find("addr 300000 size 1000")!=std::string::npos);++fixtures;
 };
 allocs=0;failAlloc=1;failed("bo_alloc",-ENOMEM);failAlloc=0;
 failRange=true;failed("host_range",-ENOMEM);failRange=false;
 auto beforeComplete=completed;prepareStatus=int(0xe00002c7u);failed("prepare",-EFAULT);
 assert(completed==beforeComplete&&failureLines.back().find("detail e00002c7")!=std::string::npos);prepareStatus=0;
 wrongLength=true;failed("host_length",-EINVAL);wrongLength=false;
 assert(completed==beforeComplete+1);
 allocs=0;failAlloc=2;failed("sg_alloc",-ENOMEM);failAlloc=0;
 failPhysical=true;failed("segment",-EFAULT);failPhysical=false;
 assert(failureLines.back().find("off 0 pa 0")!=std::string::npos);
 ramPhys=0x100000000ULL;failed("phys32",-ENOTTY);ramPhys=0x60000000;
 assert(failureLines.back().find("pa 100000000 len 1000")!=std::string::npos);
 for(unsigned i=0;i<40;++i)assert(hostImportFailure("bound",0x55,0x66,-EINVAL)==-EINVAL);
 assert(failureLines.size()==16 && pidReads==16); // Fixed bound, independent of number of callers.
 protectHostRange=true;
 for(unsigned i=0;i<9;++i){native_powervr_bo *b=nullptr;assert(!boImportHost(1,0x105e6d000,0xf000,0,&b));assert(b->flags&NATIVE_POWERVR_BO_READ_ONLY);boDestroy(b);}
 assert(roLines.size()==4&&pidReads==20);
 assert(roLines[0].find("RO prepare_out pid 56 addr 105e6d000 size f000")!=std::string::npos);
 protectHostRange=false;native_powervr_bo *rw=nullptr;
 assert(!boImportHost(1,0x300000,4096,0,&rw));assert(!(rw->flags&NATIVE_POWERVR_BO_READ_ONLY));boDestroy(rw);
 assert(roLines.size()==4&&pidReads==20);++fixtures;
 assert(!mapsCreated);
 printf("PASS %u cases: actual 3-state helper/import/destroy, full/interior SG, no prepare on known-GOP rejection, RAM fallback, failures and retained lifetimes\n",fixtures);
}
'''
with tempfile.TemporaryDirectory(prefix='gop-scanout-import-') as tmp:
 out=Path(tmp);(out/'fixture.cpp').write_text(h)
 print('GPU_sha256',hashlib.sha256(s.encode()).hexdigest(),flush=True)
 extra=['-DREAL_XNU_OSPTR','-I'+str(xnu/'libkern')] if xnu else []
 compiler='clang++' if xnu else 'c++'
 # On hosts with GCC headers for several versions, clang may need this option
 # supplied through CXXFLAGS externally; the default portable run uses c++.
 import os,shlex
 flags=shlex.split(os.environ.get('CXXFLAGS',''))
 subprocess.run([os.environ.get('CXX',compiler),*flags,'-std=c++17','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-fsanitize=address,undefined','-g',*extra,str(out/'fixture.cpp'),'-o',str(out/'fixture')],check=True)
 subprocess.run([str(out/'fixture')],check=True,timeout=30)
