#!/usr/bin/env python3
"""実FWCCB/WaitFence/完了関数をpthreadとASan/UBSanで検証。端末アクセスなし。"""
from pathlib import Path
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / 'package/powervr/NativePowerVRJobs.cpp').read_text()
def fn(sig):
    start = SRC.index(sig); b = SRC.index('{', start); i = b + 1; depth = 1
    while depth:
        depth += (SRC[i] == '{') - (SRC[i] == '}'); i += 1
    return SRC[start:i] + '\n'
h = r'''
#include <atomic>
#include <cassert>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <mutex>
#include <random>
#include <set>
#include <thread>
#include <unordered_map>
using u32=uint32_t; using wait_result_t=int;
struct list_head {list_head *next,*prev;};
void INIT_LIST_HEAD(list_head *h){h->next=h->prev=h;}
void list_add_tail(list_head*n,list_head*h){n->next=h;n->prev=h->prev;h->prev->next=n;h->prev=n;}
void list_del(list_head*n){n->prev->next=n->next;n->next->prev=n->prev;}
bool list_empty(list_head*h){return h->next==h;}
#define entry(n,t,m) ((t*)((char*)(n)-offsetof(t,m)))
#define list_first_entry(h,t,m) entry((h)->next,t,m)
#define list_for_each_entry(p,h,m) for(auto *it=(h)->next;it!=(h)&&((p)=entry(it,__typeof__(*(p)),m),true);it=it->next)
#define list_for_each_entry_safe(p,n,h,m) for(auto *it=(h)->next,*nx=it->next;it!=(h)&&((p)=entry(it,__typeof__(*(p)),m), (n)=nx==(h)?nullptr:entry(nx,__typeof__(*(n)),m),true);it=nx,nx=it->next)
struct IOLock {std::mutex mutex;};
thread_local int jobsHeld=0,fwHeld=0;
IOLock jobsLock,fwLock;
void IOLockLock(IOLock*l){l->mutex.lock();(l==&jobsLock?jobsHeld:fwHeld)++;}
bool IOLockTryLock(IOLock*l){if(!l->mutex.try_lock())return false;(l==&jobsLock?jobsHeld:fwHeld)++;return true;}
void IOLockUnlock(IOLock*l){assert((l==&jobsLock?jobsHeld:fwHeld)==1);(l==&jobsLock?jobsHeld:fwHeld)--;l->mutex.unlock();}
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
void wmb(){}
struct Stop{};thread_local bool stopSleep=false;
std::atomic<unsigned> sleeps{0},callbacks{0};
void IOSleep(unsigned){assert(!jobsHeld&&!fwHeld);++sleeps;if(stopSleep)throw Stop{};std::this_thread::sleep_for(std::chrono::milliseconds(1));}
uint64_t nowAbs(){return std::chrono::steady_clock::now().time_since_epoch().count();}
void nanoseconds_to_absolutetime(uint64_t x,uint64_t*p){*p=x;}
void native_powervr_log(const char*,...){}
constexpr uint32_t kFailedFences=64,kFailureRanges=128,kResetNotes=16,NativePowerVRTunnelMagic=0x50565254;
constexpr uint32_t DRM_PVR_CTX_TYPE_RENDER=0,DRM_PVR_CTX_TYPE_COMPUTE=1,DRM_PVR_CTX_TYPE_TRANSFER_FRAG=2;
constexpr uint32_t ROGUE_CONTEXT_RESET_REASON_GUILTY_LOCKUP=1,ROGUE_CONTEXT_RESET_REASON_GUILTY_OVERRUNING=2;
constexpr uint32_t ROGUE_FWIF_FWCCB_CMD_CONTEXT_RESET_FLAG_PF=1,ROGUE_FWIF_FWCCB_CMD_CONTEXT_RESET_FLAG_ALL_CTXS=2;
constexpr uint32_t ROGUE_CMD_MAGIC_DWORD_MASK=0xffff0000,ROGUE_CMD_MAGIC_DWORD_SHIFTED=0x2abc0000;
constexpr uint32_t ROGUE_FWIF_FWCCB_CMD_REQUEST_GPU_RESTART=0x2abc0001,ROGUE_FWIF_FWCCB_CMD_FREELISTS_RECONSTRUCTION=0x2abc0002,
 ROGUE_FWIF_FWCCB_CMD_FREELIST_GROW=0x2abc0003,ROGUE_FWIF_FWCCB_CMD_UPDATE_STATS=0x2abc0004,
 ROGUE_FWIF_FWCCB_CMD_CONTEXT_RESET_NOTIFICATION=0x2abc0005;
struct ResetNote {uint32_t ctx_id,job_ref,reason,flags,dm;uint64_t fault_address,pc_address;};
struct RetiredFence {uint64_t fence,generation;uint32_t ctx,job,dm;bool eligible;};
struct rogue_fwif_fwccb_cmd_context_reset_data {uint32_t server_common_context_id,reset_job_ref,reset_reason,flags,dm;uint64_t fault_address,pc_address;};
struct rogue_fwif_fwccb_cmd {uint32_t cmd_type;struct {rogue_fwif_fwccb_cmd_context_reset_data cmd_context_reset_notification;int cmd_freelists_reconstruction,cmd_free_list_gs;}cmd_data;};
struct rogue_fwif_ccb_ctl {std::atomic<u32> read_offset{0},write_offset{0};u32 wrap_mask=63;};
struct pvr_queue {uint64_t generation=1;uint32_t dm=3;struct {uint32_t*value;}timeline_ufo;};
struct pvr_context {uint32_t type=2,ctx_id=1;int faulty=0;struct {pvr_queue *geometry=nullptr,*compute=nullptr,*transfer=nullptr;}queues;};
int atomic_read(const int*p){return *p;}void atomic_set(int*p,int v){*p=v;}
struct Xarray{std::unordered_map<u32,pvr_context*>items;};
void xa_lock(Xarray*){}void xa_unlock(Xarray*){}void*xa_load(Xarray*x,u32 id){auto i=x->items.find(id);return i==x->items.end()?nullptr:i->second;}
struct pvr_device{Xarray ctx_ids;};
struct FailedFenceRange {uint64_t first,last;};
std::atomic<bool> failAlloc{false};std::atomic<int> liveAlloc{0};
constexpr unsigned GFP_KERNEL=1;
void *kzalloc(size_t n,unsigned){if(failAlloc)return nullptr;void *p=calloc(1,n);if(p)++liveAlloc;return p;}
void kfree(const void *p){if(p){--liveAlloc;free(const_cast<void *>(p));}}
struct FileData {Xarray ctx_handles,free_list_handles,hwrt_handles,vm_ctx_handles;};
void pvr_destroy_contexts_for_file(FileData*){} void pvr_destroy_free_lists_for_file(FileData*){}
void pvr_destroy_hwrt_datasets_for_file(FileData*){} void xa_destroy(Xarray*){}
constexpr uint32_t kMaxDeadlineMs=2;
struct native_powervr_jobs_file {FileData pf;list_head link,jobs;uint64_t nextFence=2;FailedFenceRange failed[kFailureRanges]{};uint32_t failedCount=0;uint64_t failureUncertainThrough=0;RetiredFence retired[kFailedFences]{};uint32_t retiredNext=0;};
struct Job {list_head dev_link,file_link;native_powervr_jobs_file*file;uint64_t fence=1;pvr_context*ctx;pvr_queue*queue;uint32_t id=12,seqno=1;bool failed=false,timed_out=false;uint64_t kicked=0;int type=3;bool hosts_completed=false;};
struct {pvr_device dev;IOLock*lock=&jobsLock,*fwccbLock=&fwLock;list_head jobs,files;rogue_fwif_ccb_ctl*fwccbCtl;rogue_fwif_fwccb_cmd*fwccb;uint32_t fwccbSlots=64;ResetNote resets[kResetNotes]{},resetReports[kResetNotes]{};uint32_t resetCount=0,resetReportCount=0,fwccbActive=0;uint64_t timeoutAbs=1000000000;}gJobs;
uint32_t get_dm_type(pvr_queue*q){return q->dm;}
bool queueReached(pvr_queue*q,uint32_t seq){assert(jobsHeld);return int32_t(*q->timeline_ufo.value-seq)>=0;}
std::atomic<bool> callbackEntered{false},releaseCallback{false},pauseCallback{false};
void pvr_dump_context_reset_notification(pvr_device*,rogue_fwif_fwccb_cmd_context_reset_data*){
 assert(!jobsHeld&&!fwHeld);++callbacks;if(pauseCallback){callbackEntered=true;while(!releaseCallback)std::this_thread::yield();}}
void pvr_power_reset(pvr_device*,bool){assert(!jobsHeld&&!fwHeld);}
void pvr_free_list_process_reconstruct_req(pvr_device*,int*){assert(!jobsHeld&&!fwHeld);++callbacks;}
void pvr_free_list_process_grow_req(pvr_device*,int*){assert(!jobsHeld&&!fwHeld);++callbacks;}
void applyResets(const ResetNote*,uint32_t);
'''
for sig in ['void recordFailed(', 'bool wasFailed(', 'uint64_t contextGeneration(', 'void rememberRetired(', 'void applyResets(',
            'void process_fwccb_command(', 'extern "C" void pvr_fwccb_process(', 'bool fwccbPendingLocked(',
            'bool tryLockJobsAfterResets(', 'void unlockJobsAfterResets()']:
    # skip forward declarations in the source
    if sig=='void applyResets(': start=SRC.index('void applyResets(',SRC.index('static bool gFirmwareDumpedAfterReset'))
    else: start=SRC.index(sig)
    tail=SRC[start:];brace=tail.index('{');end=brace+1;d=1
    while d:d+=(tail[end]=='{')-(tail[end]=='}');end+=1
    h+=tail[:end]+'\n'
