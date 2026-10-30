#include "../../package/A04BR3RtcWacs.h"
#include <cassert>
#ifdef NDEBUG
#error "assertions are required"
#endif
struct IO {
 bool locked=false, available=true, poison=false; unsigned phase=0, calls=0, commands=0, clears=0, unlocks=0; uint32_t first=1U<<21, response=(1U<<21)|(6U<<16)|42U, final=1U<<21;
 bool lock(){if(!available)return false;locked=true;return true;} void unlock(){assert(locked);locked=false;unlocks++;}
 uint32_t status(){assert(locked);calls++;return phase==0?first:phase==1?response:final;}
 void command(uint32_t c){assert(locked && (c >> 31)==0 && ((c >> 16) & 0x7fffU)*2U==0x800aU && (c & 0xffffU)==0);phase=1;commands++;}
 void clear(){assert(locked);phase=2;clears++;}
};
int main(){
 IO good; auto x=a04br3_rtc_wacs_read(good);assert(x.status==rtcOk && x.second==42 && !good.locked && good.commands==1 && good.clears==1 && good.unlocks==1);
 IO busy;busy.available=false;x=a04br3_rtc_wacs_read(busy);assert(x.status==rtcContended && busy.commands==0);
 IO foreign;foreign.first=(1U<<21)|(6U<<16);x=a04br3_rtc_wacs_read(foreign);assert(x.status==rtcForeign && foreign.commands==0 && foreign.clears==0 && !foreign.locked);
 IO noinit;noinit.first=0;x=a04br3_rtc_wacs_read(noinit);assert(x.status==rtcForeign && noinit.commands==0);
 IO timeout;timeout.response=1U<<21;x=a04br3_rtc_wacs_read(timeout);assert(x.status==rtcTimeout && x.quarantined && timeout.locked && timeout.clears==0 && timeout.calls==2049);
 IO dirty;dirty.final=(1U<<21)|(6U<<16);x=a04br3_rtc_wacs_read(dirty);assert(x.status==rtcDirty && x.quarantined && dirty.locked);
 IO bad;bad.response=(1U<<21)|(6U<<16)|60U;x=a04br3_rtc_wacs_read(bad);assert(x.status==rtcBadValue && !bad.locked);
}
