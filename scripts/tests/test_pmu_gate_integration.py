#!/usr/bin/env python3
"""Compile the board's actual gate/lease wrappers with a mock qualification."""
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'package/A04BR3AutoReset.cpp').read_text();a=s.index('// This local lease serializes');b=s.index('// The lock coordinates known board WACS2 users',a);source=s[a:b]
needle='static bool a04br3_pmu_access_qualified(unsigned cpu) {\n#if defined(A04BR3_PMUV3_EXPERIMENT) && A04BR3_PMUV3_EXPERIMENT\n  return cpu < 4 && __atomic_load_n(&gPmuExperiment,__ATOMIC_ACQUIRE)==1;\n#else\n  (void)cpu; return false;\n#endif\n}'
assert source.count(needle)==1
source=source.replace(needle,'static bool a04br3_pmu_access_qualified(unsigned cpu) { return cpu < 4 && testQualified; }')
main='''
int main(){uint64_t gen=0;assert(!a04br3_pmu_qualification_available(0));
 assert(a04br3_runtime_probe_begin());assert(!a04br3_pmu_lease_acquire(0,&gen));assert(a04br3_runtime_probe_end_checked());
 testQualified=true;assert(a04br3_pmu_qualification_available(0));assert(a04br3_runtime_probe_begin());assert(a04br3_pmu_lease_acquire(0,&gen)&&gen);
 assert(!a04br3_runtime_probe_end_checked());a04br3_runtime_probe_end();assert(a04br3_runtime_probe_active);
 assert(!a04br3_pmu_lease_release(1,gen));assert(a04br3_pmu_lease_valid(0,gen));
 assert(gWatchdogGate.lock());assert(!a04br3_runtime_probe_end_checked());assert(!a04br3_pmu_lease_valid(0,gen));gWatchdogGate.unlock();
 assert(a04br3_pmu_lease_release(0,gen));assert(a04br3_runtime_probe_end_checked());assert(!a04br3_runtime_probe_active);
 for(int i=0;i<100;++i){assert(a04br3_runtime_probe_begin());std::atomic<int> ready{0};std::atomic<bool> go{false};bool acquired=false,ended=false;uint64_t token=0;
  std::thread a([&]{++ready;while(!go.load()){} acquired=a04br3_pmu_lease_acquire(0,&token);});
  std::thread b([&]{++ready;while(!go.load()){} ended=a04br3_runtime_probe_end_checked();});
  while(ready.load()!=2){} go=true;a.join();b.join();assert(!(acquired&&ended));
  if(acquired){assert(a04br3_runtime_probe_active);assert(a04br3_pmu_lease_release(0,token));}
  if(a04br3_runtime_probe_active)assert(a04br3_runtime_probe_end_checked());
 }
}
'''
with tempfile.TemporaryDirectory(prefix='pmu-gate-integration-') as d:
 p=Path(d);cpp=p/'test.cpp';exe=p/'test';cpp.write_text('#include <cassert>\n#include <atomic>\n#include <thread>\n#include "A04BR3WatchdogGate.h"\n#include "A04BR3PmuLease.h"\nstatic A04BR3WatchdogGate gWatchdogGate={};\nstatic volatile uint32_t a04br3_runtime_probe_active=0;\nstatic bool testQualified=false;\n'+source+main)
 subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=all','-pthread','-I',str(root/'package'),str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
print('PASS actual gate/lease wrappers: refusal, active lease blocks release, nonblocking contention')
