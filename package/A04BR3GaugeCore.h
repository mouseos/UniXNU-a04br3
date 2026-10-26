// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
// LC709203F/D rev17 pp7..12, CT3 lc709203f.c, board DTS I2C2 0x0b.
// Register reads only: power mode15 and voltage09. No charger/profile/wake writes.
namespace a04gauge {
enum Result { Ok, ModeReadFailed, NotOperational, VoltageReadFailed, Busy, Transfer, Fifo, Pec, Deadline, Range };
struct Word { uint8_t raw[3]={};unsigned count=0;uint16_t value=0;Result result=Transfer; };
struct Sample { Word mode,voltage;Result result=ModeReadFailed;uint64_t at=0;bool valid=false; };
inline uint8_t crcByte(uint8_t crc,uint8_t b){crc^=b;for(unsigned bit=0;bit<8;++bit)crc=uint8_t((crc<<1)^((crc&0x80)?7:0));return crc;}
inline uint8_t pec(uint8_t reg,uint8_t lo,uint8_t hi){uint8_t c=0;const uint8_t bytes[]={0x16,reg,0x17,lo,hi};for(unsigned i=0;i<5;++i)c=crcByte(c,bytes[i]);return c;}
// Caller holds the SAME I2C2 bus lock as the BQ owner. No independent client thread.
template<class IO> Word readWord(IO &io,uint8_t reg) {
  Word w;if(reg!=0x09&&reg!=0x15)return w;
  if(io.rd(0x24)&1){w.result=Busy;return w;}
  io.wr(8,0);io.wr(0x0c,7);io.wr(0x38,1);
  io.wr(0x10,uint16_t((io.rd(0x10)&~0x3eU)|0x3aU));
  io.wr(4,0x16);io.wr(0x14,1);io.wr(0x6c,3);io.wr(0x18,2);
  io.wr(0,reg);io.wr(0x24,1);
  uint64_t start=io.now();
  for(unsigned n=0;n<2000;++n){
    uint64_t elapsed=io.now();
    if(elapsed<start||elapsed-start>=10000000ULL){w.result=Deadline;return w;}
    uint16_t status=io.rd(0x0c);
    uint64_t checked=io.now();if(checked<start||checked-start>=10000000ULL){w.result=Deadline;return w;}
    if(status&6){io.wr(0x0c,7);return w;}
    if(status&1){
      if(((io.rd(0x30)>>4)&15)!=3){w.result=Fifo;io.wr(0x0c,7);return w;}
      for(unsigned i=0;i<3;++i){w.raw[i]=uint8_t(io.rd(0));++w.count;}
      io.wr(0x0c,7);
      if(pec(reg,w.raw[0],w.raw[1])!=w.raw[2]){w.result=Pec;return w;}
      w.value=uint16_t(w.raw[0]|(uint16_t(w.raw[1])<<8));w.result=Ok;return w;
    }
    uint64_t now=io.now();if(now<start||now-start>=10000000ULL){w.result=Deadline;return w;}io.delay();
  }
  w.result=Deadline;return w;
}
template<class IO> Sample sample(IO &io){
  Sample s;s.at=io.now();s.mode=readWord(io,0x15);
  if(s.mode.result!=Ok)return s;
  if(s.mode.value!=1){s.result=NotOperational;return s;}
  s.voltage=readWord(io,0x09);
  if(s.voltage.result!=Ok){s.result=VoltageReadFailed;return s;}
  // Manufacturer operating VDD 2.5..4.5V. Keep raw even outside that range;
  // never turn an invalid/unqualified conversion into a valid 0mV observation.
  if(s.voltage.value<2500||s.voltage.value>4500){s.result=Range;return s;}
  uint64_t end=io.now();if(end<s.at||end-s.at>=100000000ULL){s.result=Deadline;return s;}
  s.result=Ok;s.valid=true;return s;
}
}
