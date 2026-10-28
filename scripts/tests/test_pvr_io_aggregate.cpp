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
 assert(a.full==1);unsigned n=0;for(unsigned i=0;i<16;i++){unsigned k=native_io_aggregate_take(&a,out);assert(k<=2);n+=k;}assert(n==16);
 a={};std::vector<std::thread> threads;
 for(unsigned t=0;t<4;t++)threads.emplace_back([&,t]{for(unsigned i=0;i<10000;i++){NativeIoRecord q={1,20,t+1,0,9,1,-1};native_io_aggregate_record(&a,q);}});
 for(auto &thread:threads)thread.join();
 uint64_t total=0;for(const auto &row:a.rows){total+=row.count[0];assert(row.ticks[0]==row.count[0]*19 && row.errors[0]==row.count[0]);}
 assert(total+a.busy==40000 && a.full==0);
 puts("PASS aggregate short I/O, errors, bounded snapshots, full slots and concurrent loss accounting");
}
