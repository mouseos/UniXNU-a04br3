#include "../../package/A04BR3RtcReference.h"
#include <cassert>
#ifdef NDEBUG
#error "These tests require assertions"
#endif
struct IO { uint64_t t=0; unsigned sec=0, phase=0; bool ok=true; uint64_t timer(){assert(phase==0 || phase==2); ++phase; return t++;} bool readSecond(unsigned *p){assert(phase==1); ++phase; *p=sec; if(!ok) phase=0; return ok;} };
int main() {
 A04BR3RtcReference s={};
 assert(a04br3_rtc_observe(s,{0,2,58}));
 assert(a04br3_rtc_observe(s,{10,12,59}));
 assert(a04br3_rtc_observe(s,{20,22,59}));
 assert(a04br3_rtc_observe(s,{30,32,0}));
 auto b=a04br3_rtc_bounds(s); assert(b.valid && b.minTicks==8 && b.maxTicks==32 && b.rtcSeconds==1);
 A04BR3RtcReference same={}; assert(a04br3_rtc_observe(same,{0,1,1})); assert(a04br3_rtc_observe(same,{2,3,1})); assert(!a04br3_rtc_bounds(same).valid);
 A04BR3RtcReference skip={};assert(a04br3_rtc_observe(skip,{0,1,3}));assert(!a04br3_rtc_observe(skip,{2,3,5}));assert(!a04br3_rtc_bounds(skip).valid);
 A04BR3RtcReference reverse={};assert(a04br3_rtc_observe(reverse,{4,6,3}));assert(!a04br3_rtc_observe(reverse,{5,7,4}));
 A04BR3RtcReference invalid={};assert(!a04br3_rtc_observe(invalid,{0,0,60}));
 A04BR3RtcReference multi={}; assert(a04br3_rtc_observe(multi,{1,2,10}));
 assert(a04br3_rtc_observe(multi,{11,12,11})); assert(a04br3_rtc_observe(multi,{21,22,12}));
 assert(a04br3_rtc_observe(multi,{31,32,13})); auto m=a04br3_rtc_bounds(multi);assert(m.valid && m.rtcSeconds==2 && m.minTicks==9 && m.maxTicks==31);
 A04BR3RtcReference backwards={};assert(!a04br3_rtc_observe(backwards,{2,1,1}));
 A04BR3RtcReference wrapTimer={};assert(a04br3_rtc_observe(wrapTimer,{UINT64_MAX-2,UINT64_MAX-1,0}));assert(!a04br3_rtc_observe(wrapTimer,{0,1,1}));
 A04BR3RtcReference overlap={};assert(a04br3_rtc_observe(overlap,{0,1,0}));assert(a04br3_rtc_observe(overlap,{2,3,1}));assert(a04br3_rtc_observe(overlap,{3,4,2}));assert(!a04br3_rtc_bounds(overlap).valid);
 A04BR3RtcReference limit={};assert(a04br3_rtc_observe(limit,{0,0,0}));for(unsigned i=1;i<=59;++i)assert(a04br3_rtc_observe(limit,{2*i,2*i,i}));assert(a04br3_rtc_bounds(limit).valid);assert(!a04br3_rtc_observe(limit,{120,120,0}));
 // This deliberately demonstrates the unresolved hidden 60-update alias.
 A04BR3RtcReference alias={};assert(a04br3_rtc_observe(alias,{9,9,0}));assert(a04br3_rtc_observe(alias,{19,19,1}));assert(a04br3_rtc_observe(alias,{611,611,1}));assert(a04br3_rtc_observe(alias,{621,621,2}));assert(a04br3_rtc_bounds(alias).valid);
 A04BR3RtcReference ioState={}; IO io;assert(a04br3_rtc_sample(io,ioState));assert(io.phase==3);io.phase=0;io.ok=false;assert(!a04br3_rtc_sample(io,ioState));assert(ioState.invalid);assert(!a04br3_rtc_observe(ioState,{8,9,1}));
}
