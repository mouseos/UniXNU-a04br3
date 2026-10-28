#!/usr/bin/env python3
"""Run the board try-lock with concurrent host threads."""
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2];source=(root/'package/A04BR3RegisterLock.cpp').read_text();header=(root/'package/A04BR3RegisterLock.h').read_text().replace('#pragma once','');a=source.index('static uint32_t pwrapBusy;');body=source[a:]
pre='''void IODelay(unsigned) {}\n#include <cassert>\n#include <cstdint>\n#include <atomic>\n#include <thread>\n'''
for name in ('A04BR3AutoReset.cpp','A04BR3Input.cpp','A04BR3Gpu.cpp'):
 assert 'A04BR3PwrapGuard guard' in (root/'package'/name).read_text(), name
post='''int main(){assert(a04br3_pwrap_try_lock());assert(!a04br3_pwrap_try_lock());assert(!a04br3_pwrap_lock_bounded(3));a04br3_pwrap_unlock();{A04BR3PwrapGuard g;assert(bool(g));assert(!a04br3_pwrap_try_lock());}assert(a04br3_pwrap_try_lock());a04br3_pwrap_unlock();std::atomic<int> inside{0},hits{0};auto worker=[&]{for(int i=0;i<100000;i++){A04BR3PwrapGuard g;if(g){assert(inside.fetch_add(1)==0);++hits;assert(inside.fetch_sub(1)==1);}}};std::thread a(worker),b(worker);a.join();b.join();assert(hits>0);}\n'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'test.cpp';exe=Path(d)/'test';p.write_text(pre+header+body+post);subprocess.run(['c++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread',str(p),'-o',str(exe)],check=True);subprocess.run([str(exe)],check=True)
print('PASS: WACS2 try-lock contention and scoped release')
