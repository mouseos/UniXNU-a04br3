#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2]
code=r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>
#include "WifiEntropy.h"
struct IO {
 unsigned gate=32,control=0,word=0,kind=0;uint64_t time=0;std::vector<unsigned>writes;
 unsigned topRead(unsigned o){assert(o==0x3c);return gate;}
 void topWrite(unsigned o,unsigned v){assert(v==32&&(o==0x9c||o==0x6c));writes.push_back(o);if(o==0x9c)gate&=~v;else gate|=v;}
 unsigned read(unsigned o){assert(o==0||o==8);if(o==0)return kind==1?0xffffffffu:control|(kind==2?0:0x80000000u);++word;return kind==3?0:kind==4?0xffffffffu:kind==5?7:word*19;}
 void write(unsigned o,unsigned v){assert(!o);control=v;writes.push_back(v);}
 uint64_t now(){time+=1000000;return time;}
};
int main(){
 for(unsigned mode=0;mode<6;mode++)for(unsigned gated=0;gated<2;gated++){
  IO io;io.kind=mode;io.gate=gated?32:0;uint8_t out[64];memset(out,0xcd,64);
  bool ok=a04_wifi_entropy(io,out);assert(ok==(mode==0));
  assert(io.gate==(gated?32u:0u));assert(!(io.control&1));
  if(!ok)for(auto v:out)assert(v==0xcd);
  if(ok)assert(out[0]==19&&out[60]==16*19%256);
 }
}
'''
with tempfile.TemporaryDirectory() as t:
 p=Path(t);(p/'test.cpp').write_text(code)
 subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(root/'package/wifi'),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print('PASS actual TRNG helper: fresh words, stuck/zero/all-ones/unavailable/deadline refusal, output unchanged, original gate/control restored')
