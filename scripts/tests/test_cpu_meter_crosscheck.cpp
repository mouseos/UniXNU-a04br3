#include "../../package/A04BR3CpuMeterRoute.h"
#include <cassert>
#include <cstdio>
#include <vector>

struct Fake {
  uint32_t ctrl=0, debug=0x123400fe, test=0x07f80000, hop=0x100, mux=4, dcm=0, pll0=1, pll1=3, power=1, data=0;
  bool held=false, available=true; unsigned locks=0, unlocks=0, writes=0, routeWrites=0, sample=0, reads=0;
  unsigned failWrite=0, failRoute=0, zeroAt=0, unsettledAt=0, baselineAt=0, conflictAt=0, changeAt=0, changeKind=0; bool changeAfterDebugRestore=false; unsigned beforeChange=0; unsigned debugAfter=0, testAfter=0; bool raceDebugRead=false, raceTestRead=false;
  std::vector<unsigned> ids;
  bool lock(){++locks;if(!available)return false;assert(!held);held=true;return true;}
  void unlock(){assert(held);held=false;++unlocks;}
  uint32_t top(unsigned o){assert(held);if(o==0x10)return ctrl;if(o==0x38){if(writes==15 && ++debugAfter==4 && raceDebugRead)debug^=0x100;return debug;} assert(o==0x14); ++reads; if(baselineAt==sample+1 && ctrl==0)return 42; if(unsettledAt==sample && (ctrl & (1u<<15)) && reads%3==0)return data+1;return data;}
  uint32_t pll(unsigned o){assert(held);if(o==0x64 && writes==15 && ++testAfter==5 && raceTestRead)test^=0x400;return o==0x14?hop:o==0x64?test:o==0x100?pll0:o==0x104?pll1:power;}
  uint32_t infra(unsigned o){assert(held);return o?dcm:mux;}
  void write(unsigned o,uint32_t v){assert(held&&o==0x10);++writes;ctrl=writes==failWrite?0x123:v;if(v==(1u<<14))data=0;if(v&(1u<<15)){++sample;ids.push_back((v>>16)&0x7f);}}
  void writePllTest(uint32_t v){assert(held);++routeWrites;if(routeWrites!=failRoute)test=v;}
  void writeDebug(uint32_t v){assert(held);++routeWrites;if(routeWrites!=failRoute)debug=v;if(changeAfterDebugRestore && routeWrites==3)dcm^=2;}
  void beforeRoute(const A04BR3CpuMeterResult&){assert(held);if(beforeChange==1)debug^=1;if(beforeChange==2)dcm^=2;}
  void delayRoute(unsigned v){assert(held&&v==10);}
  void delay(unsigned v){assert(held);if(v==40){data=zeroAt==sample?0:(sample==1?20020:1040);if(conflictAt==sample)ctrl=0x123;if(changeAt==sample){if(changeKind==0)pll1++;else if(changeKind==1)dcm^=2;else if(changeKind==2)mux^=8;else if(changeKind==3)power^=4;else if(changeKind==4)debug^=0x100;else if(changeKind==5)test^=0x400;else hop^=0x100;}}}
};
int main(){
  Fake f; A04BR3CpuMeterCrosscheck c{}; auto r=a04br3_cpu_meter_crosscheck(f,c);
  assert(r.status==0 && c.attempted==3 && c.route_stage==4 && f.locks==1 && f.unlocks==1 && !f.held);
  assert(a04br3_cpu_crosscheck_feed_allowed(r,c));
  assert((f.ids==std::vector<unsigned>{25,7,24}) && f.ctrl==0 && f.debug==0x123400fe && f.test==0x07f80000 && f.routeWrites==4);
  for(unsigned i=0;i<3;i++)assert(c.sample[i].status==0 && c.sample[i].valid==7 && c.sample[i].baseline==0 && c.sample[i].count==(i?1040u:20020u) && c.sample[i].second==c.sample[i].count);
  Fake busy;busy.available=false;r=a04br3_cpu_meter_crosscheck(busy,c);assert(r.status==1 && c.route_stage==0 && !busy.writes && !busy.routeWrites && !c.attempted && a04br3_cpu_crosscheck_feed_allowed(r,c));
  Fake pre;pre.hop|=1;r=a04br3_cpu_meter_crosscheck(pre,c);assert(r.status==2 && c.route_stage==0 && !pre.writes && !pre.routeWrites && a04br3_cpu_crosscheck_feed_allowed(r,c));
  for(unsigned n=1;n<=2;n++){Fake changed;changed.beforeChange=n;r=a04br3_cpu_meter_crosscheck(changed,c);assert(r.status==2 && c.route_stage==0 && !changed.writes && !changed.routeWrites && a04br3_cpu_crosscheck_feed_allowed(r,c));}
  Fake firstWrite;firstWrite.failRoute=1;r=a04br3_cpu_meter_crosscheck(firstWrite,c);assert(r.status==9 && c.route_stage==0 && firstWrite.routeWrites==1 && !a04br3_cpu_crosscheck_feed_allowed(r,c));
  for(unsigned i=1;i<=3;i++){
    Fake z;z.zeroAt=i;r=a04br3_cpu_meter_crosscheck(z,c);assert(a04br3_cpu_crosscheck_feed_allowed(r,c) && r.status==5 && c.attempted==i && c.sample[i-1].valid==7 && c.sample[i-1].count==0 && c.route_stage==4 && z.routeWrites==4);
    Fake u;u.unsettledAt=i;r=a04br3_cpu_meter_crosscheck(u,c);assert(a04br3_cpu_crosscheck_feed_allowed(r,c) && r.status==8 && c.attempted==i && c.sample[i-1].valid==7 && c.route_stage==4);
    Fake b;b.baselineAt=i;r=a04br3_cpu_meter_crosscheck(b,c);assert(r.status==9 && c.attempted==i && c.sample[i-1].status==3 && c.sample[i-1].valid==1 && c.sample[i-1].baseline==42 && c.route_stage==2 && b.routeWrites==2);
    Fake x;x.conflictAt=i;r=a04br3_cpu_meter_crosscheck(x,c);assert(r.status==9 && c.attempted==i && c.sample[i-1].status==4 && c.sample[i-1].valid==1 && c.route_stage==2 && x.routeWrites==2);
    for(unsigned kind=0;kind<7;kind++) { Fake ch;ch.changeAt=i;ch.changeKind=kind;r=a04br3_cpu_meter_crosscheck(ch,c);assert(r.status==9 && c.sample[i-1].status==7 && c.attempted==i && c.route_stage==2 && ch.routeWrites==2 && !a04br3_cpu_crosscheck_feed_allowed(r,c)); }
    for(unsigned off: {1u,2u,3u,4u,5u}) { Fake w;w.failWrite=(i-1)*5+off;r=a04br3_cpu_meter_crosscheck(w,c);assert(r.status==9 && c.attempted==i && c.route_stage==2 && w.routeWrites==2 && !w.held); }
  }
  Fake debugRace;debugRace.raceDebugRead=true;r=a04br3_cpu_meter_crosscheck(debugRace,c);assert(r.status==9 && c.route_stage==2 && debugRace.routeWrites==2 && debugRace.debugAfter==4 && debugRace.debug==0x12340100 && !a04br3_cpu_crosscheck_feed_allowed(r,c));
  Fake testRace;testRace.raceTestRead=true;r=a04br3_cpu_meter_crosscheck(testRace,c);assert(r.status==9 && c.route_stage==3 && testRace.routeWrites==3 && testRace.testAfter==5 && testRace.test==0x07f80687 && !a04br3_cpu_crosscheck_feed_allowed(r,c));
  Fake partial;partial.changeAfterDebugRestore=true;r=a04br3_cpu_meter_crosscheck(partial,c);assert(r.status==9 && c.route_stage==3 && partial.routeWrites==3 && !a04br3_cpu_crosscheck_feed_allowed(r,c));
  for(unsigned i=1;i<=4;i++){Fake x;x.failRoute=i;r=a04br3_cpu_meter_crosscheck(x,c);assert(r.status==9 && !x.held && a04br3_cpu_meter_requires_recovery(r.status) && !a04br3_cpu_crosscheck_feed_allowed(r,c));}
  std::puts("PASS: crosscheck order, raw data, recovery and injected faults");
}
