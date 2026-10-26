#!/usr/bin/env python3
"""Compile/run actual charger policy and PIO with host operations, never hardware."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / 'package/A04BR3ChargerCore.h'
CODE = r'''
#include "A04BR3ChargerCore.h"
#include <assert.h>
#include <vector>
#include <utility>
#include <string.h>
using namespace a04charger;
struct Ops {
 uint8_t raw[8]={0x30,0,0x8c,0xa4,0x45,0x51,0,0x38};
 uint64_t ns=1000, readDelay=1000; int fail=-1; bool kickOk=true;
 std::vector<unsigned> reads; std::vector<uint8_t> writes;
 uint64_t now(){return ns;}
 bool read(unsigned reg,uint8_t *v){reads.push_back(reg);ns+=readDelay;if(int(reg)==fail)return false;*v=raw[reg];return true;}
 bool kick(uint8_t v){writes.push_back(v);return kickOk;}
};
struct IO {
 uint16_t regs[64]={}; uint64_t ns=100; unsigned polls=0; int outcome=1, count=1;
 std::vector<std::pair<unsigned,unsigned>> writes;
 IO(){regs[0x10/2]=0x2c;}
 uint16_t rd(unsigned o){
  if(o==0xc){++polls;return uint16_t(outcome);}
  if(o==0x30)return uint16_t(count<<4);
  if(o==0)return 0x45;
  return regs[o/2];
 }
 void wr(unsigned o,uint16_t v){writes.push_back({o,v}); if(o!=0xc&&o!=0x38&&o)regs[o/2]=v;}
 uint64_t now(){return ns;}
 void delay(){ns+=5000;}
};
int main(){
 for(unsigned reg: {1U,6U}) for(unsigned bit=0;bit<(reg==1?4U:6U);++bit) {
  Owner o;Ops p;p.raw[reg]^=1U<<bit;
  assert(step(o,p)==PolicyMismatch&&o.stopped&&p.writes.empty());
 }
 // Status-only register06 bits are not VINDPM settings.
 {Owner o;Ops p;p.raw[6]=0xc0;assert(step(o,p)==Maintained);}
 {Owner o;Ops p;o.handoffPending=true;o.handoffDeadline=p.ns+100000000ULL;p.readDelay=20000000;
  assert(step(o,p)==Deadline&&o.stopped&&p.writes.empty());}
 // No transaction exists until the real owner thread calls step; tests don't mirror a decoder.
 {Owner o;Ops p;assert(!o.attempted&&p.reads.empty()&&p.writes.empty());
  assert(step(o,p)==Maintained&&o.kicks==1&&o.first.valid==255);
  assert(p.reads==std::vector<unsigned>({0,1,2,3,4,5,6,7}));assert(p.writes==std::vector<uint8_t>({0x80}));
  p.ns+=10000000000ULL;assert(step(o,p)==Maintained&&o.kicks==2);
  assert(o.first.at==1000&&o.first.raw[0]==0x30);
  p.ns+=20000000000ULL;size_t n=p.reads.size();assert(step(o,p)==Deadline&&p.reads.size()==n);
  assert(step(o,p)==Stopped&&p.writes.size()==2);
 }
 // Every partial snapshot fails without a watchdog/configuration write; first FAULT is preserved.
 for(int fail=0;fail<8;++fail){Owner o;Ops p;p.fail=fail;p.raw[0]=0x33;
  assert(step(o,p)==ReadFailed&&o.stopped&&o.attempted&&p.writes.empty());
  assert(o.first.valid==unsigned((1<<fail)-1));if(fail)assert(o.first.raw[0]==0x33&&(o.faultHistory&8));
 }
 for(unsigned fault=1;fault<8;++fault){Owner o;Ops p;p.raw[0]|=fault;
  assert(step(o,p)==Fault&&p.writes.empty()&&o.first.raw[0]==p.raw[0]&&(o.faultHistory&(1U<<fault)));
 }
 // No setter/repair for DEFAULT policy, a different current/voltage/timer/source or disabled charging.
 for(unsigned reg: {0U,2U,3U,4U,5U,7U})for(unsigned bit=0;bit<8;++bit){
  Owner o;Ops p;p.raw[reg]^=1U<<bit;Result verdict=policy(Snapshot{{p.raw[0],p.raw[1],p.raw[2],p.raw[3],p.raw[4],p.raw[5],p.raw[6],p.raw[7]},255,0});
  if(verdict!=Maintained){assert(step(o,p)==verdict&&p.writes.empty());}
 }
 {Owner o;Ops p;p.raw[3]=0x14;assert(step(o,p)==PolicyMismatch&&p.writes.empty());}
 {Owner o;Ops p;p.raw[0]=0x40;p.raw[1]=0xc0;assert(step(o,p)==Maintained);assert(p.writes[0]==0x80);}
 {Owner o;Ops p;p.readDelay=200000000;assert(step(o,p)==Deadline&&p.writes.empty());}
 {Owner o;Ops p;p.kickOk=false;assert(step(o,p)==KickFailed&&o.stopped&&o.kicks==0);}
 // Execute the actual combined PIO reader; exact one-byte pointer/read and FIFO count are required.
 {IO io;uint8_t v=0;assert(transfer(io,true,4,&v)&&v==0x45);
  assert(io.regs[0x4/2]==0xd6&&io.regs[0x14/2]==1&&io.regs[0x6c/2]==1&&io.regs[0x18/2]==2);
  assert(!(io.regs[0x10/2]&4)&&(io.regs[0x10/2]&0x12)==0x12);
  for(auto w:io.writes)assert(w.first!=0x50&&w.first!=0x20&&w.first!=0x70);
 }
 for(int count:{0,2}){IO io;io.count=count;uint8_t v=0xaa;assert(!transfer(io,true,0,&v)&&v==0xaa);}
 for(int status:{2,4,6}){IO io;io.outcome=status;uint8_t v=0xaa;assert(!transfer(io,true,0,&v)&&v==0xaa);}
 {IO io;io.outcome=0;uint8_t v=0xaa;assert(!transfer(io,true,0,&v)&&io.polls<=2000&&v==0xaa);}
 {IO io;io.regs[0x24/2]=1;uint8_t v=0;assert(!transfer(io,true,0,&v)&&io.writes.empty());}
 // The only allowed data write is register00 with TMR_RST(+unchanged selector).
 for(unsigned reg=0;reg<8;++reg)for(unsigned value=0;value<256;++value){IO io;uint8_t v=value;
  bool allowed=reg==0&&(value==0x80||value==0x88);assert(transfer(io,false,reg,&v)==allowed);
  if(!allowed)assert(io.writes.empty());
 }
}
'''
with tempfile.TemporaryDirectory(prefix='a04br3-charger-') as tmp:
    root = Path(tmp)
    test = root / 'test.cpp'
    test.write_text(CODE)
    compiler = shutil.which('g++') or shutil.which('clang++')
    assert compiler
    binary = root / 'test'
    subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', '-I', str(HEADER.parent), str(test), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
    # Syntax-check the actual board source with compile-context substitutes, not a copied implementation.
    for name, body in {
        'IOKit/IOLib.h': '#pragma once\n#include <stdint.h>\n#include <stddef.h>\ntypedef uintptr_t vm_offset_t;typedef size_t vm_size_t;struct IOLock; IOLock *IOLockAlloc();void IOLockLock(IOLock*);void IOLockUnlock(IOLock*);void IOLog(const char*,...);void IODelay(unsigned);void IOSleep(unsigned);',
        'kern/thread.h': '#pragma once\ntypedef void* thread_t;typedef int wait_result_t;enum{KERN_SUCCESS=0};int kernel_thread_start(void(*)(void*,wait_result_t),void*,thread_t*);void thread_deallocate(thread_t);void thread_terminate_self();',
        'machine/machine_routines.h': '#pragma once\n#include <stdint.h>\nuint64_t mach_absolute_time();void absolutetime_to_nanoseconds(uint64_t,uint64_t*);',
        'pexpert/device_tree.h': '#pragma once\ntypedef void* DTEntry;int SecureDTLookupEntry(DTEntry,const char*,DTEntry*);int SecureDTGetProperty(DTEntry,const char*,const void**,unsigned*);',
        'pexpert/pexpert.h': '#pragma once\n#include <stddef.h>\nbool PE_parse_boot_argn(const char*,void*,size_t);',
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    cross = shutil.which('clang++-18') or shutil.which('clang++')
    assert cross
    subprocess.run([cross, '-target', 'aarch64-none-elf', '-ffreestanding', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-fsyntax-only', '-I', str(root), str(ROOT/'package/A04BR3Charger.cpp')], check=True)
print('PASS actual owner/policy/PIO: ASan/UBSan, first FAULT and partial reads, deadline, no policy repair, write whitelist, FIFO/NACK/busy/timeout, AArch64 board source syntax')
