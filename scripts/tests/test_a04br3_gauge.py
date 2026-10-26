#!/usr/bin/env python3
"""Actual LC709203F PIO/PEC/read-only voltage client host test, never hardware."""
from pathlib import Path
import tempfile,subprocess,shutil
r=Path(__file__).resolve().parents[2]
code=r'''
#include "A04BR3GaugeCore.h"
#include <cassert>
#include <vector>
#include <utility>
using namespace a04gauge;
struct IO {
 uint64_t ns=100,slow=0,voltageSetupDelay=0;unsigned command=0,index=0;uint16_t mode=1,mV=3778,status=1,count=3;
 bool busy=false,badMode=false,badVoltage=false;uint8_t bytes[3]={};
 std::vector<std::pair<unsigned,unsigned>> writes;std::vector<unsigned> commands;
 uint64_t now(){return ns;}
 void delay(){ns+=5000;}
 uint16_t rd(unsigned a){
  if(a==0x24)return busy;
  if(a==0x0c){ns+=slow;return status;}
  if(a==0x30)return count<<4;
  if(a==0){assert(index<3);return bytes[index++];}
  return 0;
 }
 void wr(unsigned a,uint16_t v){
  writes.push_back({a,v});
  if(a==0){assert(v==0x15||v==9);command=v;commands.push_back(v);if(v==9)ns+=voltageSetupDelay;}
  if(a==4)assert(v==0x16); // never charger D6 or any arbitrary slave
  if(a==0x24){assert(v==1);uint16_t word=command==0x15?mode:mV;
   bytes[0]=word;bytes[1]=word>>8;bytes[2]=pec(command,bytes[0],bytes[1]);
   if(command==0x15?badMode:badVoltage)bytes[2]^=1;
   index=0;
  }
 }
};
int main(){
 // Published onsemi rev17 Read Word example: 3778mV C2 0E -> PEC86.
 assert(pec(9,0xc2,0x0e)==0x86);
 {IO io;auto s=sample(io);assert(s.valid&&s.voltage.value==3778&&s.mode.value==1);
  assert(io.commands==std::vector<unsigned>({0x15,9}));
  for(auto w:io.writes)assert(w.first!=0x28&&w.first!=0x50&&w.first!=0x70);
 }
 for(unsigned value:{0U,2U,0xffffU}){IO io;io.mode=value;auto s=sample(io);assert(!s.valid&&s.result==NotOperational&&io.commands.size()==1);}
 {IO io;io.badMode=true;auto s=sample(io);assert(!s.valid&&s.mode.result==Pec&&io.commands.size()==1);}
 {IO io;io.badVoltage=true;auto s=sample(io);assert(!s.valid&&s.voltage.result==Pec&&s.voltage.raw[2]!=0x86);}
 for(unsigned count=0;count<16;++count)if(count!=3){IO io;io.count=count;auto s=sample(io);assert(!s.valid&&s.mode.result==Fifo);}
 for(unsigned status:{2U,4U,6U}){IO io;io.status=status;assert(!sample(io).valid);}
 {IO io;io.busy=true;assert(!sample(io).valid&&io.writes.empty());}
 {IO io;io.status=0;auto s=sample(io);assert(!s.valid&&s.mode.result==Deadline&&io.ns<11000000);}
 {IO io;io.slow=60000000;auto s=sample(io);assert(!s.valid&&s.mode.result==Deadline);}
 {IO io;io.voltageSetupDelay=110000000;auto s=sample(io);assert(!s.valid&&s.result==Deadline);}
 for(unsigned voltage:{0U,2499U,4501U,65535U}){IO io;io.mV=voltage;auto s=sample(io);assert(!s.valid&&s.result==Range);}
 for(unsigned voltage:{2500U,4500U}){IO io;io.mV=voltage;assert(sample(io).valid);}
 for(unsigned reg=0;reg<256;++reg)if(reg!=9&&reg!=0x15){IO io;assert(readWord(io,reg).result==Transfer&&io.writes.empty());}
}
'''
with tempfile.TemporaryDirectory() as tmp:
 t=Path(tmp);(t/'test.cpp').write_text(code)
 subprocess.run([shutil.which('g++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(r/'package'),str(t/'test.cpp'),'-o',str(t/'test')],check=True)
 subprocess.run([str(t/'test')],check=True)
s=(r/'package/A04BR3Charger.cpp').read_text()
assert 'if(gaugeVoltage && result==a04charger::Maintained)' in s
assert s.index('const auto g=a04gauge::sample(ops)')<s.index('IOLockUnlock(bus);',s.index('const auto g=a04gauge::sample(ops)'))
assert '"a04br3-gauge-voltage"' in s and 'mV=unknown current=unknown' in s
print('PASS actual LC PIO/PEC: vendor golden3778mV, mode/CRC/FIFO/busy/NACK/timeout/range failures, strict read whitelist; client stays in charger bus lock and explicit opt-in')
