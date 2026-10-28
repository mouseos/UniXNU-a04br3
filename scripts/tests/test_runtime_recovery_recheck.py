#!/usr/bin/env python3
"""Exercise the production recovery check without MMIO."""
from pathlib import Path
import subprocess,tempfile
s=(Path(__file__).resolve().parents[2]/'package/A04BR3AutoReset.cpp').read_text()
a=s.index('extern "C" bool a04br3_runtime_recovery_recheck(void)');b=s.index('\nextern "C" void a04br3_autoreset_init',a)
pre='''#include <cassert>\n#include <cstdint>\nuint8_t fake; volatile uint8_t *gToprgu=nullptr,*gPwrap=nullptr; volatile uint32_t a04br3_runtime_recovery_ready=0;\nunsigned kWdtMode=0,kRtcPdn1=1,kRtcPdn1Fastboot=0x2000;uint32_t mode=0,rtc=0;bool wacsOk=true;\nuint32_t rd(volatile uint8_t *,unsigned){return mode;}\nbool wacs(bool write,uint32_t address,uint32_t,uint32_t *out){assert(!write && address==kRtcPdn1);if(!wacsOk)return false;*out=rtc;return true;}\n'''
post='''int main(){assert(!a04br3_runtime_recovery_recheck());gToprgu=&fake;gPwrap=&fake;mode=0x15;rtc=0x2000;assert(!a04br3_runtime_recovery_recheck());a04br3_runtime_recovery_ready=1;assert(a04br3_runtime_recovery_recheck());rtc=0;assert(!a04br3_runtime_recovery_recheck());rtc=0x2000;wacsOk=false;assert(!a04br3_runtime_recovery_recheck());wacsOk=true;mode=0x14;assert(!a04br3_runtime_recovery_recheck());mode=0x1d;assert(!a04br3_runtime_recovery_recheck());}\n'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'test.cpp';exe=Path(d)/'test';p.write_text(pre+s[a:b]+post);subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(p),'-o',str(exe)],check=True);subprocess.run([str(exe)],check=True)
print('PASS: live RTC/read failure/watchdog recheck')
