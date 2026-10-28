#include "../../package/powervr/NativePowerVRIoAggregate.h"
#include <cassert>
#include <thread>
#include <vector>
#include <cstdio>
int main() {
 NativeIoAggregate a={}; NativeIoAggregateRow out[2]={};
 NativeIoRecord r={10,11,4,8,7,1,0};
 native_io_aggregate_record(&a,r);r.result=-60;r.kind=3;r.end=30;native_io_aggregate_record(&a,r);
 assert(native_io_aggregate_take(&a,out)==1 && out[0].pid==7 && out[0].tid==4);
 assert(out[0].count[0]==1 && out[0].ticks[0]==1 && out[0].errors[0]==0);
 assert(out[0].count[2]==1 && out[0].ticks[2]==20 && out[0].errors[2]==1);
 assert(native_io_aggregate_take(&a,out)==0);
 a={};for(unsigned i=0;i<17;i++){NativeIoRecord q={1,20,i+1,0,9,1,0};native_io_aggregate_record(&a,q);}
 assert(a.full==1);a.staleTicks=100;
 NativeIoRecord later={120,130,99,0,9,1,0};native_io_aggregate_record(&a,later);
 assert(a.full==2 && a.retired==0); // 期限を過ぎても未回収枠は保護される。
 unsigned n=0;for(unsigned i=0;i<16;i++){unsigned k=native_io_aggregate_take(&a,out);assert(k<=2);n+=k;}assert(n==16);
 later.start=119;native_io_aggregate_record(&a,later);assert(a.full==3 && a.retired==0);
 later.start=120;native_io_aggregate_record(&a,later);
 assert(a.retired==1 && a.full==3);bool found=false;for(const auto &row:a.rows)if(row.tid==99){found=true;assert(row.serial==17 && row.first==120);}assert(found);
 a.lock=1;native_io_aggregate_record(&a,later);assert(a.busyRecord==1);assert(native_io_aggregate_take(&a,out)==0 && a.busyReport==1);a.lock=0;
 a={};std::vector<std::thread> threads;
 for(unsigned t=0;t<4;t++)threads.emplace_back([&,t]{for(unsigned i=0;i<10000;i++){NativeIoRecord q={1,20,t+1,0,9,1,-1};native_io_aggregate_record(&a,q);}});
 for(auto &thread:threads)thread.join();
 uint64_t total=0;for(const auto &row:a.rows){total+=row.count[0];assert(row.ticks[0]==row.count[0]*19 && row.errors[0]==row.count[0]);}
 assert(total+a.busyRecord==40000 && a.busyReport==0 && a.full==0);
 a={};NativeIoRecord seed={1,20,1,0,9,1,-1};native_io_aggregate_record(&a,seed);
 unsigned ready=0,start=0,done=0,readerFirst=0;uint64_t last[4]={};threads.clear();
 for(unsigned t=0;t<4;t++)threads.emplace_back([&,t]{
  __atomic_fetch_add(&ready,1u,__ATOMIC_RELEASE);while(!__atomic_load_n(&start,__ATOMIC_ACQUIRE))std::this_thread::yield();
  while(!__atomic_load_n(&readerFirst,__ATOMIC_ACQUIRE))std::this_thread::yield();
  for(unsigned i=0;i<10000;i++){NativeIoRecord q={1,20,t+1,0,9,1,-1};native_io_aggregate_record(&a,q);}
  __atomic_fetch_add(&done,1u,__ATOMIC_RELEASE);
 });
 std::thread reader([&]{
  __atomic_fetch_add(&ready,1u,__ATOMIC_RELEASE);while(!__atomic_load_n(&start,__ATOMIC_ACQUIRE))std::this_thread::yield();
  NativeIoAggregateRow first[2]={};assert(native_io_aggregate_take(&a,first)==1 && first[0].tid==1 && first[0].count[0]==1);last[0]=1;__atomic_store_n(&readerFirst,1u,__ATOMIC_RELEASE);
  while(__atomic_load_n(&done,__ATOMIC_ACQUIRE)!=4){NativeIoAggregateRow snap[2]={};unsigned m=native_io_aggregate_take(&a,snap);assert(m<=2);for(unsigned i=0;i<m;i++){assert(snap[i].tid>=1 && snap[i].tid<=4);unsigned k=snap[i].tid-1;assert(snap[i].count[0]>=last[k]);last[k]=snap[i].count[0];assert(snap[i].ticks[0]==snap[i].count[0]*19 && snap[i].errors[0]==snap[i].count[0]);}}
 });
 while(__atomic_load_n(&ready,__ATOMIC_ACQUIRE)!=5)std::this_thread::yield();__atomic_store_n(&start,1u,__ATOMIC_RELEASE);
 for(auto &thread:threads)thread.join();reader.join();
 total=0;for(const auto &row:a.rows)total+=row.count[0];assert(total+a.busyRecord==40001 && a.full==0);
 puts("PASS aggregate short I/O, errors, bounded snapshots, full slots and concurrent loss accounting");
}
