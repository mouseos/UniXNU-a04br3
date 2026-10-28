#include "../../package/powervr/NativePowerVRWaitSeries.h"
#include <cassert>
#include <thread>
#include <vector>
#include <cstdio>
static NativeWaitSeries make() {NativeWaitSeries s={};s.threshold=1000;s.windowTicks=10000;s.staleTicks=5000;s.pendingLeaseTicks=65000;return s;}
static bool test_begin(NativeWaitSeries *s,uint32_t p,uint64_t t,uint64_t f,uint64_t now) { uint64_t serial=0; return native_wait_begin(s,p,t,f,1,now,&serial); }
static bool test_end(NativeWaitSeries *s,uint32_t p,uint64_t t,uint64_t f,uint64_t start,uint64_t end,int result,bool terminal,NativeWaitResult *out) { return native_wait_end(s,p,t,f,1,start,end,result,terminal,out); }
int main() {
 NativeWaitSeries s=make();NativeWaitResult out={};
 assert(!test_begin(&s,56,7,3,100));
 __atomic_store_n(&s.enabled,1,__ATOMIC_RELEASE);
 assert(test_begin(&s,56,7,3,100));
 for(unsigned i=0;i<10;i++) {
  uint64_t start=110+i*110,end=start+100;
  if(i) assert(!test_begin(&s,56,7,3,start-1));
  bool emit=test_end(&s,56,7,3,start,end,-60,false,&out);
  assert(emit==(i==9));
 }
 assert(out.pid==56 && out.tid==7 && out.fence==3 && out.first==110 && out.last==1200);
 assert(out.sum==1000 && out.count==10 && out.errors==10 && out.result==-60);
 assert(!test_begin(&s,56,7,3,1210));
 assert(test_end(&s,56,7,3,1211,1250,0,true,&out) && out.result==0 && out.count==1);
 assert(test_begin(&s,56,7,4,1260)); // new fence is never merged
 assert(!test_end(&s,56,8,4,1261,1300,-60,false,&out) && s.late==1);
 NativeWaitSeries observed=make();__atomic_store_n(&observed.enabled,1,__ATOMIC_RELEASE);
 unsigned emitted=0;assert(test_begin(&observed,56,1164,169,100));
 for(unsigned i=0;i<40;i++) {
  uint64_t start=101+i*101,end=start+100;
  if(i)test_begin(&observed,56,1164,169,start-1);
  if(test_end(&observed,56,1164,169,start,end,-60,false,&out)) {
   ++emitted;assert(out.sum==1000 && out.count==10 && out.tid==1164 && out.fence==169);
  }
 }
 assert(emitted==4 && observed.suppressedSeries==0);
 // Mode3 reproduces the observed coupling: four tiny terminal completions
 // consume BOTH otherwise separate budgets. A later long series is suppressed.
 NativeWaitSeries prior=make();prior.mode=3;__atomic_store_n(&prior.enabled,1,__ATOMIC_RELEASE);
 for(unsigned i=0;i<4;i++) {
  assert(test_begin(&prior,56,2,50+i,100+i*10));
  assert(test_end(&prior,56,2,50+i,101+i*10,105+i*10,0,true,&out));
 }
 assert(prior.beginBudget==4 && prior.seriesBudget==4);
 assert(!test_begin(&prior,56,2,70,200) && prior.suppressedBegin==1);
 assert(prior.suppressedSeries==0); // no threshold crossed yet: not evidence of no wait
 assert(!test_end(&prior,56,2,70,201,1201,-60,false,&out) && prior.suppressedSeries==1);
 // Mode4: fast successful waits do not consume series/active budgets, and
 // a suppressed begin still permits an independently budgeted slow series.
 NativeWaitSeries progress=make();progress.mode=4;progress.threshold=250;progress.windowTicks=1000;progress.activeThreshold=500;
 __atomic_store_n(&progress.enabled,1,__ATOMIC_RELEASE);
 for(unsigned i=0;i<4;i++) {
  assert(!test_begin(&progress,56,1,10+i,100+i*10));
  assert(!test_end(&progress,56,1,10+i,101+i*10,105+i*10,0,true,&out));
 }
 assert(progress.beginBudget==0 && progress.seriesBudget==0 && progress.suppressedBegin==0);
 assert(!test_begin(&progress,56,1,20,200));
 for(unsigned i=0;i<5;i++) {
  uint64_t start=201+i*101,end=start+100;
  if(i)assert(!test_begin(&progress,56,1,20,start-1));
  bool got=test_end(&progress,56,1,20,start,end,-60,false,&out);
  assert(got==(i==2 || i==4));
  if(got)assert(out.tid==1 && out.fence==20 && out.sum==(i+1)*100);
 }
 assert(progress.seriesBudget==2);
 // Snapshot an outstanding wait; when completion arrives after a monitor
 // advanced the window, it still clears the slot using the recorded end.
 assert(!test_begin(&progress,56,1,20,800));
 NativeWaitActive active[2]={};assert(native_wait_active(&progress,1300,active)==1);
 assert(active[0].tid==1 && active[0].fence==20 && active[0].sum==500);
 assert(test_end(&progress,56,1,20,801,1299,0,true,&out) && out.sum==998 && out.result==0);
 bool pending=false;for(auto &slot:progress.slots)if(slot.pid==56 && slot.tid==1 && slot.fence==20)pending=true;
 assert(!pending && progress.late>0);
 // Mode5 retains mode4's short-wait and active-snapshot behavior.
 NativeWaitSeries reasons=make();reasons.mode=5;reasons.threshold=250;reasons.windowTicks=1000;reasons.activeThreshold=500;
 __atomic_store_n(&reasons.enabled,1,__ATOMIC_RELEASE);
 assert(!test_begin(&reasons,56,1,44,100));
 assert(!test_end(&reasons,56,1,44,101,105,0,true,&out));
 assert(reasons.beginBudget==0 && reasons.seriesBudget==0);
 assert(!test_begin(&reasons,56,1,45,200));
 NativeWaitActive mode5Active[2]={};assert(native_wait_active(&reasons,700,mode5Active)==1 && mode5Active[0].fence==45);
 NativeWaitSeries clients=make();__atomic_store_n(&clients.enabled,1,__ATOMIC_RELEASE);
 uint64_t serial1=0,serial2=0;
 assert(native_wait_begin(&clients,56,7,3,101,100,&serial1));
 assert(native_wait_begin(&clients,56,7,3,202,101,&serial2));
 assert(serial1 && serial2 && serial1!=serial2);
 assert(native_wait_end(&clients,56,7,3,101,110,1110,-60,false,&out) && out.serial==serial1 && out.sum==1000);
 assert(native_wait_end(&clients,56,7,3,202,120,1120,-60,false,&out) && out.serial==serial2 && out.sum==1000);
 NativeWaitSeries v=make();__atomic_store_n(&v.enabled,1,__ATOMIC_RELEASE);
 for(unsigned i=0;i<16;i++) test_begin(&v,56,i+1,1,100+i);
 assert(!test_begin(&v,56,99,1,6000) && v.full==1); // pending stays alive
 assert(!test_end(&v,56,1,1,101,6100,0,true,&out) || out.tid==1);
 test_begin(&v,56,99,1,6200); // a completed slot becomes available
 NativeWaitSeries lease=make();__atomic_store_n(&lease.enabled,1,__ATOMIC_RELEASE);
 assert(test_begin(&lease,1,1,1,100));
 lease.lock=1;assert(!test_end(&lease,1,1,1,101,200,-60,false,&out));lease.lock=0;
 assert(lease.busy==1 && lease.slots[0].pending==1);
 test_begin(&lease,1,2,2,65101);
 assert(lease.expiredPending==1);
 NativeWaitSeries rate=make();__atomic_store_n(&rate.enabled,1,__ATOMIC_RELEASE);
 for(unsigned i=0;i<5;i++) {
  bool b=test_begin(&rate,56,i+1,100+i,100+i*1000);
  assert(b==(i<4));
  bool got=test_end(&rate,56,i+1,100+i,101+i*1000,1101+i*1000,-60,false,&out);
  assert(got==(i<4));
 }
 assert(rate.suppressedBegin==1 && rate.suppressedSeries==1);
 assert(test_begin(&rate,56,10,1,10101)); // next window
 NativeWaitSeries busy=make();__atomic_store_n(&busy.enabled,1,__ATOMIC_RELEASE);
 busy.lock=1;assert(!test_begin(&busy,1,1,1,1) && busy.busy==1);busy.lock=0;
 NativeWaitSeries parallel=make();__atomic_store_n(&parallel.enabled,1,__ATOMIC_RELEASE);
 std::vector<std::thread> ts;
 for(unsigned i=0;i<32;i++)ts.emplace_back([&,i]{test_begin(&parallel,1,i+1,i+1,100);});
 for(auto &t:ts)t.join();
 unsigned used=0;for(auto &x:parallel.slots)used+=x.pid!=0;
 assert(used<=16 && parallel.beginBudget<=4);
 puts("PASS wait series: default off, 100ms repetition, identities, active/stale slots, rate limit, contention and concurrency");
}
