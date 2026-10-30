#!/usr/bin/env python3
"""Input powerOn(): the VGP1 voltage and enable writes are read back (an ignored WACS write must fail)."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'package/A04BR3Input.cpp').read_text()
code=s[s.index('int pwWait(unsigned fsm)'):s.index('\nint transfer(')]
pre=r'''
#include <cassert>
#include <cstdint>
#include <map>
#include "A04BR3RegisterLock.h"
static bool busy; static std::map<unsigned,uint16_t> pmic; static unsigned ignore; static int phase; static unsigned addr; static bool writing;
extern "C" bool a04br3_pwrap_try_lock(){if(busy)return false;busy=true;return true;}
extern "C" void a04br3_pwrap_unlock(){assert(busy);busy=false;}
extern "C" bool a04br3_pwrap_lock_bounded(unsigned){return a04br3_pwrap_try_lock();}
static volatile uint8_t *gPwrap=nullptr;
static void IODelay(unsigned){}
static void IOSleep(unsigned){}
static uint32_t rd(volatile uint8_t*,unsigned o){if(o==0x9c)return 1;assert(o==0xa4);if(phase==1 && !writing)return (1u<<21)|(6u<<16)|pmic[addr];return 1u<<21;}
static void wr(volatile uint8_t*,unsigned o,uint32_t v){assert(busy);if(o==0xa0){writing=(v>>31)!=0;addr=((v>>16)&0x7fff)*2;phase=1;if(writing){if(addr!=ignore)pmic[addr]=uint16_t(v&0xffff);phase=0;}}else{assert(o==0xa8 && v==1);phase=0;}}
'''
main=r'''
static void reset(unsigned ig){busy=false;pmic.clear();pmic[0x530]=0x0000;pmic[0x50a]=0x0001;ignore=ig;phase=0;}
int main(){
 reset(0);assert(powerOn()==0 && pmic[0x530]==0x60 && (pmic[0x50a]&0x8000) && !busy);
 reset(0x50a);assert(powerOn()==-8 && !(pmic[0x50a]&0x8000) && !busy);   // ignored enable write is detected
 reset(0x530);assert(powerOn()==-6 && !busy);                           // ignored voltage write is detected
}
'''
with tempfile.TemporaryDirectory(prefix='input-power-') as d:
 p=Path(d)/'test.cpp';o=Path(d)/'test';p.write_text(pre+code+main)
 subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined','-I',str(root/'package'),str(p),'-o',str(o)],check=True)
 subprocess.run([str(o)],check=True)
print('PASS Input powerOn reads back VGP1 voltage and enable, mocked PMIC')
