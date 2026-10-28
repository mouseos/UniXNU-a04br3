#!/usr/bin/env python3
"""Exercise the production watchdog check without MMIO."""
from pathlib import Path
import subprocess
import tempfile
s=(Path(__file__).resolve().parents[2]/'package/A04BR3AutoReset.cpp').read_text()
a=s.index('extern "C" bool a04br3_runtime_watchdog_armed(void)')
b=s.index('\nextern "C" void a04br3_autoreset_init',a)
body=s[a:b]
pre='''#include <cassert>\n#include <cstdint>\nuint8_t fake; volatile uint8_t *gToprgu=nullptr; volatile uint32_t a04br3_runtime_recovery_ready=0;\nunsigned kWdtMode=0; uint32_t mode=0;\nuint32_t rd(volatile uint8_t *,unsigned){return mode;}\n'''
post='''int main(){assert(!a04br3_runtime_watchdog_armed());gToprgu=&fake;mode=0x15;assert(!a04br3_runtime_watchdog_armed());a04br3_runtime_recovery_ready=1;assert(a04br3_runtime_watchdog_armed());mode=0x14;assert(!a04br3_runtime_watchdog_armed());mode=0x1d;assert(!a04br3_runtime_watchdog_armed());mode=0x55;assert(!a04br3_runtime_watchdog_armed());}\n'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'test.cpp';exe=Path(d)/'test';p.write_text(pre+body+post)
 subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(p),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
print('PASS: watchdog mode recheck rejects missing or changed recovery configuration')
