// SPDX-License-Identifier: MIT
#pragma once
// Versioned little-endian inline /chosen record, owned by this boot's ADT.
// Never a physical scratch address, persistent variable, or fabricated state.
namespace a04charger {
constexpr unsigned HandoffBytes=128;
constexpr uint64_t HandoffMagic=0x314f524147484334ULL;
inline uint64_t get64(const uint8_t *p) { uint64_t v=0;for(unsigned i=0;i<8;++i)v|=uint64_t(p[i])<<(8*i);return v; }
inline void put64(uint8_t *p,uint64_t v) {for(unsigned i=0;i<8;++i)p[i]=uint8_t(v>>(8*i));}
inline uint64_t seal(const uint8_t *p) {uint64_t v=14695981039346656037ULL;for(unsigned i=0;i<120;++i){v^=p[i];v*=1099511628211ULL;}return v;}
inline uint64_t rawWord(const Snapshot &s){uint64_t v=0;for(unsigned i=0;i<8;++i)v|=uint64_t(s.raw[i])<<(8*i);return v;}
inline void publish(uint8_t *p,const Owner &o,bool active,uint64_t last,uint64_t freq,uint64_t exit,unsigned reason) {
  for(unsigned i=0;i<HandoffBytes;++i)p[i]=0;
  const uint64_t words[]={HandoffMagic,HandoffBytes,active&&!o.stopped?1ULL:2ULL,last,freq,exit,o.kicks,o.faultHistory,o.first.valid,rawWord(o.first),o.latest.valid,rawWord(o.latest),reason};
  for(unsigned i=0;i<13;++i)put64(p+8*i,words[i]);
  put64(p+120,seal(p));
}
// nowPhysical/frequency come from CNTPCT_EL0/CNTFRQ_EL0 on BOTH sides.
// No comparison of EFI uptime ns with XNU mach_absolute_time origin.
inline bool adopt(Owner &o,const uint8_t *p,unsigned size,uint64_t nowPhysical,uint64_t frequency,uint64_t nowNs) {
  if(o.attempted||o.stopped||o.kicks||!p||size!=HandoffBytes||get64(p)!=HandoffMagic||get64(p+8)!=HandoffBytes||
     get64(p+16)!=1||get64(p+120)!=seal(p)||get64(p+32)!=frequency||
     !frequency||frequency>~uint64_t(0)/1000000000ULL||!get64(p+48)||
     (get64(p+56)&~1ULL)||get64(p+64)!=255||get64(p+80)!=255||
     (get64(p+72)&7)||get64(p+96)!=Maintained||get64(p+104)||get64(p+112)) {o.stopped=true;return false;}
  uint64_t last=get64(p+24),exited=get64(p+40);
  if(last>exited||exited>nowPhysical||nowPhysical-last>=20*frequency){o.stopped=true;return false;}
  // Conservatively round elapsed ns UP, preserving the original 20s gap bound.
  uint64_t delta=nowPhysical-last, fraction=(delta%frequency)*1000000000ULL;
  uint64_t elapsed=(delta/frequency)*1000000000ULL+fraction/frequency+(fraction%frequency!=0);
  if(nowNs>~uint64_t(0)-(20000000000ULL-elapsed)){o.stopped=true;return false;}
  o.handoffPending=true;o.handoffDeadline=nowNs+(20000000000ULL-elapsed);
  o.faultHistory=unsigned(get64(p+56));return true;
}
inline unsigned get32(const uint8_t *p){return unsigned(p[0])|(unsigned(p[1])<<8)|(unsigned(p[2])<<16)|(unsigned(p[3])<<24);}
inline bool named(const uint8_t *p,unsigned bytes,const char *s){unsigned i=0;for(;i<bytes&&s[i];++i)if(p[i]!=uint8_t(s[i]))return false;return i<bytes&&!p[i]&&!s[i];}
inline bool walkSlot(uint8_t *tree,unsigned size,unsigned &at,unsigned depth,uint8_t *&found) {
  if(depth>64||at>size||size-at<8)return false;
  unsigned props=get32(tree+at),children=get32(tree+at+4);at+=8;
  if(props>(size-at)/36)return false;
  bool chosen=false,hasName=false;uint8_t *slot=nullptr;
  for(unsigned i=0;i<props;++i){
    if(size-at<36)return false;
    auto key=tree+at;unsigned len=get32(key+32);at+=36;
    bool terminated=false;for(unsigned k=0;k<32;++k)if(!key[k])terminated=true;
    if(!terminated||len>size-at)return false;
    if(named(key,32,"name")){if(hasName)return false;hasName=true;chosen=named(tree+at,len,"chosen");}
    if(named(key,32,"a04br3-charger-owner")) {if(slot||len!=HandoffBytes)return false;slot=tree+at;}
    at+=len;if(at>size||size-at<((4-at%4)%4))return false;at+=(4-at%4)%4;
  }
  if(slot){if(depth!=1||!chosen||found)return false;found=slot;}
  if(children>(size-at)/8)return false;
  for(unsigned i=0;i<children;++i)if(!walkSlot(tree,size,at,depth+1,found))return false;
  return true;
}
inline uint8_t *findSlot(uint8_t *tree,unsigned size){unsigned at=0;uint8_t *p=nullptr;return walkSlot(tree,size,at,0,p)&&at==size?p:nullptr;}

}
