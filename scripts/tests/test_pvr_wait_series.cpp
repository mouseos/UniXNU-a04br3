#include "../../package/powervr/NativePowerVRWaitSeries.h"
#include <cassert>
#include <thread>
#include <vector>
#include <cstdio>
static NativeWaitSeries make() {NativeWaitSeries s={};s.threshold=1000;s.windowTicks=10000;s.staleTicks=5000;return s;}
int main() {
 NativeWaitSeries s=make();NativeWaitResult out={};
 assert(!native_wait_begin(&s,56,7,3,100));
 __atomic_store_n(&s.enabled,1,__ATOMIC_RELEASE);
 assert(native_wait_begin(&s,56,7,3,100));
 for(unsigned i=0;i<10;i++) {
  uint64_t start=110+i*110,end=start+100;
  if(i) assert(!native_wait_begin(&s,56,7,3,start-1));
  bool emit=native_wait_end(&s,56,7,3,start,end,-60,false,&out);
  assert(emit==(i==9));
 }
 assert(out.pid==56 && out.tid==7 && out.fence==3 && out.first==110 && out.last==1200);
 assert(out.sum==1000 && out.count==10 && out.errors==10 && out.result==-60);
 assert(!native_wait_begin(&s,56,7,3,1210));
 assert(native_wait_end(&s,56,7,3,1211,1250,0,true,&out) && out.result==0 && out.count==1);
 assert(native_wait_begin(&s,56,7,4,1260)); // new fence is never merged
 assert(!native_wait_end(&s,56,8,4,1261,1300,-60,false,&out) && s.late==1);
 NativeWaitSeries observed=make();__atomic_store_n(&observed.enabled,1,__ATOMIC_RELEASE);
 unsigned emitted=0;assert(native_wait_begin(&observed,56,1164,169,100));
 for(unsigned i=0;i<40;i++) {
  uint64_t start=101+i*101,end=start+100;
  if(i)native_wait_begin(&observed,56,1164,169,start-1);
  if(native_wait_end(&observed,56,1164,169,start,end,-60,false,&out)) {
   ++emitted;assert(out.sum==1000 && out.count==10 && out.tid==1164 && out.fence==169);
  }
 }
 assert(emitted==4 && observed.suppressedSeries==0);
 NativeWaitSeries v=make();__atomic_store_n(&v.enabled,1,__ATOMIC_RELEASE);
 for(unsigned i=0;i<16;i++) native_wait_begin(&v,56,i+1,1,100+i);
 assert(!native_wait_begin(&v,56,99,1,6000) && v.full==1); // pending stays alive
 assert(!native_wait_end(&v,56,1,1,101,6100,0,true,&out) || out.tid==1);
 native_wait_begin(&v,56,99,1,6200); // a completed slot becomes available
 NativeWaitSeries rate=make();__atomic_store_n(&rate.enabled,1,__ATOMIC_RELEASE);
 for(unsigned i=0;i<5;i++) {
  bool b=native_wait_begin(&rate,56,i+1,100+i,100+i*1000);
  assert(b==(i<4));
  bool got=native_wait_end(&rate,56,i+1,100+i,101+i*1000,1101+i*1000,-60,false,&out);
  assert(got==(i<4));
 }
 assert(rate.suppressedBegin==1 && rate.suppressedSeries==1);
 assert(native_wait_begin(&rate,56,10,1,10101)); // next window
 NativeWaitSeries busy=make();__atomic_store_n(&busy.enabled,1,__ATOMIC_RELEASE);
 busy.lock=1;assert(!native_wait_begin(&busy,1,1,1,1) && busy.busy==1);busy.lock=0;
 NativeWaitSeries parallel=make();__atomic_store_n(&parallel.enabled,1,__ATOMIC_RELEASE);
 std::vector<std::thread> ts;
 for(unsigned i=0;i<32;i++)ts.emplace_back([&,i]{native_wait_begin(&parallel,1,i+1,i+1,100);});
 for(auto &t:ts)t.join();
 unsigned used=0;for(auto &x:parallel.slots)used+=x.pid!=0;
 assert(used<=16 && parallel.beginBudget<=4);
 puts("PASS wait series: default off, 100ms repetition, identities, active/stale slots, rate limit, contention and concurrency");
}
