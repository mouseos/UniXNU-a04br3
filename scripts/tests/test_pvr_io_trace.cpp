#include "../../package/powervr/NativePowerVRIoTrace.h"
#include <cassert>
#include <thread>
#include <vector>
#include <cstdio>
int main() {
 NativeIoTrace t={}; NativeIoRecord r={1,2,3,4,5,1,0}, out={};
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
 puts("PASS disabled, short flood, boundary, errors, concurrency, bounded overflow and reuse");
}
