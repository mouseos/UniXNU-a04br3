#!/usr/bin/env python3
"""本番rail取得コードの正常値・不安定値・未完了をホストで検証。"""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[2]
code = r'''
#include <cassert>
#include <cstdint>
#include <vector>
#include "A04BR3CpuRailCore.h"
using namespace a04br3rail;
struct IO {
 unsigned state=0, reads=0, delay_count=0, commands=0, acknowledgements=0;
 bool enabled=true, init=true, never_idle=false, never_done=false, never_ack=false, flip=false;
 uint16_t ctrl=0, value=0x60; unsigned addr=0;
 uint32_t read(unsigned off) {
  if (off==0x98 || off==0x9c) return enabled ? 1 : 0;
  assert(off==0xa4);
  if (!init) return 0;
  unsigned fsm=state;
  if (never_idle && !commands) fsm=1;
  if (never_done && commands && !acknowledgements) fsm=1;
  if (never_ack && acknowledgements) fsm=1;
  uint16_t data=addr==0x216 ? ctrl : value;
  if (flip && addr!=0x216 && commands==3) data++;
  return (1U<<21) | (fsm<<16) | data;
 }
 void write(unsigned off,uint32_t val) {
  if(off==0xa0){ assert(!(val & 0x80000000)); addr=(val>>16)*2; ++commands; state=6; }
  else { assert(off==0xa8 && val==1 && state==6); ++acknowledgements; state=0; }
 }
 void delay() { ++delay_count; }
};
int main(){
 Snapshot s={0xbeef,0xbeef,0xdeadbeef}; IO a;
 assert(capture(a,s)==ok && a.commands==4 && a.acknowledgements==4);
 assert(s.control==0 && s.selector==0x60 && s.nominal_uv==1300000);
 IO b; b.ctrl=2; b.value=0x50; assert(capture(b,s)==ok && s.nominal_uv==1200000);
 IO c; c.enabled=false; s.nominal_uv=0xdeadbeef; assert(capture(c,s)==unavailable && s.nominal_uv==0xdeadbeef && !c.commands);
 IO d; d.never_idle=true; assert(capture(d,s)==timeout_before_command && d.delay_count==1000 && !d.commands);
 IO e; e.never_done=true; assert(capture(e,s)==timeout_after_command && e.commands==1 && !e.acknowledgements);
 IO f; f.never_ack=true; assert(capture(f,s)==timeout_after_command && f.acknowledgements==1);
 IO g; g.flip=true; assert(capture(g,s)==changed && s.nominal_uv==0xdeadbeef);
 IO h; h.init=false; assert(capture(h,s)==timeout_before_command && !h.commands);
}
'''
with tempfile.TemporaryDirectory(prefix='a04br3-cpu-rail-') as tmp:
    p=Path(tmp); (p/'test.cpp').write_text(code)
    subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(root/'package'),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
print('PASS: rail設定値、取得失敗、変更検出、送信後timeout、PMIC書込み禁止')
