#include "../../package/powervr/NativePowerVRWaitReason.h"
#include <cassert>
#include <cstdio>
int main() {
 NativeWaitReasonLimit s={};s.minTicks=50;s.perFenceTicks=500;s.windowTicks=1000;
 auto claim=[&](unsigned p,unsigned t,unsigned f,unsigned o,uint64_t start,uint64_t now){return native_wait_reason_claim(&s,p,t,f,o,start,now);};
 // Tiny successes do not spend slots, rate budget, or suppression counters.
 for(unsigned i=0;i<100;i++) assert(!claim(56,1,i+1,7,100,149));
 assert(s.used==0 && s.suppressed==0);
 assert(claim(56,1,169,7,100,200));
 assert(!claim(56,1,169,7,200,300)); // same fence within 500ms
 assert(claim(56,2,170,7,200,300));
 assert(!claim(56,3,171,7,200,301)); // two globally per window
 assert(s.suppressed==2);
 assert(claim(56,3,171,7,1150,1200)); // next window
 assert(claim(56,1,169,8,1150,1201)); // different connection
 assert(!claim(56,4,172,7,1200,1199)); // backwards timestamp
 s.lock=1;assert(!claim(56,4,172,7,1200,1250) && s.busy==1);s.lock=0;
 // Once full, only successful claims evict; ordering and owner stay explicit.
 NativeWaitReasonLimit many={};many.minTicks=1;many.perFenceTicks=1;many.windowTicks=10;
 for(unsigned i=0;i<16;i++) assert(native_wait_reason_claim(&many,56,1,i+1,7,10+i*10,12+i*10));
 assert(native_wait_reason_claim(&many,56,1,100,7,180,182) && many.evicted==1);
 puts("PASS long-fence selection, identity, rate boundaries, contention, eviction");
}
