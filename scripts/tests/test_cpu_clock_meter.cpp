#include "../../package/A04BR3CpuClockMeter.h"
#include <cassert>
#include <cstdint>
#include <cstdio>

struct Fake {
  uint32_t c=0,debug=0,hopping=0,plltest=0x287,mux=4,dcm=0,pll0=0x11,pll1=0x9a000,power=5,data=0;
  unsigned writes=0,delays=0,failAt=0; bool available=true,held=false,conflict=false,changed=false,changed_test=false,zero=false,badReset=false,unsettled=false;
  bool lock(){if(!available)return false;assert(!held);held=true;return true;}
  void unlock(){assert(held);held=false;}
  uint32_t top(unsigned o){if(o==0x10)return c;if(o==0x14)return data;assert(o==0x38);return debug;}
  uint32_t infra(unsigned o){if(o==0)return mux;assert(o==0x14);return dcm;}
  uint32_t pll(unsigned o){if(o==0x14)return hopping;if(o==0x64)return plltest;if(o==0x100)return pll0;if(o==0x104)return pll1;assert(o==0x110);return power;}
  void write(unsigned o,uint32_t v){assert(held&&o==0x10);++writes;c=(writes==failAt)?0x123:v;if(v==(1U<<14)){data=badReset?9:0;}}
  void delay(unsigned us){assert(held&&(us==40||us==10));++delays;if(conflict)c=0x123;if(changed)pll1++;if(changed_test)plltest++;if(!zero)data=(unsettled&&delays==2)?20001:20000;}
};
int main(){
  Fake a;auto r=a04br3_cpu_meter(a);assert(r.status==0&&r.count==20000&&r.khz==1000000&&a.c==0&&!a.held&&a.writes==5);
  Fake other;other.hopping=0x100;auto other_result=a04br3_cpu_meter(other);assert(other_result.status==0&&other.hopping==0x100&&other_result.hopping==0x100);
  Fake other_arm;other_arm.hopping=0x101;assert(a04br3_cpu_meter(other_arm).status==2&&other_arm.writes==0);
  Fake wrong_test;wrong_test.plltest=0;assert(a04br3_cpu_meter(wrong_test).status==2&&wrong_test.writes==0);
  Fake busy;busy.available=false;assert(a04br3_cpu_meter(busy).status==1&&busy.writes==0);
  for(int n=0;n<7;n++){Fake f;if(n==0)f.c=1;if(n==1)f.debug=1;if(n==2)f.hopping=1;if(n==3)f.mux=0;if(n==4)f.dcm=1;if(n==5)f.pll0=0;if(n==6)f.power=2;auto x=a04br3_cpu_meter(f);assert(x.status==2&&!f.held&&f.writes==0);}
  Fake first;first.failAt=1;assert(a04br3_cpu_meter(first).status==3&&first.writes==1&&first.c==0x123&&!first.held);
  Fake restore;restore.failAt=4;assert(a04br3_cpu_meter(restore).status==6&&restore.writes==4&&restore.c==0x123&&!restore.held);
  assert(a04br3_cpu_meter_requires_recovery(3)&&a04br3_cpu_meter_requires_recovery(4)&&a04br3_cpu_meter_requires_recovery(6));
  assert(!a04br3_cpu_meter_requires_recovery(0)&&!a04br3_cpu_meter_requires_recovery(1)&&!a04br3_cpu_meter_requires_recovery(2)&&!a04br3_cpu_meter_requires_recovery(5)&&!a04br3_cpu_meter_requires_recovery(7)&&!a04br3_cpu_meter_requires_recovery(8));
  Fake z;z.badReset=true;assert(a04br3_cpu_meter(z).status==3&&z.c==0&&z.writes==2&&!z.held);
  Fake clash;clash.conflict=true;assert(a04br3_cpu_meter(clash).status==4&&clash.c==0x123&&clash.writes==3&&!clash.held);
  Fake progress;progress.unsettled=true;assert(a04br3_cpu_meter(progress).status==8&&progress.c==0&&!progress.held);
  Fake none;none.zero=true;assert(a04br3_cpu_meter(none).status==5&&none.c==0&&!none.held);
  Fake race;race.changed=true;assert(a04br3_cpu_meter(race).status==7&&race.c==0&&!race.held);
  Fake test_race;test_race.changed_test=true;assert(a04br3_cpu_meter(test_race).status==7&&test_race.c==0&&!test_race.held);
  puts("PASS: local exclusion, preconditions, zero/reset, interference, PLL change and idle restoration");
}
