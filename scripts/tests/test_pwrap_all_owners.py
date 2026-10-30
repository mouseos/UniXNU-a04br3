#!/usr/bin/env python3
"""Exercise the actual board WACS clients with a shared mocked hardware bus."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
def extract(path,start,end):
 s=(root/path).read_text(); return s[s.index(start):s.index(end,s.index(start))]
auto=extract('package/A04BR3AutoReset.cpp','bool wacs(bool write,','\nbool rtcTrigger')
input_=extract('package/A04BR3Input.cpp','int pwWait(unsigned fsm)','\n// MT6392 VGP1')
gpu=extract('package/A04BR3Gpu.cpp','bool clockDiagnosticWait(', '\nvoid clockDiagnosticSnapshot')
pre=r'''
#include <cassert>
#include <cstdint>
#include "A04BR3RegisterLock.h"
static bool busy; static int phase, fault, clears, commands, writePolls; static bool writing;
extern "C" bool a04br3_pwrap_try_lock(){if(busy)return false;busy=true;return true;}
extern "C" void a04br3_pwrap_unlock(){assert(busy);busy=false;}
extern "C" bool a04br3_pwrap_lock_bounded(unsigned){return a04br3_pwrap_try_lock();}
static volatile uint8_t *gPwrap=nullptr;
static constexpr unsigned kWacsRdata=0xa4,kWacsCmd=0xa0,kWacsVldclr=0xa8;
static void IODelay(unsigned){}
static uint32_t rd(volatile uint8_t*,unsigned){assert(busy);if(fault==6 && phase==1 && writing)return (1u<<21)|(2u<<16);if(fault==1 && phase==0)return (1u<<21)|(6u<<16);if(fault==2 && phase==1)return 1u<<21;if(fault==3 && phase==2)return (1u<<21)|(6u<<16);if(fault==4 && phase>0)return 0;if(phase==1 && writing && fault!=5 && writePolls++==0)return (1u<<21)|(2u<<16);if(phase==1 && !writing)return (1u<<21)|(6u<<16)|42u;return 1u<<21;}
static void wr(volatile uint8_t*,unsigned off,uint32_t value){assert(busy);if(off==0xa0){++commands;writing=(value>>31)!=0;phase=1;}else{assert(off==0xa8 && value==1);++clears;phase=2;}}
static uint32_t clockDiagnosticRead(volatile uint8_t *p,unsigned o){return rd(p,o);}
static void clockDiagnosticWrite(volatile uint8_t *p,unsigned o,uint32_t v){wr(p,o,v);}
'''
main=r'''
static void reset(int f){busy=false;phase=0;fault=f;clears=commands=writePolls=0;writing=false;}
static bool run(int owner){uint32_t out=0;uint16_t v=0;if(owner==0)return wacs(false,0x800a,0,&out);if(owner==1)return pwRead(0x800a,&v)==0;return clockDiagnosticPmicRead(gPwrap,0x800a,&v)==nullptr;}
int main(){for(int owner=0;owner<3;++owner){reset(0);assert(run(owner));assert(!busy && commands==1 && clears==1);for(int f=1;f<=4;++f){reset(f);assert(!run(owner));if(f==1){assert(!busy && clears==0);fault=0;assert(run((owner+1)%3));}else{assert(busy);int old=commands;assert(!run((owner+1)%3));assert(commands==old);}}}
 reset(4);assert(!wacs(true,0x800a,1,nullptr) && busy);reset(4);assert(pwWrite(0x800a,1)!=0 && busy);
 // fault 5: a write whose busy phase is never visible but which stays idle completes and releases the lock.
 reset(5);assert(wacs(true,0x800a,1,nullptr) && !busy && commands==1);reset(5);assert(pwWrite(0x800a,1)==0 && !busy && commands==1);
 // fault 6: a write that stays busy is uncertain and retains the lock.
 reset(6);assert(!wacs(true,0x800a,1,nullptr) && busy);reset(6);assert(pwWrite(0x800a,1)!=0 && busy);
 reset(0);assert(wacs(true,0x800a,1,nullptr) && !busy);reset(0);assert(pwWrite(0x800a,1)==0 && !busy);
}
'''
with tempfile.TemporaryDirectory(prefix='wacs-owners-') as d:
 p=Path(d)/'test.cpp';o=Path(d)/'test';p.write_text(pre+auto+input_+gpu+main)
 subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined','-I',str(root/'package'),str(p),'-o',str(o)],check=True)
 subprocess.run([str(o)],check=True)
print('PASS actual WACS owners: success and cross-owner quarantine, mocked MMIO')
