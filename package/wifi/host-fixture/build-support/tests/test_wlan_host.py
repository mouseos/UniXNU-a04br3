# SPDX-License-Identifier: MIT
"""Run the actual fn1 headers with a bounded, byte-checking CMD52/53 card."""
import unittest
import test_wmt_protocol as codecs

# This emulates registers/FIFO, not firmware success: every TX is checked and
# ACK/readiness/credits are injected independently, including fault cases.
FAKE = r'''
#include "A04BR3WLANFirmware.h"
struct card {
 unsigned enable,own,ownership_timeout,reads,txs,pda,configs,start,ready_polls;
 unsigned seq,rx_size,corrupt_seq,status,fail_tx,ready_timeout,cpu,ac,ffa,no_credit;
 unsigned section_offset,section_index;
 const struct a04_wlan_firmware *fw;
};
static inline int command(void *ctx,unsigned cmd,uint32_t arg,unsigned response,uint32_t *out){
 struct card *c=(struct card *)ctx;(void)response;assert(cmd==52);
 unsigned fn=(arg>>28)&7,addr=(arg>>9)&0x1ffff,w=arg>>31,v=arg&255;*out=0;
 if(!fn && addr==2){if(w)c->enable=v;else *out=c->enable;}
 else if(!fn && addr==3)*out=c->enable;
 else if(fn==1 && addr==5){assert(w && v==2);c->own=1;}
 else if(fn==1 && addr==4)assert(w && v==2);
 else assert(!fn && (addr==0x110 || addr==0x111));
 return 0;
}
static inline void delay(void *ctx,unsigned us){(void)ctx;assert(us && us<=10000);}
static inline int transfer(void *ctx,uint32_t arg,void *buffer,unsigned n){
 struct card *c=(struct card *)ctx;uint8_t *p=(uint8_t *)buffer;
 unsigned fn=(arg>>28)&7,addr=(arg>>9)&0x1ffff,w=arg>>31;
 assert(fn==1);c->reads+=!w;
 if(w && addr==0x34){
  c->txs++;if(c->fail_tx==c->txs)return 24;
  unsigned size=a04_wmt_le16(p);assert(size<=n && !(arg&(1u<<26)));
  for(unsigned i=size;i<n;i++)assert(!p[i]);
  if(p[5]==0xa0 && a04_wmt_le16(p+2)==0xc000){
   assert(c->fw && c->section_index<c->fw->count);
   const struct a04_wlan_section *s=&c->fw->section[c->section_index];
   assert(size>=9 && size<=2056 && size-8<=s->length-c->section_offset);
   assert(!memcmp(p+8,s->data+c->section_offset,size-8));
   c->section_offset+=size-8;c->pda++;
  }else if(p[5]==0xa0 && p[4]==1 && c->fw){
   assert(size==20 && a04_wmt_le16(p+2)==0x8000);
   if(c->configs){assert(c->section_offset==c->fw->section[c->section_index].length);c->section_index++;}
   assert(c->fw && c->section_index<c->fw->count);c->section_offset=0;
   assert(a04_wmt_get32(p+8)==c->fw->section[c->section_index].address);
   assert(a04_wmt_get32(p+12)==c->fw->section[c->section_index].length);
   assert(a04_wmt_get32(p+16)==(0x80000001u|(c->configs?0:8)));
   c->seq=p[7];c->configs++;c->rx_size=12;c->cpu++;c->ffa++;
  }else if(p[5]==0xa0 && p[4]==2 && c->fw){
   assert(size==16 && c->section_offset==c->fw->section[c->section_index].length);
   assert(!a04_wmt_get32(p+8) && !a04_wmt_get32(p+12));c->start=1;c->ac++;c->ffa++;
  }
  return 0;
 }
 if(!w && (addr==0x50 || addr==0x54)){
  assert(n>=16 && c->rx_size);memset(p,0,n);a04_wmt_put16(p,c->rx_size);a04_wmt_put16(p+2,0xe000);
  p[4]=1;p[5]=(uint8_t)(c->seq+c->corrupt_seq);p[8]=(uint8_t)c->status;c->rx_size=0;return 0;
 }
 assert(n==4 && (arg&(1u<<26)));uint32_t v=0;
 if(!w){
  if(addr==0){v=0x16630;if(c->start && !c->ready_timeout && ++c->ready_polls>=3)v|=1u<<21;}
  else if(addr==4)v=c->own&&!c->ownership_timeout?0x100:0;
  else if(addr==0x90)v=c->rx_size;
  else if(addr==0x130 && !c->no_credit){v=c->ac;c->ac=0;}
  else if(addr==0x14c && !c->no_credit){v=c->ffa|(c->cpu<<16);c->ffa=c->cpu=0;}
  else assert(addr==0xc || (addr>=0x130 && addr<=0x14c));
  a04_wmt_put32(p,v);
 }else assert(addr==0xc);
 return 0;
}
static inline void setup(struct card *c,struct a04_sdio_io *io,struct a04_sdio_transport *t){
 memset(c,0,sizeof(*c));memset(io,0,sizeof(*io));memset(t,0,sizeof(*t));
 c->enable=4;io->ctx=c;io->command=command;io->delay=delay;
 t->io=io;t->transfer=transfer;t->functions=2;t->enabled=4;
}
static inline void make_fw(uint8_t *b,unsigned n){
 assert(n>=64);memset(b,0,n);a04_wmt_put32(b,0x574b544d);a04_wmt_put32(b+8,2);
 a04_wmt_put32(b+16,48);a04_wmt_put32(b+24,n-56);a04_wmt_put32(b+28,0x91400);
 a04_wmt_put32(b+32,n-8);a04_wmt_put32(b+40,8);a04_wmt_put32(b+44,0x209c000);
 for(unsigned i=48;i<n;i++)b[i]=(uint8_t)(i*37);
 a04_wmt_put32(b+4,a04_wmt_firmware_crc32(b+8,n-8));
}
'''

