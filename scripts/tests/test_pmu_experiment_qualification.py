#!/usr/bin/env python3
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'package/A04BR3AutoReset.cpp').read_text()
a=s.index('#if defined(A04BR3_PMUV3_EXPERIMENT)',s.index('static A04BR3PmuLease'))
b=s.index('extern "C" bool a04br3_pmu_qualification_available',a)
impl=s[a:b]
assert 'PE_parse_boot_argn("aq"' in s and 'experiment==1' in s
assert 'EL2/EL3/debug ownership and WDT recovery unverified' in s
main='''\nint main() {
 for(unsigned c=0;c<5;c++) assert(!a04br3_pmu_access_qualified(c));
#if A04BR3_PMUV3_EXPERIMENT
 __atomic_store_n(&gPmuExperiment,1U,__ATOMIC_RELEASE);
 for(unsigned c=0;c<4;c++) assert(a04br3_pmu_access_qualified(c));
 assert(!a04br3_pmu_access_qualified(4));
#endif
}\n'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d); src=p/'t.cpp';src.write_text('#include <cassert>\n'+impl+main)
 for mode in (0,1):
  exe=p/str(mode);subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',f'-DA04BR3_PMUV3_EXPERIMENT={mode}',str(src),'-o',str(exe)],check=True);subprocess.run([str(exe)],check=True)
print('PASS board experiment: default off, explicit opt-in, CPU range')
