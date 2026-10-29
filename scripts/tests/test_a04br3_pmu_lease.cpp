#include "../../package/A04BR3PmuLease.h"
#include <cassert>
#include <cstdio>
int main(){A04BR3PmuLease l={};uint64_t g=0,h=0;
 assert(!a04br3_pmu_try(l,false,true,0,&g)&&!l.state);
 assert(!a04br3_pmu_try(l,true,false,0,&g)&&!l.state);
 assert(a04br3_pmu_try(l,true,true,0,&g)&&g&&a04br3_pmu_valid(l,true,0,g));
 assert(!a04br3_pmu_try(l,true,true,1,&h));assert(!a04br3_pmu_valid(l,true,1,g));assert(!a04br3_pmu_release(l,false,0,g));
 assert(a04br3_pmu_release(l,true,0,g));assert(!a04br3_pmu_valid(l,true,0,g));
 assert(a04br3_pmu_try(l,true,true,0,&h)&&h!=g);assert(!a04br3_pmu_release(l,true,0,g));assert(a04br3_pmu_release(l,true,0,h));
 puts("PASS local PMU lease exclusion, generation, gate and qualification refusal");}