class WLANHost(unittest.TestCase):
    run_c = codecs.WMTProtocol.run_c

    def test_download_packets_ack_ready_and_credit_transition(self):
        self.run_c(FAKE + r'''
int main(void){
 uint8_t blob[4800];make_fw(blob,sizeof(blob));struct a04_wlan_firmware fw;
 assert(!a04_wlan_firmware_parse(blob,sizeof(blob),&fw));
 assert(fw.count==2 && fw.section[0].length==4744);
 struct card c;struct a04_sdio_io io;struct a04_sdio_transport t;struct a04_wlan_host h;
 setup(&c,&io,&t);assert(!a04_wlan_host_open(&h,&t));assert(c.enable==6 && h.initialized);
 c.fw=&fw;struct a04_wlan_download_state s;memset(&s,0,sizeof(s));assert(!a04_wlan_download(&h,&fw,&s));
 assert(s.configured==2 && s.packets==4 && s.started && s.ready && h.ready && c.txs==7);
 assert(!a04_wlan_runtime_begin(&h) && h.runtime && h.cpu_free==26 && h.tc0_free==13);
 assert(!h.cpu_used && !h.tc0_used && !h.ffa);
 unsigned txs=c.txs;assert(a04_wlan_download(&h,&fw,&s)==A04_WLAN_STATE && c.txs==txs);
 assert(a04_wlan_runtime_begin(&h)==A04_WLAN_STATE);
 return 0;
}
''')

    def test_fault_poison_no_retry_and_bounded_waits(self):
        self.run_c(FAKE + r'''
int main(void){
 uint8_t blob[4800];make_fw(blob,sizeof(blob));struct a04_wlan_firmware fw;
 assert(!a04_wlan_firmware_parse(blob,sizeof(blob),&fw));
 for(unsigned fault=0;fault<5;fault++){
  struct card c;struct a04_sdio_io io;struct a04_sdio_transport t;struct a04_wlan_host h;
  setup(&c,&io,&t);assert(!a04_wlan_host_open(&h,&t));c.fw=&fw;
  if(fault==0)c.corrupt_seq=1;
  if(fault==1)c.status=1;
  if(fault==2)c.fail_tx=3;
  if(fault==3)c.ready_timeout=1;
  struct a04_wlan_download_state s;memset(&s,0,sizeof(s));
  if(fault<4){assert(a04_wlan_download(&h,&fw,&s));assert(h.failed && t.failed && s.failed);
   unsigned tx=c.txs;assert(a04_wlan_download(&h,&fw,&s)==A04_WLAN_STATE && tx==c.txs);
   assert(a04_wlan_send(&h,16,1)==A04_WLAN_STATE);
   assert(c.reads<30000);
  }else{assert(!a04_wlan_download(&h,&fw,&s));c.no_credit=1;
   assert(a04_wlan_runtime_begin(&h)==A04_WLAN_TIMEOUT && t.failed && c.reads<2000);}
 }
 struct card c;struct a04_sdio_io io;struct a04_sdio_transport t;struct a04_wlan_host h;
 setup(&c,&io,&t);c.ownership_timeout=1;
 assert(a04_wlan_host_open(&h,&t)==A04_WLAN_TIMEOUT && c.reads==257 && !c.txs);
 setup(&c,&io,&t);assert(!a04_wlan_host_open(&h,&t));c.rx_size=4097;
 assert(a04_wlan_receive(&h,0,1)==A04_WLAN_FORMAT && t.failed);
 setup(&c,&io,&t);assert(!a04_wlan_host_open(&h,&t));c.cpu=1;c.ffa=1;
 assert(a04_wlan_resources(&h)==A04_WLAN_CREDIT && t.failed);
 setup(&c,&io,&t);assert(!a04_wlan_host_open(&h,&t));h.cpu_free=0;h.cpu_used=1;c.cpu=1;
 assert(!a04_wlan_resources(&h) && h.cpu_free==0 && h.cpu_done==1);c.ffa=1;
 assert(!a04_wlan_resources(&h) && h.cpu_free==1 && !h.cpu_used);
 return 0;
}
''')

    def test_firmware_rejects_crc_overlap_wrap_truncation(self):
        self.run_c(FAKE + r'''
int main(void){
 uint8_t b[100];struct a04_wlan_firmware fw;
 for(unsigned n=0;n<32;n++){memset(b,0,sizeof(b));assert(a04_wlan_firmware_parse(b,n,&fw));}
 for(unsigned fault=0;fault<8;fault++){
  make_fw(b,sizeof(b));
  if(!fault)b[99]^=1;
  if(fault==1)a04_wmt_put32(b+32,48);
  if(fault==2)a04_wmt_put32(b+44,0x91400);
  if(fault==3)a04_wmt_put32(b+28,0xfffffff0);
  if(fault==4)a04_wmt_put32(b+24,101);
  if(fault==5)a04_wmt_put32(b+8,17);
  if(fault==6)a04_wmt_put32(b+20,1);
  if(fault==7)a04_wmt_put32(b+16,16);
  if(fault)a04_wmt_put32(b+4,a04_wmt_firmware_crc32(b+8,sizeof(b)-8));
  assert(a04_wlan_firmware_parse(b,sizeof(b),&fw) && !fw.count);
 }
 return 0;
}
''')

if __name__ == '__main__':
    unittest.main()
