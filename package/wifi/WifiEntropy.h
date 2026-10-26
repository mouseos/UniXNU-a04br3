// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
// MT8167 TRNG register facts: former bootshim/rng.c; Linux mtk-rng.c.
// io owns the TRNG, holds its lock, and supplies monotonic ns. No timer/MAC
// fallback. Basic stuck-source checks are NOT entropy-quality certification.
template<class IO> bool a04_wifi_entropy(IO &io,uint8_t out[64]) {
  uint8_t local[64]={};bool ok=false;
  bool gated=(io.topRead(0x3c)&(1u<<5))!=0;
  if(gated)io.topWrite(0x9c,1u<<5);
  if(io.topRead(0x3c)&(1u<<5)){if(gated)io.topWrite(0x6c,1u<<5);return false;}
  unsigned control=io.read(0),previous=0;
  if(control!=0xffffffffu){
    io.write(0,control|1u);ok=true;
    for(unsigned i=0;i<16&&ok;i++){
      uint64_t start=io.now();unsigned attempts=100000;
      while(!(io.read(0)&(1u<<31))){
        uint64_t now=io.now();if(!attempts--||now<start||now-start>=10000000ULL){ok=false;break;}
      }
      if(!ok)break;
      unsigned value=io.read(8);
      if(!value||value==0xffffffffu||(i&&value==previous)){ok=false;break;}
      previous=value;for(unsigned j=0;j<4;j++)local[4*i+j]=uint8_t(value>>(8*j));
    }
    if(!(control&1))io.write(0,control&~1u);
  }
  if(gated)io.topWrite(0x6c,1u<<5);
  if(ok)for(unsigned i=0;i<64;i++)out[i]=local[i];
  volatile uint8_t *wipe=local;for(unsigned i=0;i<64;i++)wipe[i]=0;
  return ok;
}
