#include "../../package/A04BR3CpuMeterRoute.h"
#include <cassert>
#include <cstdio>
#include <initializer_list>
struct Fake {
 uint32_t c=0,debug=0xfe,hopping=0x100,plltest=0x07f80000,mux=4,dcm=0,pll0=0x11,pll1=0x9a000,power=5,data=0;
 unsigned writes=0, routeWrites=0, failAt=0, failMeterAt=0; bool held=false, injectMeterConflict=false, changeHopping=false, noData=false, changedPll=false;
 bool lock(){if(held)return false;held=true;return true;} void unlock(){assert(held);held=false;}
 uint32_t top(unsigned o){return o==0x10?c:o==0x14?data:debug;}
 uint32_t infra(unsigned o){return o?dcm:mux;}
 uint32_t pll(unsigned o){return o==0x14?hopping:o==0x64?plltest:o==0x100?pll0:o==0x104?pll1:power;}
 void write(unsigned o,uint32_t v){assert(held&&o==0x10);writes++;c=writes==failMeterAt?0x123:v;if(v==(1U<<14))data=0;}
 void writePllTest(uint32_t v){assert(held);routeWrites++;if(routeWrites!=failAt)plltest=v;}
 void writeDebug(uint32_t v){assert(held);routeWrites++;if(routeWrites!=failAt)debug=v;}
 void beforeRoute(const A04BR3CpuMeterResult &r){assert(held&&r.debug==debug&&r.plltest==plltest&&routeWrites==0);}
 void delayRoute(unsigned x){assert(x==10);if(changeHopping)hopping=0x101;}
 void delay(unsigned){if(injectMeterConflict)c=0x123;if(changedPll)pll1++;if(!noData)data=20000;}
};
int main(){
 Fake f;auto r=a04br3_cpu_meter_routed(f);assert(r.status==0&&r.count==20000&&f.debug==0xfe&&f.plltest==0x07f80000&&f.hopping==0x100&&f.routeWrites==4&&!f.held&&r.debug==0xfe&&r.plltest==0x07f80000);
 Fake h;h.hopping=0x101;assert(a04br3_cpu_meter_routed(h).status==2&&h.routeWrites==0);
 Fake b;b.c=4;assert(a04br3_cpu_meter_routed(b).status==2&&b.routeWrites==0);
 for(unsigned n=1;n<=4;n++){Fake x;x.failAt=n;auto a=a04br3_cpu_meter_routed(x);assert(a.status==9&&!x.held&&a04br3_cpu_meter_requires_recovery(a.status));}
 Fake debugFail;debugFail.failAt=2;auto df=a04br3_cpu_meter_routed(debugFail);assert(df.status==9&&debugFail.plltest==0x07f80000&&debugFail.debug==0xfe&&debugFail.routeWrites==3);
 Fake conflict;conflict.injectMeterConflict=true;assert(a04br3_cpu_meter_routed(conflict).status==9&&conflict.routeWrites==2);
 for(unsigned n: {1u,4u}){Fake x;x.failMeterAt=n;auto a=a04br3_cpu_meter_routed(x);assert(a.status==9&&x.routeWrites==2&&a04br3_cpu_meter_requires_recovery(a.status)&&a.debug==0xfe&&a.plltest==0x07f80000);}
 Fake nodata;nodata.noData=true;auto nd=a04br3_cpu_meter_routed(nodata);assert(nd.status==5&&nodata.routeWrites==4&&nodata.debug==0xfe&&nodata.plltest==0x07f80000);
 Fake pllchange;pllchange.changedPll=true;auto pc=a04br3_cpu_meter_routed(pllchange);assert(pc.status==7&&pc.pll1==0x9a000&&pllchange.routeWrites==4&&pllchange.debug==0xfe&&pllchange.plltest==0x07f80000);
 Fake race;race.changeHopping=true;assert(a04br3_cpu_meter_routed(race).status==9&&race.routeWrites==2);
 puts("PASS: route preservation, failed writes, conflict, and ARM hopping");
}
