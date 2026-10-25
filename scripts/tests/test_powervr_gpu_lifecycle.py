"""修正した実関数を抽出し、カーネル/MMIOのモックで寿命と順序を検証する。実機アクセスなし。"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

DEVICE = Path(__file__).resolve().parents[2]
GPU = (DEVICE / 'package/powervr/NativePowerVRGpu.cpp').read_text()
JOBS = (DEVICE / 'package/powervr/NativePowerVRJobs.cpp').read_text()
GOP = (DEVICE.parent / 'UniXNU/drivers/NativeMobileDisplay/NativeGOPPresenter.cpp').read_text()


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def run_cpp(body):
    with tempfile.TemporaryDirectory(prefix='powervr-lifecycle-') as tmp:
        source, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
        source.write_text(body)
        subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-g', str(source), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=30)


class GpuLifecycleTest(unittest.TestCase):
    def test_arm64_cache_instructions(self):
        stub = '''using size_t=__SIZE_TYPE__; using uintptr_t=__UINTPTR_TYPE__;
using uint64_t=unsigned long long; using uint32_t=unsigned int; using vm_offset_t=uintptr_t;
constexpr uint64_t kDevicePageSize=4096; constexpr int ENOTTY=25;
struct native_powervr_sg{uint64_t phys,size;};
struct native_powervr_bo{native_powervr_sg *sg;uint32_t nents;};
struct native_powervr_host_sync{native_powervr_bo **bos;uint32_t count,capacity;};
extern uint64_t gPhysBase,gPhysSize;extern vm_offset_t phystokv(uint64_t);
extern void disable_preemption();extern void enable_preemption();
'''
        with tempfile.TemporaryDirectory(prefix='powervr-arm64-') as tmp:
            obj = Path(tmp) / 'cache.o'
            subprocess.run(['clang++', '-target', 'arm64-apple-ios17.0', '-std=c++17', '-ffreestanding',
                            '-Wall', '-Wextra', '-Werror', '-x', 'c++', '-c', '-', '-o', str(obj)],
                           input=stub + function(GPU, 'extern "C" int native_powervr_hosts_sync('), text=True, check=True)
            self.assertEqual(obj.read_bytes()[:4], bytes.fromhex('cffaedfe'))

    def test_host_snapshot_cache_and_fence(self):
        snapshot = function(GPU, 'extern "C" int native_powervr_vm_hosts_snapshot(')
        put = function(GPU, 'extern "C" void native_powervr_hosts_put(')
        sync = function(GPU, 'extern "C" int native_powervr_hosts_sync(')
        # CPU cache instructions alone are replaced by instrumented operations on the host.
        sync = sync.replace('__asm__ volatile("mrs %0, ctr_el0" : "=r"(ctr));', 'ctr = ctrValue;')
        sync = sync.replace('__asm__ volatile("dsb sy" ::: "memory");', 'barriers++;')
        sync = sync.replace('__asm__ volatile("dc civac, %0" :: "r"(p) : "memory");', 'cacheOp(p);')
        harness = r'''
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <vector>
using vm_offset_t=uintptr_t;
struct IOLock {bool held=false;};
void IOLockLock(IOLock *p){assert(!p->held);p->held=true;}
void IOLockUnlock(IOLock *p){assert(p->held);p->held=false;}
int allocations=0,failAllocation=0;
void *kzallocBytes(size_t n){if(++allocations==failAllocation)return nullptr;return calloc(1,n);}
void kfreeBytes(void *p,size_t){free(p);}
struct native_powervr_sg {uint64_t phys,size;};
struct native_powervr_bo {int refs;void *host;native_powervr_sg *sg;uint32_t nents;};
native_powervr_bo *native_powervr_bo_get(native_powervr_bo *b){++b->refs;return b;}
void native_powervr_bo_put(native_powervr_bo *b){assert(b->refs>0);--b->refs;}
struct Mapping {native_powervr_bo *bo;};
struct native_powervr_vm_context {IOLock *lock;uint32_t count;Mapping *maps;};
struct native_powervr_host_sync {native_powervr_bo **bos;uint32_t count,capacity;};
constexpr uint64_t kDevicePageSize=4096;
uint64_t gPhysBase=0x100000,gPhysSize=4096*128,ctrValue=4ULL<<16;
unsigned cacheOps=0,barriers=0,batches=0;bool preemptDisabled=false;
void disable_preemption(){assert(!preemptDisabled);preemptDisabled=true;++batches;}
void enable_preemption(){assert(preemptDisabled);preemptDisabled=false;}
vm_offset_t phystokv(uint64_t p){assert(p>=gPhysBase&&p-gPhysBase<gPhysSize);return p+0x10000000;}
void cacheOp(uintptr_t p){assert(preemptDisabled&&p>=0x10100000);++cacheOps;}
''' + put + snapshot + sync + r'''
struct Queue {bool reached=false;};
struct Context {int faulty=0;};
struct Job {native_powervr_host_sync *hosts=nullptr;bool hosts_completed=false,failed=false;uint64_t fence=1;
 Queue *queue=nullptr;uint32_t seqno=1;Context *ctx=nullptr;uint32_t type=0,id=1;};
struct list_head {std::vector<Job *> items;};
struct native_powervr_jobs_file {uint64_t nextFence=2;list_head jobs;bool failed=false;};
#define list_for_each_entry(j,h,m) for(auto *cursor:(h)->items) if((j=cursor))
IOLock jobsLock;
struct {IOLock *lock=&jobsLock;} gJobs;
constexpr uint32_t NativePowerVRTunnelMagic=0x4e544e4c;
uint64_t ticks=0;uint64_t nowAbs(){return ticks;}
void nanoseconds_to_absolutetime(uint64_t n,uint64_t *p){*p=n;}
void IOSleep(int){++ticks;}
int atomic_read(int *p){return *p;}
bool queueReached(Queue *q,uint32_t){return q->reached;}
bool wasFailed(native_powervr_jobs_file *f,uint64_t){return f->failed;}
constexpr uint32_t DRM_PVR_JOB_TYPE_FRAGMENT=1;
unsigned renderDumps=0;
void native_powervr_log(const char *,...){}
void native_powervr_dump_render_regs(){++renderDumps;}
void pteCompleted(const Job *){} // PTE completion bookkeeping is tested by check_powervr_pte.py.
''' + function(JOBS, 'void complete_job_hosts(') + function(JOBS, 'extern "C" int native_powervr_fence_wait(') + r'''
int main(){
 IOLock lock;native_powervr_sg spans[]={{gPhysBase,65*4096},{0xbe9c0000,4096}};
 native_powervr_bo host{1,&lock,spans,2},plain{1,nullptr,nullptr,0};
 Mapping maps[]={{&host},{&host},{&plain}};native_powervr_vm_context vm{&lock,3,maps};
 native_powervr_host_sync *held=nullptr;
 assert(native_powervr_vm_hosts_snapshot(&vm,&held)==0&&held&&held->count==1&&host.refs==2);
 vm.count=0;native_powervr_bo_put(&host); // handles/mappings go away; the job still pins the import
 assert(host.refs==1&&native_powervr_hosts_sync(held)==0);
 assert(cacheOps==65*4096/64&&batches==3&&!preemptDisabled); // scanout is never passed to phystokv
 Queue q;Context ctx;Job job;job.hosts=held;job.queue=&q;job.ctx=&ctx;job.type=DRM_PVR_JOB_TYPE_FRAGMENT;
 native_powervr_jobs_file file;file.jobs.items.push_back(&job);
 auto before=cacheOps;
 assert(native_powervr_fence_wait(&file,1,0)==-ETIMEDOUT&&cacheOps==before&&!job.hosts_completed);
 q.reached=true;
 assert(native_powervr_fence_wait(&file,1,0)==0&&job.hosts_completed&&cacheOps==before+65*4096/64);
 assert(native_powervr_fence_wait(&file,1,0)==0&&cacheOps==before+65*4096/64);
 complete_job_hosts(&job);assert(cacheOps==before+65*4096/64&&renderDumps==1); // worker and waiter cannot sync twice
 native_powervr_hosts_put(held);assert(host.refs==0&&plain.refs==1);
 host.refs=1;vm.count=3;
 for(int n=1;n<=2;n++){allocations=0;failAllocation=n;held=nullptr;
  assert(native_powervr_vm_hosts_snapshot(&vm,&held)==-ENOMEM&&!held&&host.refs==1&&!lock.held);}
 failAllocation=0;assert(native_powervr_vm_hosts_snapshot(&vm,&held)==0);
 job.hosts=held;job.hosts_completed=false;job.failed=false;ctrValue=11ULL<<16;
 assert(native_powervr_fence_wait(&file,1,0)==-EIO&&job.failed&&!preemptDisabled);
 native_powervr_hosts_put(held);assert(host.refs==1);
}
'''
        run_cpp(harness)
        worker = function(JOBS, 'void completionThread(')
        self.assertLess(worker.index('complete_job_hosts(j)'), worker.index('list_del(&j->file_link)'))
        submit = function(JOBS, 'extern "C" int native_powervr_submit_jobs(')
        self.assertLess(submit.index('native_powervr_hosts_sync'), submit.index('file->nextFence++'))
        self.assertLess(submit.index('native_powervr_hosts_sync'), submit.index('run_job(j,'))

    def test_late_reset_marks_idle_context(self):
        harness = r'''
#include <cassert>
#include <cstdint>
#include <vector>
struct pvr_context {uint32_t ctx_id;int faulty=0;};
struct xarray {bool held=false;pvr_context *contexts[8]{};};
void xa_lock(xarray *x){assert(!x->held);x->held=true;}
void xa_unlock(xarray *x){assert(x->held);x->held=false;}
void *xa_load(xarray *x,uint32_t id){assert(x->held);return id<8?x->contexts[id]:nullptr;}
void atomic_set(int *p,int v){*p=v;}
struct Job {pvr_context *ctx;uint32_t id;bool failed=false;};
struct list_head {std::vector<Job *> items;};
struct {struct {xarray ctx_ids;} dev;list_head jobs;} gJobs;
#define list_for_each_entry(j,h,m) for(auto *cursor:(h)->items) if((j=cursor))
struct ResetNote {uint32_t ctx_id,job_ref,reason,flags;};
constexpr uint32_t ROGUE_CONTEXT_RESET_REASON_GUILTY_LOCKUP=1,
 ROGUE_CONTEXT_RESET_REASON_GUILTY_OVERRUNING=2,
 ROGUE_FWIF_FWCCB_CMD_CONTEXT_RESET_FLAG_PF=1,
 ROGUE_FWIF_FWCCB_CMD_CONTEXT_RESET_FLAG_ALL_CTXS=2;
''' + function(JOBS, 'void applyResets(') + r'''
int main(){
 pvr_context idle{1},other{2};gJobs.dev.ctx_ids.contexts[1]=&idle;gJobs.dev.ctx_ids.contexts[2]=&other;
 ResetNote late{1,99,ROGUE_CONTEXT_RESET_REASON_GUILTY_LOCKUP,0};
 applyResets(&late,1);assert(idle.faulty&&!other.faulty&&!gJobs.dev.ctx_ids.held);
 idle.faulty=0;ResetNote innocent{1,99,3,0};applyResets(&innocent,1);assert(!idle.faulty);
 ResetNote pageFault{1,99,3,ROGUE_FWIF_FWCCB_CMD_CONTEXT_RESET_FLAG_PF};
 applyResets(&pageFault,1);assert(idle.faulty);idle.faulty=0;
 Job failed{&idle,99},following{&idle,100},unrelated{&other,99};
 gJobs.jobs.items={&failed,&following,&unrelated};applyResets(&late,1);
 assert(idle.faulty&&failed.failed&&!following.failed&&!unrelated.failed&&!other.faulty);
 ResetNote missing{7,99,1,0};applyResets(&missing,1);assert(!other.faulty&&!gJobs.dev.ctx_ids.held);
}
'''
        run_cpp(harness)

    def test_readonly_import_refused(self):
        source = function(GPU, 'int boImportHost(')
        # Execute the actual prepare/error path; the rest of import is compiled against descriptor mocks.
        harness = r'''
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
using IOReturn=int;using IOByteCount=uint64_t;
constexpr int kIOReturnSuccess=0,kIODirectionInOut=3,kIOMemoryMapperNone=0;
constexpr uint64_t kDevicePageSize=4096,kDevicePageMask=~4095ULL;
int prepared=0,released=0;int prepareStatus=2;
void IOLog(const char *,...){}
struct IOMemoryDescriptor;
struct IOMemoryMap {IOMemoryDescriptor *getMemoryDescriptor(){return nullptr;}
 uint64_t getPhysicalSegment(uint64_t,IOByteCount *,int){assert(false);return 0;}};
int scanoutHostRange(void *,uint64_t,uint64_t,IOMemoryMap **out){*out=nullptr;return -ENOENT;}
constexpr uint64_t DRM_PVR_BO_BYPASS_DEVICE_CACHE=1;
struct IOMemoryDescriptor {void retain(){}int prepare(){++prepared;return prepareStatus;}void release(){++released;}
 uint64_t getLength(){return 4096;}uint64_t getPhysicalSegment(uint64_t,IOByteCount *n,int){*n=4096;return 0x100000;}} md;
struct native_powervr_sg {uint64_t phys,size;};
struct native_powervr_bo {uint32_t refs;uint64_t size,flags;IOMemoryDescriptor *host;bool hostPrepared;
 uint32_t sgCapacity,nents;native_powervr_sg *sg;IOMemoryMap *hostMap;};
void *kzallocBytes(size_t n){return calloc(1,n);}
IOMemoryDescriptor *hostRange(void *,uint64_t,uint64_t,int direction){assert(direction==kIODirectionInOut);return &md;}
void boDestroy(native_powervr_bo *b){if(b->host)b->host->release();free(b->sg);free(b);}
using task_t=void *;
''' + source + r'''
int main(){native_powervr_bo *out=nullptr;
 assert(boImportHost(nullptr,4096,4096,0,&out)==-EFAULT&&!out&&prepared==1&&released==1);
 prepareStatus=0;assert(boImportHost(nullptr,4096,4096,0,&out)==0&&out->hostPrepared&&out->nents==1);
 boDestroy(out);assert(prepared==2&&released==2);
}
'''
        run_cpp(harness)

    def test_owned_handoff_close_and_reconnect(self):
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <functional>
using IOReturn=int;using task_t=void *;
enum {kIOReturnSuccess=0,kIOReturnUnsupported=1,kIOReturnNotReady=2,kIOReturnNotOpen=3,kIOReturnTimeout=4,
 kIOReturnNoMemory=5,kIOReturnBadArgument=6,kIOReturnAborted=7,kIOReturnExclusiveAccess=8,
 THREAD_ABORTSAFE=0,THREAD_AWAKENED=1,kMillisecondScale=1,kIOMapAnywhere=1,NGP_BGRX8888=1,
 NGP_METHOD_MODE=0,NGP_METHOD_WAIT_SWAP=2,NGP_METHOD_COMPLETE_SWAP=3};
struct NGPMode{uint32_t version,format;uint64_t width,height,stride,bytes;};
struct NGPWait{uint64_t timeout_ms;};
struct NGPSwap{uint64_t sequence,address,bytes,stride;uint32_t width,height,format,reserved;};
struct NGPComplete{uint64_t sequence;uint32_t status,reserved;};
struct IOService{};
struct IOLock{bool held=false;};
void IOLockLock(IOLock *p){assert(!p->held);p->held=true;}
void IOLockUnlock(IOLock *p){assert(p->held);p->held=false;}
void IOLockWakeup(IOLock *p,void *,bool){assert(p->held);}
std::function<void()> sleeping,mappingHook;
int IOLockSleepDeadline(IOLock *p,void *,uint64_t,int){IOLockUnlock(p);auto f=sleeping;sleeping=nullptr;if(f)f();IOLockLock(p);return f?THREAD_AWAKENED:0;}
void clock_interval_to_deadline(uint32_t n,int,uint64_t *p){*p=n;}
uint64_t mach_absolute_time(){return 0;}
void absolutetime_to_nanoseconds(uint64_t n,uint64_t *p){*p=n;}
uint64_t native_gop_wait_stats[7]={},native_gop_gpu_failures=0,native_gop_gpu_completions=0,native_gop_gpu_abandoned=0;
unsigned completions=0;int lastCompletion=-1;
void native_legacy_gpu_complete(void *,int s){++completions;lastCompletion=s;}
struct IOMemoryDescriptor;
int liveMaps=0;
struct IOMemoryMap{IOMemoryDescriptor *md;uint64_t getVirtualAddress(){return 4096;}uint64_t getLength(){return 4096;}void release();};
struct IOMemoryDescriptor{int refs=1;void retain(){++refs;}void release(){assert(refs>0);--refs;}
 IOMemoryMap *createMappingInTask(task_t,int,int){if(mappingHook){auto f=mappingHook;mappingHook=nullptr;f();}retain();++liveMaps;return new IOMemoryMap{this};}};
void IOMemoryMap::release(){md->release();--liveMaps;delete this;}
#define OSSafeReleaseNULL(p) do{if(p){p->release();p=nullptr;}}while(0)
class NativeGOPPresenter:public IOService{public:
 IOLock storage;IOLock *gate=&storage;IOService *owner=nullptr,*handoffOwner=nullptr;
 NGPMode mode={};bool online=true,gpuMode=true,gpuClient=false;
 struct {bool offered,delivered;uint64_t sequence;void *display;IOMemoryDescriptor *memory;IOMemoryMap *map;
 uint64_t stride;uint32_t width,height;}swap={};
 bool open(IOService *p){if(owner)return false;owner=p;return true;}
 void close(IOService *p){assert(owner==p);owner=nullptr;}
 IOReturn waitSwap(task_t,uint64_t,NGPSwap *,IOService *);
 IOReturn completeSwap(uint64_t,int,IOService *);
 bool abandonSwap(IOService *p=nullptr);
 static IOReturn handoffAcquire(IOService *);
 static void handoffClose(IOService *);
 static IOReturn handoffCall(uint32_t,task_t,const void *,size_t,void *,size_t,IOService *);
};
NativeGOPPresenter *gHandoff;
''' + '\n'.join(function(GOP, signature) for signature in [
            'IOReturn NativeGOPPresenter::waitSwap(', 'IOReturn NativeGOPPresenter::completeSwap(',
            'bool NativeGOPPresenter::abandonSwap(', 'IOReturn NativeGOPPresenter::handoffCall(',
            'IOReturn NativeGOPPresenter::handoffAcquire(', 'void NativeGOPPresenter::handoffClose(']) + r'''
void offer(NativeGOPPresenter &p,IOMemoryDescriptor &m,uint64_t seq){
 assert(!p.swap.offered);m.retain();p.swap={true,false,seq,&p,&m,nullptr,3200,800,1280};}
int main(){NativeGOPPresenter p;gHandoff=&p;IOService a,b;IOMemoryDescriptor first,second;NGPSwap out{};
 assert(p.handoffAcquire(&a)==0&&p.handoffAcquire(&b)==kIOReturnExclusiveAccess);
 sleeping=[&]{offer(p,first,1);};assert(p.waitSwap(nullptr,100,&out,&a)==0&&out.sequence==1&&liveMaps==1);
 p.handoffClose(&b);assert(p.swap.offered&&liveMaps==1&&completions==0);
 assert(p.completeSwap(1,0,&b)==kIOReturnNotOpen);
 p.handoffClose(&a);assert(!p.swap.offered&&!p.gpuClient&&!p.owner&&liveMaps==0&&first.refs==1);
 assert(completions==1&&lastCompletion==kIOReturnAborted);p.handoffClose(&a);assert(completions==1);
 assert(p.handoffAcquire(&b)==0);sleeping=[&]{offer(p,second,2);};assert(p.waitSwap(nullptr,100,&out,&b)==0);
 assert(p.completeSwap(2,0,&b)==0&&completions==2&&second.refs==1&&liveMaps==0);p.handoffClose(&b);
 // Close while WAIT_SWAP is asleep; a new owner's frame must remain untouched by the old waiter.
 assert(p.handoffAcquire(&a)==0);
 sleeping=[&]{p.handoffClose(&a);assert(p.handoffAcquire(&b)==0);offer(p,second,3);};
 assert(p.waitSwap(nullptr,100,&out,&a)==kIOReturnNotReady&&p.swap.sequence==3&&!p.swap.delivered);
 assert(p.waitSwap(nullptr,100,&out,&b)==0);p.handoffClose(&b);
 // Close during mapping (gate released); cleanup must not install an old map into a new session.
 assert(p.handoffAcquire(&a)==0);offer(p,first,4);
 mappingHook=[&]{p.handoffClose(&a);assert(p.handoffAcquire(&b)==0);offer(p,second,5);};
 assert(p.waitSwap(nullptr,100,&out,&a)==kIOReturnNoMemory&&p.swap.sequence==5&&!p.swap.map&&first.refs==1);
 assert(p.waitSwap(nullptr,100,&out,&b)==0);p.handoffClose(&b);
 assert(first.refs==1&&second.refs==1&&liveMaps==0&&!p.owner&&!p.handoffOwner);
 // Existing unowned Adreno/direct calls still work when no owned session is active.
 sleeping=[&]{offer(p,first,6);};assert(p.waitSwap(nullptr,100,&out,nullptr)==0);
 assert(p.completeSwap(6,0,nullptr)==0&&first.refs==1&&liveMaps==0);
 p.abandonSwap();assert(!p.gpuClient);
}
'''
        run_cpp(harness)


if __name__ == '__main__':
    unittest.main()
