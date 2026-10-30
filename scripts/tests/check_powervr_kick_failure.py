#!/usr/bin/env python3
"""Exercise actual KICK batch/error/quarantine code with controlled firmware."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
jobs = (root / 'package/powervr/NativePowerVRJobs.cpp').read_text()
gpu = (root / 'package/powervr/NativePowerVRGpu.cpp').read_text()

def fn(src, sig):
    a = src.index(sig)
    b = src.index('{', a) + 1
    depth = 1
    while depth:
        depth += (src[b] == '{') - (src[b] == '}')
        b += 1
    return src[a:b] + '\n'

h = r'''
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <vector>
#include <algorithm>
constexpr uint32_t kKccbTimeoutMs=3, NativePowerVRAbiMaxJobs=8, DRM_PVR_JOB_TYPE_GEOMETRY=0,DRM_PVR_JOB_TYPE_FRAGMENT=1;
struct Lock{bool held=false;}; Lock lock;
void IOLockLock(Lock *l){assert(!l->held);l->held=true;}
void IOLockUnlock(Lock *l){assert(l->held);l->held=false;}
struct Vm {Lock inner;Lock *lock=&inner;bool quarantined=false;int refs=1;}; using native_powervr_vm_context=Vm;
void native_powervr_vm_context_get(Vm *v){assert(v->lock->held);++v->refs;}
'''
h += fn(gpu, 'extern "C" void native_powervr_vm_quarantine(')
h += fn(gpu, 'extern "C" bool native_powervr_vm_quarantined(')
h += r'''
struct pvr_device{};struct rogue_fwif_kccb_cmd {unsigned cmd_type;};using u32=uint32_t;
struct Context{Vm *vm_ctx;int faulty=0;};struct Queue{bool killed=false;int cccb;};
struct native_powervr_jobs_file;
struct Job{Context *ctx;Queue *queue;int type=0;Job *paired=nullptr;void *hwrt=nullptr;uint64_t fence=0;bool failed=false,quarantined=false;uint64_t kicked=0;native_powervr_jobs_file *file=nullptr;int dev_link=0,file_link=0;};
struct native_powervr_jobs_file{std::vector<uint64_t> failed;int jobs=0;};
struct {Lock *lock=&::lock;pvr_device dev;int kickError=0,jobs=0;}gJobs;
void list_add_tail(int *,int *list){assert(lock.held);++*list;}
int released=0;void pvr_job_release(Job *){assert(!lock.held);++released;}
uint64_t nowAbs(){return 42;}
constexpr int PerfKick=2;
void atomic_set(int *p,int n){*p=n;} Vm *nativeVm(Vm *p){return p;}
void recordFailed(native_powervr_jobs_file *f,uint64_t n){assert(lock.held);f->failed.push_back(n);}
std::vector<int> results;unsigned calls=0,sleeps=0;
uint64_t perfBegin(int){return 0;} void perfEnd(int,uint64_t){} constexpr int PerfKccb=1;
int native_powervr_kccb_submit(rogue_fwif_kccb_cmd *,unsigned){assert(lock.held);assert(calls<results.size());return results[calls++];}
void IOSleep(unsigned){assert(lock.held);++sleeps;} void native_powervr_log(const char*,...){}
'''
h += fn(jobs, 'extern "C" void pvr_kccb_send_cmd_reserved_powered(')
h += r'''
void *trackFor(void *p){return p;}
void run_job(Job *j,void *){assert(lock.held);rogue_fwif_kccb_cmd c{unsigned(j->type)};pvr_kccb_send_cmd_reserved_powered(&gJobs.dev,&c,nullptr);}
'''
h += fn(jobs, 'int kickJobs(')
h += fn(jobs, 'void quarantineKickBatch(')
submit = fn(jobs, 'extern \"C\" int native_powervr_submit_jobs(')
start = submit.index('  bool touched[NativePowerVRAbiMaxJobs] = {};')
end = submit.index('  return err ? -EIO : 0;', start) + len('  return err ? -EIO : 0;')
h += 'int finishBatch(native_powervr_jobs_file *file, Job **js, uint32_t count, uint64_t *fences) { int err=0;\n' + submit[start:end] + '\n}\n'
h += r'''
void scenario(const std::vector<int>&r,bool paired,int expected){
 Vm a,b,c;Context ca{&a},cb{&b},cc{&c};Queue qa,qb,qc;
 Job j0{&ca,&qa,0},j1{&cb,&qb,1},j2{&cc,&qc,3};
 if(paired){j0.paired=&j1;j1.paired=&j0;}
 j0.fence=1;j1.fence=2;j2.fence=3;Job *js[]={&j0,&j1,&j2};bool touched[3]={};
 results=r;calls=sleeps=0;native_powervr_jobs_file file;
 IOLockLock(&lock);int e=kickJobs(js,3,touched);if(e)quarantineKickBatch(&file,js,3,touched);IOLockUnlock(&lock);
 assert(e==expected);
 if(!e){assert(file.failed.empty()&&!a.quarantined&&!b.quarantined&&!c.quarantined);return;}
 assert(file.failed==std::vector<uint64_t>({1,2,3}));
 for(unsigned i=0;i<3;i++) {assert(js[i]->quarantined==touched[i]);assert(js[i]->failed==touched[i]);assert(bool(js[i]->ctx->faulty)==touched[i]);assert(native_powervr_vm_quarantined(js[i]->ctx->vm_ctx)==touched[i]);}
 assert(a.refs==(touched[0]?2:1)&&b.refs==(touched[1]?2:1)&&c.refs==(touched[2]?2:1));
 if(touched[0]){native_powervr_vm_quarantine(&a);assert(a.refs==2);}
}
int main(){
 scenario({0,0,0},false,0);assert(calls==3);
 scenario({-EBUSY,0,0,0},false,0);assert(calls==4&&sleeps==1);
 scenario({-EBUSY,-EBUSY,-EBUSY,-EBUSY},false,-EBUSY);assert(calls==4);
 scenario({-ETIMEDOUT},false,-ETIMEDOUT);assert(calls==1);
 scenario({0,-ETIMEDOUT},false,-ETIMEDOUT);assert(calls==2);
 scenario({-ETIMEDOUT},true,-ETIMEDOUT);assert(calls==1);
 scenario({0,-ETIMEDOUT},true,-ETIMEDOUT);assert(calls==2);
 // Actual submit tail: a partial failure publishes no fence and releases only untouched work.
 Vm a,b,c;Context ca{&a},cb{&b},cc{&c};Queue qa,qb,qc;
 Job j0{&ca,&qa,0},j1{&cb,&qb,1},j2{&cc,&qc,3};j0.fence=1;j1.fence=2;j2.fence=3;
 Job *js[]={&j0,&j1,&j2};uint64_t fences[]={77,77,77};native_powervr_jobs_file file;
 results={0,-ETIMEDOUT};calls=sleeps=released=0;gJobs.jobs=0;IOLockLock(&lock);
 assert(finishBatch(&file,js,3,fences)==-EIO);assert(!lock.held&&calls==2&&released==1);
 assert(fences[0]==77&&fences[1]==77&&fences[2]==77&&gJobs.jobs==2&&file.jobs==2);
 assert(j0.quarantined&&j1.quarantined&&!j2.quarantined&&a.quarantined&&b.quarantined&&!c.quarantined);
}
'''
submit = fn(jobs, 'extern "C" int native_powervr_submit_jobs(')
assert submit.index('err = kickJobs(') < submit.index('quarantineKickBatch(') < submit.index('if (!err) fences[i]')
assert 'if (!touched[i]) pvr_job_release(js[i])' in submit
assert 'return err ? -EIO : 0;' in submit
assert 'native_powervr_vm_quarantined(nativeVm(js[i]->ctx->vm_ctx))' in submit
assert 'if (j->quarantined) continue;' in fn(jobs, 'void completionThread(')
assert '__atomic_load_n(&ctx->quarantined' in fn(gpu, 'int vmMapLocked(')
assert '__atomic_load_n(&vm_ctx->quarantined' in fn(gpu, 'int NativePowerVRUserClient::vmUnmap(')
user_submit = fn(gpu, 'int NativePowerVRUserClient::submitJobs(')
assert user_submit.index('if (err) return err;') < user_submit.index('reply->fence[i]')
with tempfile.TemporaryDirectory(prefix='pvr-kick-') as td:
    p = Path(td)
    (p/'t.cpp').write_text(h)
    subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(p/'t.cpp'),'-o',str(p/'t')],check=True)
    subprocess.run([str(p/'t')],check=True)
print('PASS actual KICK retry, partial/paired failure, quarantine and VM freeze; ASan/UBSan')
