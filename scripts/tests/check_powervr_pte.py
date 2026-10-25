#!/usr/bin/env python3
"""実診断関数を抽出し、geometry・参照寿命・ロック分離をASan/UBSanで検証する。"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PVR = ROOT / 'package/powervr'
GPU = (PVR / 'NativePowerVRGpu.cpp').read_text()
JOBS = (PVR / 'NativePowerVRJobs.cpp').read_text()
WALK = (PVR / 'NativePowerVRPte.c').read_text()


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


common = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <cstdarg>
#include <cstdio>
#include <cerrno>
#include "native_powervr_pte.h"
'''
geometry = common + r'''
using tt_entry_t=uint64_t;
struct Level {uint64_t offmask,shift,index_mask;};
struct pt_attr_t {unsigned root,leaf; Level levels[4];};
struct Pmap {uint64_t ttep,min,max;tt_entry_t *tte;pt_attr_t *geometry;};
using pmap_t=Pmap*;
struct Map {pmap_t pmap;}; using vm_map_t=Map*;
#define vm_map_pmap(m) ((m)->pmap)
#define PMAP_LOCK_SHARED 1
#define ARM_TTE_VALID 1ULL
#define ARM_TTE_TYPE_MASK 2ULL
#define ARM_TTE_TYPE_BLOCK 0ULL
#define ARM_TTE_PA_MASK 0x0000fffffffff000ULL
#define ARM_TTE_TABLE_MASK ARM_TTE_PA_MASK
#define ARM_PTE_ATTRINDXMASK (7ULL<<2)
#define ARM_PTE_ATTRINDX(x) ((uint64_t)(x)<<2)
#define ARM_PTE_SHMASK (3ULL<<8)
#define ARM_PTE_SH(x) ((uint64_t)(x)<<8)
static bool held; static unsigned locks,unlocks;
void pmap_lock(pmap_t,int){assert(!held);held=true;++locks;}
void pmap_unlock(pmap_t,int){assert(held);held=false;++unlocks;}
uint64_t pte_mair(){assert(held);return 0x4400ff;}
pt_attr_t *pmap_get_pt_attr(pmap_t p){assert(held);return p->geometry;}
unsigned pt_attr_root_level(const pt_attr_t*g){return g->root;}
unsigned pt_attr_leaf_level(const pt_attr_t*g){return g->leaf;}
uint64_t pt_attr_ln_offmask(const pt_attr_t*g,unsigned l){return g->levels[l].offmask;}
unsigned ttn_index(const pt_attr_t*g,uint64_t va,unsigned l){return (va>>g->levels[l].shift)&g->levels[l].index_mask;}
alignas(16384) uint64_t tables[4][2048];
uintptr_t phystokv(uint64_t p){assert(held&&p>=0x10000&&p<=0x40000);return reinterpret_cast<uintptr_t>(tables[(p>>16)-1]);}
'''
geometry += function(WALK, 'void native_powervr_pte_walk(')
geometry += r'''
int main(){
 for(unsigned shift:{12u,14u}){
  pt_attr_t g{};g.root=0;g.leaf=3;
  for(unsigned l=0;l<4;++l){g.levels[l].shift=shift+(3-l)*(shift-3);
   g.levels[l].offmask=(1ULL<<g.levels[l].shift)-1;g.levels[l].index_mask=(1ULL<<(shift-3))-1;}
  Pmap p{0x10000,0,1ULL<<48,tables[0],&g};Map map{&p};
  uint64_t va=0x12345678ULL;
  memset(tables,0,sizeof(tables));
  for(unsigned l=0;l<3;++l)tables[l][ttn_index(&g,va,l)]=((l+2)*0x10000)|3;
  uint64_t base=0x80000000ULL;
  tables[3][ttn_index(&g,va,3)]=base|3|ARM_PTE_ATTRINDX(1)|ARM_PTE_SH(2);
  native_powervr_pte_alias r{};native_powervr_pte_walk(&map,va,&r);
  assert(!r.status&&r.pa==(base|(va&g.levels[3].offmask))&&r.level==3&&r.attr==1&&r.sh==2&&r.mair==0x4400ff);
  // L1/L2 blocks must stop the walk, including a 16 KiB geometry.
  for(unsigned l:{1u,2u}){
   auto &e=tables[l][ttn_index(&g,va,l)];uint64_t saved=e;
   e=(base&~g.levels[l].offmask)|1|ARM_PTE_ATTRINDX(0)|ARM_PTE_SH(3);
   native_powervr_pte_walk(&map,va,&r);
   assert(!r.status&&r.level==l&&r.pa==((base&~g.levels[l].offmask)|(va&g.levels[l].offmask))&&r.attr==0&&r.sh==3);
   e=saved;
  }
  auto &leaf=tables[3][ttn_index(&g,va,3)];leaf=0;native_powervr_pte_walk(&map,va,&r);assert(r.status==1);
  leaf=1;native_powervr_pte_walk(&map,va,&r);assert(r.status==1); // reserved L3 block
  tables[0][ttn_index(&g,va,0)]=1;native_powervr_pte_walk(&map,va,&r);assert(r.status==1); // reserved L0
  native_powervr_pte_walk(&map,p.max,&r);assert(r.status==1);
  native_powervr_pte_walk(nullptr,va,&r);assert(r.status==1);
  assert(!held&&locks==unlocks);
 }
}
'''

ownership = common + r'''
struct Lock{bool held=false;}; static Lock conn,vmLock,jobsLock;
void IOLockLock(Lock*l){assert(!l->held);l->held=true;}
void IOLockUnlock(Lock*l){assert(l->held);l->held=false;}
static bool supplied;static unsigned arg,walks,mappingCalls,logs;
bool PE_parse_boot_argn(const char*k,void*p,unsigned n){assert(!strcmp(k,"a04br3-gpu-pte")&&n==4);memcpy(p,&arg,4);return supplied;}
constexpr unsigned kIOMapReference=1,kIOMapAnywhere=2,kIOMapDefaultCache=0;
void *current_task(){return (void*)1;}static void *kernel_task=(void*)2;
struct IOMemoryMap{
 unsigned refs=1;uint64_t addr=0x100000;unsigned options=0xabc;
 void release(){assert(refs);--refs;}uint64_t getVirtualAddress(){return addr;}
 unsigned getMapOptions(){return options;}
} user;
struct View{
 bool present=true;
 IOMemoryMap *createMappingInTask(void*t,uint64_t a,unsigned opts,uint64_t off,uint64_t size){
  assert(t==current_task()&&!a&&opts==(kIOMapReference|kIOMapAnywhere)&&!off&&size==0x4000);
  ++mappingCalls;if(!present)return nullptr;++user.refs;return &user;
 }
}view;
struct native_powervr_sg{uint64_t phys,size;};
struct native_powervr_bo{unsigned refs=1;uint64_t size=0x4000;void *md=(void*)1,*host=nullptr,*cpu=(void*)0xdead0000;
 View *view;native_powervr_sg *sg;unsigned nents=2;};
struct native_powervr_bo_mapping{native_powervr_bo *bo;uint64_t va_addr,va_range,bo_offset;};
struct Mapping{uint64_t addr,range,offset;native_powervr_bo *bo;};
struct native_powervr_vm_context{unsigned refs=1;Lock *lock=&vmLock;unsigned count=3;Mapping maps[3];}vm;
native_powervr_sg sg[]={{0x80000000,0x2000},{0x81000000,0x2000}};
native_powervr_bo bo;
uint64_t gPhysBase=0x80000000,gPhysSize=0x2000000;
void *kzallocBytes(size_t n){return calloc(1,n);}void kfreeBytes(void*p,size_t){free(p);}
native_powervr_vm_context *native_powervr_vm_context_get(native_powervr_vm_context*v){++v->refs;return v;}
void native_powervr_vm_context_put(native_powervr_vm_context*v){if(v){assert(v->refs);--v->refs;}}
void native_powervr_bo_put(native_powervr_bo*b){if(b){assert(b->refs);--b->refs;}}
uint32_t vmFirstEndingAfter(native_powervr_vm_context*v,uint64_t a){unsigned i=0;while(i<v->count&&v->maps[i].addr+v->maps[i].range<=a)++i;return i;}
int native_powervr_vm_find_bo(native_powervr_vm_context*v,uint64_t a,native_powervr_bo_mapping*out){
 IOLockLock(v->lock);unsigned n=vmFirstEndingAfter(v,a);bool ok=n<v->count&&v->maps[n].addr<=a;
 if(ok){auto&m=v->maps[n];++m.bo->refs;*out={m.bo,m.addr,m.range,m.offset};}IOLockUnlock(v->lock);return ok?0:-1;}
static unsigned mapRefs;
extern "C" void *native_powervr_pte_map_get(void*t){++mapRefs;return t;}
extern "C" void native_powervr_pte_map_put(void*t){if(t){assert(mapRefs);--mapRefs;}}
uintptr_t phystokv(uint64_t p){return p+0x100000000ULL;}
extern "C" void native_powervr_pte_walk(void*m,uint64_t va,native_powervr_pte_alias*out){
 assert(m&&!conn.held&&!vmLock.held&&!jobsLock.held);++walks;out->va=va;out->status=0;out->entry=0x123;}
class NativePowerVRUserClient{public:Lock *lock_=&conn;bool closing_=false;
 static int ptePrephase(void*,native_powervr_pte_snapshot**,native_powervr_pte_record**,uint32_t);};
'''
ownership += function(GPU, 'extern "C" int native_powervr_pte_enabled(')
ownership += function(GPU, 'struct native_powervr_pte_snapshot') + ';\n'
for sig in ('extern "C" void native_powervr_pte_put(',
            'extern "C" native_powervr_pte_snapshot *native_powervr_pte_prepare(',
            'extern "C" void native_powervr_pte_capture(',
            'extern "C" void native_powervr_pte_validate(',
            'int NativePowerVRUserClient::ptePrephase('):
    ownership += function(GPU, sig) + '\n'
ownership += r'''
struct Ctx{uint32_t ctx_id=7;};
struct Job{bool pte_requested=true;uint32_t seqno=1,id=2;uint64_t fence=3;Ctx *ctx;native_powervr_pte_record *pre_pte;};
struct {Lock *lock=&jobsLock;}gJobs;
native_powervr_pte_record gPtes[2];uint32_t gPteCount=0,gPtePrepared=0;bool gPteDone[2],gPtePrinted;
void native_powervr_log(const char*,...){assert(!conn.held&&!vmLock.held&&!jobsLock.held);++logs;}
'''
for sig in ('bool reservePte()', 'void capturePte(', 'void pteCompleted(', 'void drainPtes('):
    ownership += function(JOBS, sig) + '\n'
ownership += r'''
int main(){
 bo.view=&view;bo.sg=sg;
 vm.maps[0]={0x10000,0x1000,0x3000,&bo};
 vm.maps[1]={0xda00001000ULL,0x1000,0,&bo};
 vm.maps[2]={0xe000001000ULL,0x1000,0x2000,&bo};
 for(unsigned v:{0u,1u,2u,~0u}){arg=v;supplied=false;assert(!native_powervr_pte_enabled());supplied=true;assert(bool(native_powervr_pte_enabled())==(v==1));}
 arg=0;assert(!native_powervr_pte_prepare(&vm,0x10100)&&!mappingCalls&&!mapRefs);
 arg=1;IOLockLock(&conn);auto*s=native_powervr_pte_prepare(&vm,0x10100);assert(s&&user.refs==4&&bo.refs==4&&vm.refs==2&&mapRefs==2);
 NativePowerVRUserClient client;native_powervr_pte_record r{};r.submission=42;r.index=1;
 native_powervr_pte_snapshot*ss[]={s};native_powervr_pte_record*rr[]={&r};
 assert(!client.ptePrephase(&client,ss,rr,1)&&conn.held&&walks==9&&!logs);
 assert(r.target[1].bo_offset==0x3100&&r.target[1].expected_pa==0x81001100);
 assert(r.target[1].user.va==user.addr+0x3100&&r.target[1].kernel.va==0xdead3100);
 vm.maps[0].offset=0;native_powervr_pte_validate(s,&r);assert(r.target[1].status==4);
 IOLockUnlock(&conn);native_powervr_pte_put(s);assert(user.refs==1&&bo.refs==1&&vm.refs==1&&!mapRefs);
 // An absent user map must not create one. Kernel/physmap evidence still survives.
 view.present=false;s=native_powervr_pte_prepare(&vm,0x10100);native_powervr_pte_record missing{};
 native_powervr_pte_capture(s,&missing);assert(missing.target[0].status==3&&missing.target[0].user.status==1);
 assert(!missing.target[0].kernel.status);native_powervr_pte_put(s);assert(user.refs==1&&bo.refs==1&&!mapRefs);
 bo.host=(void*)1;s=native_powervr_pte_prepare(&vm,0x10100);unsigned before=walks;
 native_powervr_pte_capture(s,&missing);assert(walks==before&&missing.target[0].status==2);native_powervr_pte_put(s);
 // Closing while unlocked must not permit a subsequent kick.
 IOLockLock(&conn);client.closing_=true;assert(client.ptePrephase(&client,nullptr,nullptr,0)==-ENODEV&&conn.held);
 IOLockUnlock(&conn);
 assert(reservePte()&&reservePte()&&!reservePte()&&!reservePte()&&gPtePrepared==2);
 Ctx ctx;Job j{true,1,2,3,&ctx,&r};IOLockLock(&jobsLock);capturePte(&j);assert(walks==before);
 assert(gPtes[0].submission==42&&gPtes[0].index==1&&gPtes[0].seqno==1);
 pteCompleted(&j);IOLockUnlock(&jobsLock);drainPtes(false);assert(!logs);
 j.seqno=2;j.fence=4;IOLockLock(&jobsLock);capturePte(&j);pteCompleted(&j);IOLockUnlock(&jobsLock);
 drainPtes(false);assert(logs&&gPtePrinted);unsigned old=logs;drainPtes(true);assert(logs==old);
 assert(user.refs==1&&bo.refs==1&&vm.refs==1&&!mapRefs);
}
'''

# Ensure the actual hook stays above the first jobs lock and the kick path is values-only.
submit = function(JOBS, 'extern "C" int native_powervr_submit_jobs(')
assert submit.index('pte_phase(pte_cookie') < submit.index('IOLockLock(gJobs.lock)')
capture = function(JOBS, 'void capturePte(')
assert 'native_powervr_pte_capture(' not in capture and 'IOLock' not in capture
for forbidden in ('flush_dcache(', 'dc civac', 'ttbr', 'IOLog(', 'printf('):
    assert forbidden not in function(WALK, 'void native_powervr_pte_walk(').lower()
assert 'kIOMapReference | kIOMapAnywhere | kIOMapDefaultCache' in GPU

# The real macOS failure exposed a BSD-vs-MACH compiler-record mismatch that
# API mocks cannot detect. Require the actual pinned pmap record and prelude.
builder = (ROOT / 'scripts/build-a04br3-board.py').read_text()
assert "pmap_record = record('osfmk/DEVELOPMENT/pmap.o.json')" in builder
assert "compile_one(pmap_record, KIT / 'powervr' / 'NativePowerVRPte.c'" in builder
assert "c_record = record('bsd/DEVELOPMENT/kern_exec.o.json')" in builder
assert 'compile_one(c_record, KIT / name, C_FLAGS)' in builder
prelude = ('mach_assert.h', 'mach_ldebug.h', 'kern/thread.h', 'kern/sched.h',
           'kern/spl.h', 'vm/pmap.h', 'vm/vm_map.h', 'arm/misc_protos.h',
           'arm/pmap/pmap_internal.h')
positions = [WALK.index(f'#include <{name}>') for name in prelude]
assert positions == sorted(positions)
assert '#undef' not in WALK and '-Wno-' not in builder[builder.index('pmap_record ='):builder.index('# NativePowerVR (package/powervr)')]

with tempfile.TemporaryDirectory(prefix='pvr-pte-') as directory:
    for name, source in (('geometry', geometry), ('ownership', ownership)):
        path = Path(directory) / f'{name}.cpp'
        path.write_text(source.replace('#include <cassert>', '#include <initializer_list>\n#include <cassert>'))
        binary = path.with_suffix('')
        subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-g', '-I', str(PVR),
                        str(path), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
        print(f'{name}: PASS (ASan/UBSan)')
    # Compile the complete production .c (including MAIR_EL1) for its real target.
    # These are API-contract mocks, NOT proof of compatibility with an SDK's
    # generated private headers. The owner build must provide that confirmation.
    headers = Path(directory) / 'headers'
    for name in (*prelude, 'kern/task.h'):
        path = headers / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('')
    (headers / 'kern/task.h').write_text(r'''
#include <stdint.h>
typedef uint64_t tt_entry_t;
typedef void *task_t;
typedef struct {unsigned root,leaf;} pt_attr_t;
typedef struct {uint64_t ttep,min,max;tt_entry_t *tte;} *pmap_t;
typedef struct {pmap_t pmap;} *vm_map_t;
extern vm_map_t get_task_map_reference(task_t);
extern void vm_map_deallocate(vm_map_t);
extern void pmap_lock(pmap_t,int),pmap_unlock(pmap_t,int);
extern const pt_attr_t *pmap_get_pt_attr(pmap_t);
extern unsigned pt_attr_root_level(const pt_attr_t*),pt_attr_leaf_level(const pt_attr_t*);
extern unsigned ttn_index(const pt_attr_t*,uint64_t,unsigned);
extern uint64_t pt_attr_ln_offmask(const pt_attr_t*,unsigned);
extern uintptr_t phystokv(uint64_t);
#define vm_map_pmap(m) ((m)->pmap)
#define PMAP_LOCK_SHARED 1
#define ARM_TTE_VALID 1ULL
#define ARM_TTE_TYPE_MASK 2ULL
#define ARM_TTE_TYPE_BLOCK 0ULL
#define ARM_TTE_PA_MASK 0x0000fffffffff000ULL
#define ARM_TTE_TABLE_MASK ARM_TTE_PA_MASK
#define ARM_PTE_ATTRINDXMASK (7ULL<<2)
#define ARM_PTE_ATTRINDX(x) ((uint64_t)(x)<<2)
#define ARM_PTE_SHMASK (3ULL<<8)
#define ARM_PTE_SH(x) ((uint64_t)(x)<<8)
''')
    obj = Path(directory) / 'pte.o'
    subprocess.run(['clang', '-target', 'arm64-apple-ios17.0', '-ffreestanding', '-std=gnu11',
                    '-Wall', '-Wextra', '-Werror', '-I', str(headers), '-c',
                    str(PVR / 'NativePowerVRPte.c'), '-o', str(obj)], check=True)
    assert obj.read_bytes()[:4] == bytes.fromhex('cffaedfe')
    print('arm64 C + MAIR_EL1: PASS (API mocks; pinned SDK build still required)')