h+=r'''
rogue_fwif_ccb_ctl ctl;rogue_fwif_fwccb_cmd commands[64]{};
void publish(uint32_t job=12,uint32_t ctx=1,uint32_t dm=3,uint32_t flags=1,uint32_t reason=1){
 auto w=ctl.write_offset.load();commands[w]={};commands[w].cmd_type=ROGUE_FWIF_FWCCB_CMD_CONTEXT_RESET_NOTIFICATION;
 commands[w].cmd_data.cmd_context_reset_notification={ctx,job,reason,flags,dm,0,0};ctl.write_offset=(w+1)&63;
}
bool injectDuringSync=false;unsigned syncs=0,released=0;
void complete_job_hosts(Job*j){assert(jobsHeld&&fwHeld);if(j->hosts_completed)return;++syncs;j->hosts_completed=true;if(injectDuringSync){injectDuringSync=false;publish();}}
void pvr_job_release(Job*){assert(!jobsHeld&&!fwHeld);++released;}
bool native_powervr_packet_enabled(){return false;}
void native_powervr_packet_hwr(uint32_t,uint32_t,uint32_t,uint64_t,uint64_t,uint32_t,uint32_t){}
void drainPackets(){}void drainPtes(bool){}void native_powervr_dump_firmware(const char*,int){}
static bool gFirmwareDumpedAfterReset;
'''
h+=fn('void completionThread(')+fn('extern "C" int native_powervr_fence_wait(')+fn('extern "C" void native_powervr_jobs_file_destroy(')
h+=r'''
native_powervr_jobs_file file;Job job;pvr_queue queue;pvr_context ctx;uint32_t ufo=1;
void setup(){assert(!jobsHeld&&!fwHeld);ctl.read_offset=ctl.write_offset=0;gJobs.fwccbCtl=&ctl;gJobs.fwccb=commands;
 gJobs.resetCount=gJobs.resetReportCount=gJobs.fwccbActive=0;INIT_LIST_HEAD(&gJobs.jobs);INIT_LIST_HEAD(&gJobs.files);
 file={};failAlloc=false;file.nextFence=2;INIT_LIST_HEAD(&file.jobs);list_add_tail(&file.link,&gJobs.files);
 queue={};queue.generation=7;queue.dm=3;queue.timeline_ufo.value=&ufo;ufo=1;
 ctx={};ctx.type=2;ctx.ctx_id=1;ctx.queues.transfer=&queue;gJobs.dev.ctx_ids.items.clear();gJobs.dev.ctx_ids.items[1]=&ctx;
 job={};job.file=&file;job.ctx=&ctx;job.queue=&queue;job.id=12;job.seqno=1;job.fence=1;job.kicked=nowAbs();
 list_add_tail(&job.dev_link,&gJobs.jobs);list_add_tail(&job.file_link,&file.jobs);
 pauseCallback=false;callbackEntered=false;releaseCallback=false;syncs=released=0;injectDuringSync=false;}
void worker(){stopSleep=true;try{completionThread(nullptr,0);}catch(Stop&){}stopSleep=false;}
int poll(){return native_powervr_fence_wait(&file,1,0);}
int main(){
 setup();assert(poll()==0&&syncs==1);worker();assert(released==1&&poll()==0);
 // Pending reset cannot be bypassed by a signalled UFO; poll executes no callback.
 setup();publish();auto c=callbacks.load();assert(poll()==-ETIMEDOUT&&callbacks==c);worker();assert(poll()==-EIO);
 // A different consumer removed the reset but pauses before publishing its note.
 setup();publish();pauseCallback=true;std::thread consumer([]{pvr_fwccb_process(&gJobs.dev);});
 while(!callbackEntered)std::this_thread::yield();
 assert(ctl.read_offset==ctl.write_offset&&poll()==-ETIMEDOUT);
 auto start=std::chrono::steady_clock::now();assert(native_powervr_fence_wait(&file,1,2000000)==-ETIMEDOUT);
 assert(std::chrono::steady_clock::now()-start<std::chrono::seconds(1));
 releaseCallback=true;consumer.join();assert(poll()==-EIO);
 // Retirement before a late notification: only exact evidence changes subsequent waits.
 setup();worker();assert(poll()==0);publish();pvr_fwccb_process(&gJobs.dev);assert(poll()==-EIO);
 setup();worker();queue.generation++;publish();pvr_fwccb_process(&gJobs.dev);assert(poll()==0);
 setup();worker();file.retired[0].eligible=false;publish();pvr_fwccb_process(&gJobs.dev);assert(poll()==0);
 setup();worker();publish(99);pvr_fwccb_process(&gJobs.dev);assert(poll()==0);
 setup();worker();publish(12,1,4);pvr_fwccb_process(&gJobs.dev);assert(poll()==0);
 setup();worker();publish(12,1,3,0,0);pvr_fwccb_process(&gJobs.dev);assert(poll()==0);
 setup();worker();publish(12,1,3,3);pvr_fwccb_process(&gJobs.dev);assert(poll()==0);
 // Fixed window eviction and repeated error notifications do not grow/consume history.
 setup();worker();for(unsigned i=0;i<kFailedFences;i++){job.fence=2+i;rememberRetired(&job);}publish();pvr_fwccb_process(&gJobs.dev);bool ready;
 assert(tryLockJobsAfterResets(&ready));assert(!wasFailed(&file,1));unlockJobsAfterResets();
 setup();recordFailed(&file,1);for(unsigned i=0;i<100;i++)recordFailed(&file,1);assert(file.failedCount==1&&liveAlloc==0);
 // Contiguous failures compress; recording in both orders merges the two adjacent intervals.
 setup();worker();file.nextFence=5000;
 for(uint64_t i=1;i<=512;i++) recordFailed(&file,i);
 assert(file.failedCount==1&&file.failed[0].first==1&&file.failed[0].last==512);
 for(uint64_t i=1;i<=512;i++) assert(native_powervr_fence_wait(&file,i,0)==-EIO);
 assert(native_powervr_fence_wait(&file,513,0)==0);
 setup();recordFailed(&file,1);recordFailed(&file,3);recordFailed(&file,2);
 assert(file.failedCount==1&&file.failed[0].first==1&&file.failed[0].last==3);
 // 128 sparse records are exact. Exhaustion fails closed through the new failed ID.
 setup();worker();file.nextFence=1000;
 for(uint64_t i=0;i<128;i++) recordFailed(&file,2*i+1);
 assert(file.failedCount==128&&native_powervr_fence_wait(&file,2,0)==0);
 recordFailed(&file,257);assert(file.failedCount==0&&file.failureUncertainThrough==257);
 for(uint64_t i=1;i<=257;i++) assert(native_powervr_fence_wait(&file,i,0)==-EIO);
 assert(native_powervr_fence_wait(&file,258,0)==0);
 // Out-of-order exhaustion below all existing ranges preserves every higher exact failure.
 setup();worker();file.nextFence=1000;
 for(uint64_t i=0;i<128;i++) recordFailed(&file,300+2*i);
 recordFailed(&file,11);assert(file.failureUncertainThrough==11&&file.failedCount==128);
 for(uint64_t i=0;i<128;i++) assert(native_powervr_fence_wait(&file,300+2*i,0)==-EIO);
 assert(native_powervr_fence_wait(&file,299,0)==0);
 // Exhaustion in the middle discards only ranges covered by the fail-closed watermark.
 setup();worker();file.nextFence=1000;
 for(uint64_t i=0;i<128;i++) recordFailed(&file,4*i+1);
 recordFailed(&file,259);assert(file.failureUncertainThrough==259&&file.failedCount==63);
 for(uint64_t i=0;i<128;i++) assert(native_powervr_fence_wait(&file,4*i+1,0)==-EIO);
 assert(native_powervr_fence_wait(&file,260,0)==0);
 // Deterministic random insertions: every recorded failure remains failed; before exhaustion all answers are exact.
 setup();std::mt19937 rng(42);std::set<uint64_t> expected;
 for(unsigned n=0;n<5000;n++) {uint64_t v=1+rng()%1000;expected.insert(v);recordFailed(&file,v);
   assert(file.failedCount<=kFailureRanges);
   for(uint64_t x:expected) assert(wasFailed(&file,x));
   for(uint32_t j=1;j<file.failedCount;j++) assert(file.failed[j-1].last+1<file.failed[j].first);
   if(!file.failureUncertainThrough) for(uint64_t x=1;x<=1000;x++) assert(wasFailed(&file,x)==(expected.count(x)!=0));
 }
 // UINT64_MAX does not overflow adjacent-interval comparisons.
 setup();recordFailed(&file,UINT64_MAX);recordFailed(&file,UINT64_MAX-2);recordFailed(&file,UINT64_MAX-1);
 assert(file.failedCount==1&&file.failed[0].first==UINT64_MAX-2&&file.failed[0].last==UINT64_MAX);
 // Concurrent WaitFence and publication remain serialized by gJobs.lock.
 setup();worker();file.nextFence=205;
 std::thread writer([]{for(uint64_t i=1;i<200;i+=2){IOLockLock(&jobsLock);recordFailed(&file,i);IOLockUnlock(&jobsLock);}});
 std::thread reader([]{for(unsigned i=0;i<400;i++) (void)native_powervr_fence_wait(&file,1,0);});
 writer.join();reader.join();assert(native_powervr_fence_wait(&file,1,0)==-EIO);
 // A note published by FW during host sync is rechecked at the verdict/retire boundary.
 setup();injectDuringSync=true;assert(poll()==-ETIMEDOUT);worker();assert(poll()==-EIO);
 setup();injectDuringSync=true;worker();assert(released==0&&!list_empty(&file.jobs));worker();assert(poll()==-EIO);
 // Queued context failure is retained even for a different outstanding fence in that context.
 setup();publish(99);worker();assert(poll()==-EIO);
 // A backlog over the callback budget cannot starve timeout scans or drop error notes.
 setup();ufo=0;job.kicked=0;for(unsigned i=0;i<32;i++)publish(100+i,2);
 worker();assert(job.timed_out&&ctl.read_offset==16&&gJobs.resetCount==0);worker();assert(ctl.read_offset==32);
 setup();for(unsigned i=0;i<31;i++)publish(100+i,2);publish();
 worker();assert(!ctx.faulty&&!list_empty(&file.jobs));worker();assert(ctx.faulty&&poll()==-EIO);
 // A consumed but stalled callback does not prevent the worker's timeout scan.
 setup();ufo=0;job.kicked=0;publish();pauseCallback=true;
 std::thread stalled([]{pvr_fwccb_process(&gJobs.dev);});
 while(!callbackEntered){std::this_thread::yield();}worker();assert(job.timed_out);
 releaseCallback=true;stalled.join();worker();assert(poll()==-EIO);
 // Real lock contention: a submission owns jobs while independently consuming FWCCB.
 setup();std::atomic<bool> held{false},go{false};
 std::thread submit([&]{IOLockLock(&jobsLock);held=true;while(!go)std::this_thread::yield();
   IOLockUnlock(&jobsLock);});
 while(!held){std::this_thread::yield();}assert(poll()==-ETIMEDOUT);publish();pvr_fwccb_process(&gJobs.dev);go=true;submit.join();assert(poll()==-EIO);
 // A concurrent worker and waiter arbitrate the same queued guilty notification.
 setup();publish();std::thread completion([]{worker();});
 int result=native_powervr_fence_wait(&file,1,1000000000);completion.join();assert(result==-EIO&&poll()==-EIO);
 assert(liveAlloc==0);
 // Real close function detaches and frees file-owned failure nodes; queued jobs are detached, not freed.
 auto *closing=static_cast<native_powervr_jobs_file*>(kzalloc(sizeof(native_powervr_jobs_file),GFP_KERNEL));
 assert(closing);INIT_LIST_HEAD(&closing->jobs);INIT_LIST_HEAD(&gJobs.files);list_add_tail(&closing->link,&gJobs.files);
 Job pending{};pending.file=closing;list_add_tail(&pending.file_link,&closing->jobs);
 recordFailed(closing,1);recordFailed(closing,2);assert(liveAlloc==1&&closing->failedCount==1);
 native_powervr_jobs_file_destroy(closing);assert(pending.file==nullptr&&liveAlloc==0&&list_empty(&gJobs.files));
 puts("PASS actual FWCCB/WaitFence/close: compressed ranges, saturation fail-closed, concurrent waits, close, reset and timeout; ASan/UBSan");
}
'''
# Guard the real ID-reuse integration and callback lock separation, not merely the model.
create=fn('int create_job(')
assert 'if (r.job == job->id) r.eligible = false' in create
close=fn('extern "C" void native_powervr_jobs_file_destroy(')
assert close.index('list_del(&file->link)') < close.index('kfree(file)')
callback=fn('void process_fwccb_command(')
assert 'IOLockLock(gJobs.lock)' not in callback
wait=fn('extern "C" int native_powervr_fence_wait(')
assert 'pvr_fwccb_process(' not in wait
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp);(p/'test.cpp').write_text(h)
    subprocess.run(['c++','-std=c++17','-pthread','-Wall','-Wextra','-Werror','-Wno-unused-but-set-variable',
        '-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie','-g',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True,timeout=20)
