#!/usr/bin/env python3
"""Compile the actual submit function with deterministic queue/clock stubs."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'package/powervr/NativePowerVRJobs.cpp').read_text()
a=s.index('extern "C" int native_powervr_submit_jobs('); b=s.index('// NativePowerVRMethodWaitFence',a)
function=s[a:b]
pre=r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cerrno>
#include "NativePowerVRSubmitStage.h"
using u32=uint32_t;
struct Queue {bool killed=false;};
struct Record {uint64_t submission;unsigned index;};
struct native_powervr_pte_snapshot{};
using native_powervr_pte_record=Record;
struct Job {bool pte_requested=false;Record *pre_pte=nullptr;native_powervr_pte_snapshot *pte=nullptr;Job *paired=nullptr;Queue *queue=nullptr;void *hosts=nullptr;unsigned type=0;void *hwrt=nullptr;uint64_t fence=0,kicked=0;void *file=nullptr;int dev_link=0,file_link=0;};
struct native_powervr_jobs_file {uint64_t nextFence=1;int jobs;};
struct native_powervr_job_submit {};
using native_powervr_pte_phase=int(*)(void*,native_powervr_pte_snapshot**,native_powervr_pte_record**,uint32_t);
constexpr unsigned NativePowerVRAbiMaxJobs=4,kMaxUfosPerJob=1,kSchedTimeoutMs=2,DRM_PVR_JOB_TYPE_FRAGMENT=1,NativePowerVRTunnelMagic=0xffffffff;
constexpr unsigned PerfSubmit=0,PerfLock=1,PerfSpace=2,PerfBo=3,PerfKick=4;
static NativeSubmitStage gSubmitStage{};static unsigned gSubmitStageEnabled=1;static uint64_t gPteSubmission=0,clockTick=10;static int mode=0,checks=0,releases=0;static bool lockHeld=false;
static Queue queue;static Record record;static native_powervr_pte_snapshot snapshot;static Job job;
struct {int *lock=nullptr;int jobs;} gJobs;
uint64_t nowAbs(){return ++clockTick;} uint64_t perfBegin(unsigned){return 0;} void perfEnd(unsigned,uint64_t){}
void proc_selfname(char *p,int){std::strcpy(p,"backboardd");} int proc_selfpid(){return 57;} void* current_thread(){return nullptr;} uint64_t thread_tid(void*){return 1165;}
int create_job(native_powervr_jobs_file*,const native_powervr_job_submit*,Job **p){if(mode==1)return -EINVAL;job={};queue={};job.queue=&queue;if(mode==2){job.pte_requested=true;job.pre_pte=&record;job.pte=&snapshot;}*p=&job;return 0;}
void pvr_job_release(Job*){assert(!lockHeld);++releases;}
bool can_combine_jobs(Job*,Job*){return false;} unsigned job_cmds_size(Job*,unsigned){return 1;}
void nanoseconds_to_absolutetime(uint64_t,uint64_t *x){*x=mode==4?0:10000;}
void IOLockLock(int*){assert(!lockHeld);clockTick+=mode==3?40:0;lockHeld=true;} void IOLockUnlock(int*){assert(lockHeld);lockHeld=false;} void IOSleep(int){assert(!lockHeld);clockTick+=10;}
bool pvr_cccb_cmdseq_can_fit(Queue*,u32){return mode!=5;}
bool pvr_cccb_cmdseq_fits(Queue*,u32){return mode!=4 && (mode!=3 || ++checks>=2);}
bool reserveTracks(unsigned){return mode!=6;} int native_powervr_hosts_sync(void*){return mode==7?-EINVAL:0;}
void *trackFor(void*){return nullptr;} void run_job(Job*,void*,uint64_t *p){if(p)*p+=2;clockTick+=4;}
void list_add_tail(int*,int*){}
int phase(void*,native_powervr_pte_snapshot**,native_powervr_pte_record**,uint32_t){return -EINVAL;}
'''
# The actual code uses the CCCB member; retain the same object identity in the stub.
pre=pre.replace('struct Queue {bool killed=false;};','struct Queue {bool killed=false; Queue *cccb_dummy=nullptr; struct Cccb {} cccb;}; using pvr_queue=Queue;').replace('bool pvr_cccb_cmdseq_can_fit(Queue*,u32)', 'bool pvr_cccb_cmdseq_can_fit(Queue::Cccb*,u32)').replace('bool pvr_cccb_cmdseq_fits(Queue*,u32)', 'bool pvr_cccb_cmdseq_fits(Queue::Cccb*,u32)')
main=r'''
int main(){
 for(mode=0;mode<=7;mode++) {
  gSubmitStage={};clockTick=10;checks=releases=0;lockHeld=false;native_powervr_jobs_file file;native_powervr_job_submit input;uint64_t fence=0;
  int rc=native_powervr_submit_jobs(&file,&input,1,&fence,mode==2?phase:nullptr,nullptr);
  auto &r=gSubmitStage.rows[0];assert(r.count==1 && r.succeeded+r.failed==1);
  assert((rc==0)==(r.succeeded==1));assert((rc!=0)==(r.failed==1));assert(!lockHeld);
  if(mode==3){assert(checks==2);assert(r.sample.ticks[NativeSubmitRelock]>=40);assert(r.sample.ticks[NativeSubmitSpace]>=10);assert(r.sample.ticks[NativeSubmitSpace]<r.sample.ticks[NativeSubmitRelock]);}
  else assert(r.sample.ticks[NativeSubmitRelock]==0);
  if(mode==0||mode==3) assert(fence==1); else assert(rc!=0);
 }
 gSubmitStage={};native_powervr_jobs_file file;native_powervr_job_submit input;uint64_t fence=0;
 assert(native_powervr_submit_jobs(&file,&input,0,&fence,nullptr,nullptr)==-EINVAL);assert(gSubmitStage.rows[0].count==0);
 gSubmitStageEnabled=0;mode=3;checks=0;lockHeld=false;
 assert(native_powervr_submit_jobs(&file,&input,1,&fence,nullptr,nullptr)==0);assert(checks==2 && !gSubmitStage.rows[0].count && !lockHeld);
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp); (p/'test.cpp').write_text(pre+function+main)
 subprocess.run(['g++','-std=c++17','-O1','-Wall','-Wextra','-Werror','-fsanitize=undefined','-I'+str(root/'package/powervr'),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print('PASS actual submit success, create/PTE/queue/space/reserve/BO failure and relock attribution (UBSan)')
