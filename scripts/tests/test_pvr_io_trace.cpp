#include "../../package/powervr/NativePowerVRIoTrace.h"
#include <cassert>
#include <thread>
#include <vector>
#include <cstdio>
int main() {
 NativeIoTrace t={}; NativeIoRecord r={1,2,3,4,5,1,0}, out={};
 t.immediate_threshold_ticks=1000;
 NativeIoRecord longRecord={10,1010,3,4,5,1,0};
 assert(!native_io_immediate_claim(&t,longRecord));
 __atomic_store_n(&t.enabled,1,__ATOMIC_RELEASE);
 assert(!native_io_immediate_claim(&t,longRecord));
 __atomic_store_n(&t.immediate_enabled,1,__ATOMIC_RELEASE);
 NativeIoRecord below={10,1009,3,4,5,1,0};
 assert(!native_io_immediate_claim(&t,below));
 std::vector<std::thread> claimers;
 for(int i=0;i<32;i++) claimers.emplace_back([&]{native_io_immediate_claim(&t,longRecord);});
 for(auto &thread:claimers)thread.join();
 assert(t.immediate_count==8 && t.immediate_skipped==24);
 t={};
 native_io_record(&t,r); assert(t.cursor==0 && !native_io_take(&t,0,&out));
 t.threshold_ticks=2000;
 __atomic_store_n(&t.enabled,1,__ATOMIC_RELEASE);
 NativeIoRecord fast={1,2000,3,4,5,1,0};
 for(int i=0;i<10000;i++) native_io_record(&t,fast);
 assert(t.cursor==0 && t.short_count[0]==10000 && t.short_ticks[0]==19990000);
 NativeIoRecord boundary={1,2001,3,4,5,1,0};
 native_io_record(&t,boundary); unsigned found=0;
 for(unsigned i=0;i<128;i++) found+=native_io_take(&t,i,&out);
 assert(found==1 && out.end==2001);
 fast.result=-1;native_io_record(&t,fast);found=0;
 for(unsigned i=0;i<128;i++) found+=native_io_take(&t,i,&out);
 assert(found==1 && out.result==-1);
 r.end=2001;
 std::vector<std::thread> threads;
 for (int i=0;i<4;i++) threads.emplace_back([&]{for(int j=0;j<100;j++) native_io_record(&t,r);});
 for (auto &thread:threads) thread.join();
 unsigned count=0;for(unsigned i=0;i<128;i++) if(native_io_take(&t,i,&out)) {assert(out.start==1&&out.end==2001&&out.tid==3&&out.fence==4&&out.pid==5&&out.kind==1);count++;}
 assert(count==128 && t.dropped==272);
 native_io_record(&t,r); count=0;for(unsigned i=0;i<128;i++) count+=native_io_take(&t,i,&out);assert(count==1);
 t={};t.enabled=1;t.threshold_ticks=10;
 for(unsigned i=0;i<100;i++) { NativeIoRecord x={1,20,3,i,5,1,-1}; native_io_record(&t,x); }
 unsigned seen[100]={};
 std::thread fastReporter([&]{unsigned cursor=0;NativeIoRecord x;while(native_io_live_take(&t,&cursor,&x)) __atomic_fetch_add(&seen[x.fence],1u,__ATOMIC_RELAXED);});
 std::thread slowReporter([&]{NativeIoRecord x;for(unsigned i=0;i<128;i++) if(native_io_take(&t,i,&x)) __atomic_fetch_add(&seen[x.fence],1u,__ATOMIC_RELAXED);});
 fastReporter.join();slowReporter.join();
 for(unsigned n:seen)assert(n==1);
 unsigned cursor=0;assert(!native_io_live_take(&t,&cursor,&out));
 t={};t.enabled=1;t.threshold_ticks=10;
 unsigned concurrentSeen[400]={},writersDone=0;
 std::vector<std::thread> writers;
 for(unsigned k=0;k<4;k++) writers.emplace_back([&,k]{
  for(unsigned i=0;i<100;i++) {NativeIoRecord x={1,20,3,k*100+i,5,1,-1};native_io_record(&t,x);}
  __atomic_fetch_add(&writersDone,1u,__ATOMIC_RELEASE);
 });
 auto keep=[&](const NativeIoRecord &x){assert(x.fence<400);__atomic_fetch_add(&concurrentSeen[x.fence],1u,__ATOMIC_RELAXED);};
 std::thread fastConcurrent([&]{unsigned c=0;NativeIoRecord x;while(__atomic_load_n(&writersDone,__ATOMIC_ACQUIRE)!=4) if(native_io_live_take(&t,&c,&x))keep(x);while(native_io_live_take(&t,&c,&x))keep(x);});
 std::thread slowConcurrent([&]{NativeIoRecord x;while(__atomic_load_n(&writersDone,__ATOMIC_ACQUIRE)!=4) for(unsigned i=0;i<128;i++)if(native_io_take(&t,i,&x))keep(x);for(unsigned i=0;i<128;i++)if(native_io_take(&t,i,&x))keep(x);});
 for(auto &thread:writers)thread.join();fastConcurrent.join();slowConcurrent.join();
 unsigned total=0;for(unsigned n:concurrentSeen){assert(n<=1);total+=n;}
 assert(total+t.dropped==400);
 puts("PASS disabled, short flood, boundary, errors, concurrency, bounded overflow and reuse");
}
