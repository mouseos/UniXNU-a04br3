#!/usr/bin/env python3
"""Execute actual shared mux writer concurrently; no MMIO or device."""
from pathlib import Path
import subprocess,tempfile,shutil
r=Path(__file__).resolve().parents[2]
code=r'''
#include <mutex>
#include <thread>
#include <vector>
#include <cassert>
#include <map>
#include "A04BR3ChargerCore.h"
#include "A04BR3ChargerBoardCore.h"
#include "A04BR3RegisterLock.cpp"
IOLock *IOLockAlloc(){return new IOLock;}
void IOLockLock(IOLock *p){p->lock();}
bool IOLockTryLock(IOLock *p){return p->try_lock();}
void IOLockUnlock(IOLock *p){p->unlock();}
struct BoardIO {
 volatile uint8_t *base;std::map<unsigned,unsigned> r;
 BoardIO(volatile uint8_t *b):base(b){r[0x10000000]=0x10;r[0x10000024]=1U<<16;}
 bool updateMux(unsigned mask,unsigned bits){return a04br3_infra_update(base,mask,bits);}
 unsigned rd32(unsigned a){if(a==0x10001080){IOLockLock(infraLock);unsigned v=*reinterpret_cast<volatile uint32_t *>(base+0x80);IOLockUnlock(infraLock);return v;}return r[a];}
 void wr32(unsigned a,unsigned v){
  if(a==0x10000084)r[0x10000024]&=~v;
  else if(a==0x100053c8)r[0x100053c0]&=~v;
  else if(a==0x100053c4)r[0x100053c0]|=v;
  else if(a==0x10005538)r[0x10005530]&=~v;
  else r[a]=v;
 }
 uint16_t rd(unsigned a){return uint16_t(r[a]);}
 void wr(unsigned a,uint16_t v){r[a]=v;}
};
int main(){
 alignas(8) uint8_t regs[4096]={}; auto word=reinterpret_cast<uint32_t *>(regs+0x80);
 *word=10;assert(!a04br3_infra_update(regs,8,0)&&*word==10); // uninitialized: no unsafe fallback
 a04br3_registers_init();
 assert(!a04br3_infra_update(regs,4,0)&&*word==10);
 for(unsigned pass=0;pass<100;++pass){
  *word=0xfedcba9a;BoardIO io(regs);
  std::thread touch([&]{assert(a04br3_infra_update(regs,2,0));});
  assert(a04charger::initialize(io)==a04charger::BoardReady);
  touch.join();assert(*word==(0xfedcba9aU&~10U));
 }
 for(unsigned pass=0;pass<100;++pass){
  *word=0xfedcba9a;
  std::vector<std::thread> tasks;
  for(unsigned n=0;n<8;++n)tasks.emplace_back([&,n]{assert(a04br3_infra_update(regs,n&1?2:8,0));});
  for(auto &t:tasks)t.join();
  assert(*word==(0xfedcba9aU&~10U));
 }
}
'''
with tempfile.TemporaryDirectory() as tmp:
 t=Path(tmp);(t/'IOKit').mkdir();(t/'IOKit/IOLib.h').write_text('#pragma once\n#include <mutex>\nusing IOLock=std::mutex;IOLock *IOLockAlloc();void IOLockLock(IOLock*);bool IOLockTryLock(IOLock*);void IOLockUnlock(IOLock*);\n')
 (t/'machine').mkdir();(t/'machine/machine_routines.h').write_text('inline bool ml_at_interrupt_context(){return false;} inline bool ml_get_interrupts_enabled(){return true;}\n')
 (t/'test.cpp').write_text(code)
 subprocess.run([shutil.which('g++'),'-std=c++17','-pthread','-fsanitize=address,undefined','-I',str(t),'-I',str(r/'package'),str(t/'test.cpp'),'-o',str(t/'test')],check=True)
 subprocess.run([str(t/'test')],check=True)
# Actual call sites must participate in the SAME function; no private raw RMW.
s=(r/'package/A04BR3Input.cpp').read_text();assert 'a04br3_infra_update(gInfra, 1U << 1, 0)' in s
assert 'wr(gInfra, 0x80' not in s
s=(r/'package/A04BR3Charger.cpp').read_text();assert 'a04br3_infra_update(infra,mask,bits)' in s
assert 'io.updateMux(8, 0)' in (r/'package/A04BR3ChargerBoardCore.h').read_text()
print('PASS actual common mux writer: I2C0/I2C2 serialized, unrelated bits preserved, uninitialized refuses; both real call sites bound')
