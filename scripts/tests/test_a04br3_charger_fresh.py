#!/usr/bin/env python3
"""Run the actual bounded retry path using deterministic fake time and I2C."""
from pathlib import Path
import shutil, subprocess, tempfile
root = Path(__file__).resolve().parents[2]
source = r'''
#include "A04BR3ChargerFresh.h"
#include <cassert>
using namespace a04charger;
struct Ops {
  uint64_t t=100, delay=0; unsigned reads=0, writes=0, slowAttempts=1; int faultAttempt=-1, badAttempt=-1, failAttempt=-1;
  uint64_t now(){return t;}
  bool read(unsigned reg,uint8_t *v){
    unsigned a=reads/8; ++reads;
    if(int(a)==failAttempt && reg==1)return false;
    const uint8_t values[]={0x30,0,0x8c,0xa4,0x45,0x51,0,0x38};
    *v=values[reg]; if(int(a)==faultAttempt && reg==0)*v|=1;
    if(int(a)==badAttempt && reg==3)*v=0x14;
    if(reg==0 && a<slowAttempts)t+=2200000000ULL;else t+=1000;
    return true;
  }
  bool kick(uint8_t v){assert(v==0x80);++writes; t+=delay;return true;}
};
int main(){unsigned attempts=0;
 {Owner o;Ops p;o.handoffPending=true;o.handoffDeadline=10000000000ULL;
  assert(stepFresh(o,p,attempts)==Maintained && attempts==2 && p.reads==16 && p.writes==1);
  assert(!o.stopped && o.kicks==1 && o.first.valid==255 && o.first.at==100 && o.faultHistory==1);
  assert(o.lastKick < p.now());}
 {Owner o;Ops p;p.faultAttempt=0;assert(stepFresh(o,p,attempts)!=Maintained && attempts==1 && !p.writes && o.stopped && (o.faultHistory&2));}
 {Owner o;Ops p;p.badAttempt=0;assert(stepFresh(o,p,attempts)!=Maintained && attempts==1 && !p.writes && o.stopped);}
 {Owner o;Ops p;p.failAttempt=0;assert(stepFresh(o,p,attempts)==ReadFailed && attempts==1 && !p.writes && o.stopped);}
 {Owner o;Ops p;p.slowAttempts=5;assert(stepFresh(o,p,attempts)==Deadline && attempts==3 && !p.writes && o.stopped);}
 {Owner o;Ops p;o.handoffPending=true;o.handoffDeadline=2100000000ULL;assert(stepFresh(o,p,attempts)==Deadline && attempts==1 && !p.writes && o.stopped);}
 {Owner o;Ops p;o.kicks=1;o.lastKick=1;p.t=19000000000ULL;assert(stepFresh(o,p,attempts)==Deadline && attempts==1 && !p.writes && o.stopped);}
 {Owner o;Ops p;p.slowAttempts=0;p.delay=2000000000ULL;assert(stepFresh(o,p,attempts)==Deadline && o.stopped && o.lastKick==100 && p.writes==1);}
 {Owner o;Ops p;p.slowAttempts=0;p.delay=3000000000ULL;o.handoffPending=true;o.handoffDeadline=2000000000ULL;
  assert(stepFresh(o,p,attempts)==Deadline && o.stopped && p.writes==1);}
}
'''
with tempfile.TemporaryDirectory(prefix='a04-charger-fresh-') as tmp:
    src = Path(tmp)/'test.cpp'; exe=Path(tmp)/'test'; src.write_text(source)
    subprocess.run([shutil.which('g++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(root/'package'),str(src),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
print('PASS actual bounded fresh snapshot retry, preserved fault history and deadlines')
