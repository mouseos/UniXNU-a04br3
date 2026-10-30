#!/usr/bin/env python3
"""Run the actual charge-maintenance policy and PIO code against a simulated MT8167 I2C2
controller carrying a BQ24160 (0x6b) and LC709203F (0x0b). Host only, never hardware."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CODE = r'''
#include "A04BR3ChargerMaintain.h"
#include <assert.h>
#include <string.h>
#include <vector>
using namespace a04charger;
using namespace a04charger::maintain;
static const uint8_t LK[8]={0x30,0,0x8c,0xa4,0x45,0x51,0,0x38};
// Stock lc709203f.c CRC routine, copied only as the oracle for crc8().
static uint8_t stockCrc(uint8_t before,uint8_t after){uint16_t t=uint16_t((before^after)<<8);
 for(int i=0;i<8;++i){if(t&0x8000)t^=0x8380;t<<=1;}return uint8_t(t>>8);}
struct Bus {
 uint16_t regs[64]={}; uint8_t bq[8]; uint16_t gauge[32]={}; bool corruptCrc=false, nack=false, hang=false;
 std::vector<uint8_t> tx, rx; unsigned rxAt=0; uint16_t status=0, fifo=0; uint64_t ns=1000;
 std::vector<std::pair<uint8_t,uint8_t>> bqWrites; unsigned gaugeWrites=0;
 Bus(){memcpy(bq,LK,8);gauge[9]=3812;gauge[0xd]=57;gauge[0xf]=563;gauge[8]=2982;}
 uint16_t rd(unsigned o){
  if(o==0xc)return status;
  if(o==0x30)return fifo;
  if(o==0)return rxAt<rx.size()?rx[rxAt++]:0xee;
  if(o==0x24)return hang?1:regs[o/2];
  return regs[o/2];
 }
 void wr(unsigned o,uint16_t v){
  if(o==0){tx.push_back(uint8_t(v));return;}
  if(o==0xc){status=0;return;}
  if(o==0x38){tx.clear();rx.clear();rxAt=0;return;}
  regs[o/2]=v; if(o==0x24)run();
 }
 void run(){
  if(hang){return;}
  regs[0x24/2]=0; // the controller clears START when the message completes
  unsigned addr=regs[2]>>1, txl=regs[0x14/2], rxl=regs[0x6c/2]; rx.clear();rxAt=0;
  assert(tx.size()==txl);
  if(nack){status=2;return;}
  if(addr==0x6b){
   if(rxl){assert(txl==1&&rxl==1&&tx[0]<8);rx.push_back(bq[tx[0]]);}
   else {assert(txl==2);bqWrites.push_back({tx[0],tx[1]});
     if(tx[0]==0) bq[0]&=0x7f; // TMR_RST self-clears
     else if(tx[0]==2) bq[2]=uint8_t(0x80|tx[1]); else if(tx[0]==7) bq[7]=uint8_t((tx[1]&0xf9)|(bq[7]&6)); else bq[tx[0]]=tx[1];}
  } else if(addr==0x0b){
   if(!rxl){++gaugeWrites;status=1;return;}
   assert(txl==1&&rxl==3);uint16_t v=gauge[tx[0]&31];uint8_t lo=uint8_t(v),hi=uint8_t(v>>8);
   uint8_t c=crc8(crc8(crc8(crc8(crc8(0,0x16),tx[0]),0x17),lo),hi); if(corruptCrc)c^=1;
   rx={lo,hi,c};
  } else {status=2;return;}
  fifo=uint16_t(rx.size()<<4); status=1;
 }
 uint64_t now(){return ns;} void delay(){ns+=5000;}
};
static void checkCeiling(const Write &w){
 assert(allowedWrite(w.reg,w.value));
 if(w.reg==2){assert(!(w.value&0x80)&&((w.value>>4)&7)<=2&&!(w.value&3)&&(w.value&0x0c)==0x0c);}
 if(w.reg==3){assert((w.value>>2)<=0x29&&!(w.value&2));}
 if(w.reg==5){assert((w.value>>3)<=0x0a&&(w.value&7)==1);}
 if(w.reg==7){assert(((w.value>>5)&3)==1&&!(w.value&0x80)&&(w.value&8)&&!(w.value&1));}
}
int main(){
 for(unsigned a=0;a<256;++a)for(unsigned b=0;b<256;++b)assert(crc8(uint8_t(a),uint8_t(b))==stockCrc(uint8_t(a),uint8_t(b)));
 // Inherited LK/stock-Linux image: exactly one TMR_RST per cycle, nothing else.
 {Bus bus;State st;Write w[4];unsigned n;
  for(int i=0;i<5;++i){bus.ns+=10000000000ULL;assert(step(st,bus,w,&n)==Kept&&n==0);}
  assert(bus.bqWrites.size()==5);for(auto &x:bus.bqWrites)assert(x.first==0&&x.second==0x80);
  assert(st.kicks==5&&st.worstGap>=10000000000ULL&&st.worstGap<10100000000ULL);
  uint16_t v;assert(gaugeRead(bus,0x09,&v)&&v==3812&&gaugeRead(bus,0x0d,&v)&&v==57);
  assert(bus.gaugeWrites==0);}
 // After a 30 s watchdog expiry the BQ24160 reloads lower DEFAULT settings (illustrative image):
 // the stock image is restored, kicked, and verified.
 {Bus bus;uint8_t def[8]={0x33,0,0x8c,0x00,0x45,0x02,0,0x18};memcpy(bus.bq,def,8);
  State st;Write w[4];unsigned n;assert(step(st,bus,w,&n)==Repaired&&n>0);
  for(unsigned i=0;i<n;++i)checkCeiling(w[i]);
  assert(bus.bqWrites[0].first==0&&bus.bqWrites[0].second==0x80);
  assert(st.latest.raw[3]==0xa4&&st.latest.raw[5]==0x51&&(st.latest.raw[7]&0xe9)==0x28&&(st.latest.raw[2]&0x7f)==0x0c);
  assert(st.sampled.raw[3]==0x00);
  bus.bq[0]=0x30;size_t k=bus.bqWrites.size();assert(step(st,bus,w,&n)==Kept&&n==0&&bus.bqWrites.size()==k+1);}
 // DDET_EN is preserved while VBREG/IINLIMIT are restored.
 {Snapshot s;memcpy(s.raw,LK,8);s.valid=255;s.raw[3]=0x15;Write w[4];assert(plan(s,w)==1&&w[0].reg==3&&w[0].value==0xa5);
  s.raw[3]=0xa5;assert(plan(s,w)==0);}
 // The kick is written right after registers 00 and 04, before any later read can fail.
 // Reads are numbered from 1 in issue order (00, 04, then 01,02,03,05,06,07).
 {Write w[4];unsigned n;
  for(unsigned failAt=3;failAt<=8;++failAt){Bus b;unsigned count=0;
   struct Wrap{Bus &b;unsigned failAt;unsigned &count;
    uint16_t rd(unsigned o){return b.rd(o);}
    void wr(unsigned o,uint16_t v){ if(o==0x24&&b.regs[0x6c/2]){ if(++count==failAt){b.nack=true;} } b.wr(o,v); b.nack=false; }
    uint64_t now(){return b.now();} void delay(){b.delay();}};
   Wrap io{b,failAt,count};State s2;assert(step(s2,io,w,&n)==ReadError&&s2.kicks==1);
   assert(b.bqWrites.size()==1&&b.bqWrites[0].first==0);}
  Bus b;unsigned count=0;
  struct Wrap2{Bus &b;unsigned &count;
   uint16_t rd(unsigned o){return b.rd(o);}
   void wr(unsigned o,uint16_t v){ if(o==0x24&&b.regs[0x6c/2]&&++count==2)b.nack=true; b.wr(o,v); b.nack=false; }
   uint64_t now(){return b.now();} void delay(){b.delay();}};
  Wrap2 io{b,count};State s3;assert(step(s3,io,w,&n)==ReadError&&s3.kicks==0&&b.bqWrites.empty());}
 // Every single-register perturbation: planned writes never exceed stock and are whitelisted.
 for(unsigned reg:{2U,3U,5U,7U})for(unsigned v=0;v<256;++v){Snapshot s;memcpy(s.raw,LK,8);s.raw[reg]=uint8_t(v);s.valid=255;
  Write w[4];unsigned n=plan(s,w);for(unsigned i=0;i<n;++i)checkCeiling(w[i]);
  if(reg==2){uint8_t t=target2(uint8_t(v));assert(((t>>4)&7)<=((v>>4)&7));} // IUSB never raised
 }
 // A host-connected IUSB 500 mA is kept; anything above is clamped to 500 mA.
 {Snapshot s;memcpy(s.raw,LK,8);s.valid=255;s.raw[2]=0xac;Write w[4];assert(plan(s,w)==0);
  s.raw[2]=0xdc;assert(plan(s,w)==1&&w[0].reg==2&&w[0].value==0x2c);
  s.raw[2]=0x8e;assert(plan(s,w)==1&&w[0].value==0x0c);} // CE disabled -> re-enabled like stock
 // Charger-reported faults other than an expired host watchdog: kick only, never override.
 for(unsigned f:{1U,2U,4U,5U,6U,7U}){Bus bus;bus.bq[0]=uint8_t(0x70|f);bus.bq[3]=0;State st;Write w[4];unsigned n;
  assert(step(st,bus,w,&n)==Kept&&n==0&&bus.bqWrites.size()==1&&bus.bqWrites[0].first==0);
  assert(st.faultHistory&(1U<<f));}
 // Wrong ID: no write at all.
 {Bus bus;bus.bq[4]=0x85;State st;Write w[4];unsigned n;assert(step(st,bus,w,&n)==NotCharger&&bus.bqWrites.empty());}
 // NACK / hung controller are counted and retried, never latched.
 {Bus bus;bus.nack=true;State st;Write w[4];unsigned n;assert(step(st,bus,w,&n)==ReadError&&st.readErrors==1);
  bus.nack=false;assert(step(st,bus,w,&n)==Kept&&st.kicks==1);}
 {Bus bus;bus.hang=true;State st;Write w[4];unsigned n;assert(step(st,bus,w,&n)==ReadError&&bus.bqWrites.empty());
  bus.hang=false;assert(step(st,bus,w,&n)==Kept);}
 {Bus bus;bus.corruptCrc=true;uint16_t v=7;assert(!gaugeRead(bus,0x09,&v)&&v==7);}
 // Write whitelist: nothing but TMR_RST and the stock image reaches the bus.
 for(unsigned reg=0;reg<8;++reg)for(unsigned v=0;v<256;++v){Bus bus;bool ok=chargerWrite(bus,uint8_t(reg),uint8_t(v));
  assert(ok==allowedWrite(uint8_t(reg),uint8_t(v)));if(!ok)assert(bus.bqWrites.empty());
  if(ok)checkCeiling(Write{uint8_t(reg),uint8_t(v)});}
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='a04br3-charger-maintain-') as tmp:
    root = Path(tmp)
    (root / 'test.cpp').write_text(CODE)
    compiler = shutil.which('g++') or shutil.which('clang++')
    assert compiler
    subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-fno-omit-frame-pointer', '-I', str(ROOT / 'package'), str(root / 'test.cpp'), '-o', str(root / 'test')],
                   check=True)
    subprocess.run([str(root / 'test')], check=True)
print('PASS charge maintenance: stock ceiling, DEFAULT repair+verify, fault gating, retry after NACK/hang, LC709203F CRC, write whitelist')

