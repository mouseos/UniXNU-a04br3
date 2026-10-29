#!/usr/bin/env python3
"""Compile the board's actual gate/lease wrappers with a mock qualification."""
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'package/A04BR3AutoReset.cpp').read_text();a=s.index('// This local lease serializes');b=s.index('// The lock coordinates known board WACS2 users',a);source=s[a:b]
needle='static bool a04br3_pmu_access_qualified(unsigned cpu) { (void)cpu; return false; }'
assert source.count(needle)==1
# The real provider remains false. A test-only switch exercises the otherwise
# unreachable local lease/gate interlock without presenting it as evidence.
source=source.replace(needle,'static bool a04br3_pmu_access_qualified(unsigned cpu) { (void)cpu; return testQualified; }')
main='''
int main(){uint64_t gen=0;
 assert(a04br3_runtime_probe_begin());assert(!a04br3_pmu_lease_acquire(0,&gen));assert(a04br3_runtime_probe_end_checked());
 testQualified=true;assert(a04br3_runtime_probe_begin());assert(a04br3_pmu_lease_acquire(0,&gen)&&gen);
 assert(!a04br3_runtime_probe_end_checked());a04br3_runtime_probe_end();assert(a04br3_runtime_probe_active);
 assert(!a04br3_pmu_lease_release(1,gen));assert(a04br3_pmu_lease_valid(0,gen));
 assert(gWatchdogGate.lock());assert(!a04br3_runtime_probe_end_checked());assert(!a04br3_pmu_lease_valid(0,gen));gWatchdogGate.unlock();
 assert(a04br3_pmu_lease_release(0,gen));assert(a04br3_runtime_probe_end_checked());assert(!a04br3_runtime_probe_active);
}
'''
with tempfile.TemporaryDirectory(prefix='pmu-gate-integration-') as d:
 p=Path(d);cpp=p/'test.cpp';exe=p/'test';cpp.write_text('#include <cassert>\n#include "A04BR3WatchdogGate.h"\n#include "A04BR3PmuLease.h"\nstatic A04BR3WatchdogGate gWatchdogGate={};\nstatic volatile uint32_t a04br3_runtime_probe_active=0;\nstatic bool testQualified=false;\n'+source+main)
 subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=all','-I',str(root/'package'),str(cpp),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
print('PASS actual gate/lease wrappers: refusal, active lease blocks release, nonblocking contention')
