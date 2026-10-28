#include "../../package/powervr/NativePowerVRIoTrace.h"
#include <cassert>
#include <thread>
#include <vector>
#include <cstdio>
int main() {
 NativeIoTrace t={}; NativeIoRecord r={1,2,3,4,5,1,0}, out={};
 native_io_record(&t,r); assert(t.cursor==0 && !native_io_take(&t,0,&out));
 __atomic_store_n(&t.enabled,1,__ATOMIC_RELAXED);
 assert(!native_io_keep(&t,9999999,0));
 assert(native_io_keep(&t,10000000,0));
 assert(native_io_keep(&t,1,-1));
 assert(t.filtered==1 && t.filtered_ns==9999999);
 std::vector<std::thread> threads;
 for (int i=0;i<4;i++) threads.emplace_back([&]{for(int j=0;j<100;j++) native_io_record(&t,r);});
 for (auto &thread:threads) thread.join();
 unsigned count=0;for(unsigned i=0;i<128;i++) if(native_io_take(&t,i,&out)) {assert(out.start==1&&out.end==2&&out.tid==3&&out.fence==4&&out.pid==5&&out.kind==1);count++;}
 assert(count==128 && t.dropped==272);
 native_io_record(&t,r); count=0;for(unsigned i=0;i<128;i++) count+=native_io_take(&t,i,&out);assert(count==1);
 puts("PASS disabled, concurrent publication, bounded overflow, drain and reuse");
}
