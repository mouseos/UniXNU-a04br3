/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * MT6630 fn1 polled HIF. Source: gen3 mt6630_reg.h, nic_tx.c, nic_rx.c.
 * Caller serializes against WMT using the same SDIO host lock. No retry/reset.
 */
#ifndef A04BR3_WLAN_HOST_H
#define A04BR3_WLAN_HOST_H
#include "A04BR3WMTRoundtrip.h"
enum { A04_WLAN_ARGUMENT=80,A04_WLAN_FORMAT=81,A04_WLAN_CRC=82,
 A04_WLAN_TIMEOUT=83,A04_WLAN_STATUS=84,A04_WLAN_SEQUENCE=85,
 A04_WLAN_CREDIT=86,A04_WLAN_STATE=87,A04_WLAN_UNSUPPORTED=88, A04_WLAN_CMD_PACKET=0xa0 };
struct a04_wlan_host {
 struct a04_sdio_transport *transport;
 unsigned initialized,ready,sequence,cpu_free,cpu_used,cpu_done,ffa,tc0_free,tc0_used,tc0_done,runtime;
 uint32_t wcir,status,chlpcr;unsigned own_polls;int failed;
 uint32_t wasr;int wasr_error;
 unsigned last_cid,last_cmd_sequence,last_cmd_type,last_cmd_set,last_cmd_bytes;
 uint8_t tx[4608],rx[4608];unsigned rx_length,rx_port;
};
static inline int a04_wlan_fail(struct a04_wlan_host *h,int e){h->failed=e;h->transport->failed=e;return e;}
static inline int a04_wlan_read(struct a04_wlan_host *h,unsigned reg,uint32_t *v){
 if(h->failed || h->transport->failed)return A04_WLAN_STATE;
 int e=a04_sdio_read32(h->transport,1,reg,v);return e?a04_wlan_fail(h,e):0;
}
static inline int a04_wlan_write(struct a04_wlan_host *h,unsigned reg,uint32_t v){
 if(h->failed || h->transport->failed)return A04_WLAN_STATE;
 int e=a04_sdio_write32(h->transport,1,reg,v);return e?a04_wlan_fail(h,e):0;
}
static inline int a04_wlan_host_open(struct a04_wlan_host *h,struct a04_sdio_transport *t){
 if(!h || !t || t->failed)return A04_WLAN_ARGUMENT;
 h->wasr=0;h->wasr_error=0;h->status=0;
 h->last_cid=h->last_cmd_sequence=h->last_cmd_type=h->last_cmd_set=h->last_cmd_bytes=0;
 h->transport=t;h->initialized=h->ready=h->sequence=0;h->failed=0;
 h->cpu_free=h->tc0_free=104;h->cpu_used=h->cpu_done=h->ffa=h->tc0_used=h->tc0_done=h->runtime=0;h->rx_length=h->own_polls=0;h->chlpcr=0;
 int e=a04_sdio_enable(t,1);if(e)return a04_wlan_fail(h,e);
 if((e=a04_wlan_read(h,0,&h->wcir)))return e;
 if((h->wcir&0xffff)!=0x6630)return a04_wlan_fail(h,A04_WLAN_FORMAT);
 /* HAL_LP_OWN_CLR_OK: use CMD52 high byte to preserve combo workaround. */
 uint8_t v=2;if((e=a04_sdio_direct(t->io,1,5,1,&v)))return a04_wlan_fail(h,e);
 unsigned n;uint32_t own=0;
 for(n=0;n<256;n++){
  if((e=a04_wlan_read(h,4,&own)))return e;
  h->chlpcr=own;h->own_polls=n+1;
  if(own&0x100)break;
  t->io->delay(t->io->ctx,8);
 }
 if(n==256)return a04_wlan_fail(h,A04_WLAN_TIMEOUT);
 v=2;if((e=a04_sdio_direct(t->io,1,4,1,&v)))return a04_wlan_fail(h,e);
 uint32_t control=0;
 if((e=a04_wlan_read(h,0xc,&control)))return e;
 /* Polled, one RX packet at a time. WHISR read-clear, no enhanced aggregation. */
 if((e=a04_wlan_write(h,0xc,control&~((1u<<16)|(1u<<1)))))return e;
 h->initialized=1;return 0;
}
/* Native hal.h HAL_GET_ABNORMAL_INTERRUPT_REASON_CODE reads WASR0x20.
 * Preserve WHISR and read one reason BEFORE poison disables further I/O. */
static inline int a04_wlan_interrupt_status(struct a04_wlan_host *h,uint32_t *status){
 int e=a04_wlan_read(h,0x10,status);if(e)return e;h->status=*status;
 if(*status&0x80000040u){
  h->wasr_error=a04_wlan_read(h,0x20,&h->wasr);
  return a04_wlan_fail(h,A04_WLAN_STATUS);
 }
 return 0;
}
/* WTQCR read-clear counters: AC0 index0, FFA14, CPU15. Only our two
 * queues are supported. Never mint a credit from a TX-done without FFA. */
static inline int a04_wlan_resources(struct a04_wlan_host *h){
 uint32_t value=0;unsigned ac=0,cpu=0,ffa=0;
 for(unsigned i=0;i<8;i++){
  int e=a04_wlan_read(h,0x130+4*i,&value);if(e)return e;
  if(!i)ac=value&0xffff;
  if(i==7){ffa=value&0xffff;cpu=value>>16;}
  if((i==0 && (value>>16)) || (i>0 && i<7 && value))return a04_wlan_fail(h,A04_WLAN_CREDIT);
 }
 if(ac>h->tc0_used-h->tc0_done || cpu>h->cpu_used-h->cpu_done || ffa>65535-h->ffa)
  return a04_wlan_fail(h,A04_WLAN_CREDIT);
 h->tc0_done+=ac;h->cpu_done+=cpu;h->ffa+=ffa;
 unsigned r=h->tc0_done<h->ffa?h->tc0_done:h->ffa;
 h->tc0_done-=r;h->tc0_used-=r;h->tc0_free+=r;h->ffa-=r;
 r=h->cpu_done<h->ffa?h->cpu_done:h->ffa;
 h->cpu_done-=r;h->cpu_used-=r;h->cpu_free+=r;h->ffa-=r;
 return 0;
}
/* Native nicTxResetResource switches from INIT8 buffers to runtime TC4=2.
 * Drain observed init completions before changing the software budget. This
 * conservative boundary may fail on unobserved hardware; never assume return. */
static inline int a04_wlan_runtime_begin(struct a04_wlan_host *h){
 if(!h || !h->ready || h->runtime || h->failed)return A04_WLAN_STATE;
 for(unsigned n=0;n<100;n++){
  int e=a04_wlan_resources(h);if(e)return e;
  if(!h->cpu_used && !h->tc0_used){
   if(h->ffa)return a04_wlan_fail(h,A04_WLAN_CREDIT);
   h->cpu_free=26;h->tc0_free=13;h->runtime=1;return 0;
  }
  h->transport->io->delay(h->transport->io->ctx,1000);
 }
 return a04_wlan_fail(h,A04_WLAN_TIMEOUT);
}
static inline int a04_wlan_send(struct a04_wlan_host *h,unsigned logical,unsigned tc){
 if(!h || !h->initialized || h->failed || h->transport->failed)return A04_WLAN_STATE;
 if(logical<8 || logical>4096 || tc>2)return A04_WLAN_ARGUMENT;
 unsigned pages=(logical+127)/128;
 if(tc==1){
  unsigned n;for(n=0;n<100;n++){
   if(h->cpu_free>=pages)break;
   int e=a04_wlan_resources(h);if(e)return e;
   if(h->cpu_free>=pages)break;
   h->transport->io->delay(h->transport->io->ctx,1000);
  }
  if(n==100)return a04_wlan_fail(h,A04_WLAN_TIMEOUT);
  h->cpu_free-=pages;h->cpu_used+=pages;
 }else if(tc==0){
  if(h->tc0_free<pages)return a04_wlan_fail(h,A04_WLAN_CREDIT);
  h->tc0_free-=pages;h->tc0_used+=pages;
 }
 /* tc2 is PDA scatter: vendor wlanImageSectionDownload/nicTxInitCmd does
  * not acquire TC resources for scatter packets. Do not invent ACK/credits. */
 unsigned bus=(logical+3)&~3u;if(bus>512)bus=(bus+511)&~511u;
 for(unsigned i=logical;i<bus;i++)h->tx[i]=0;
 if(a04_wmt_le16(h->tx+2)==0x8000 && h->tx[5]==A04_WLAN_CMD_PACKET){
  h->last_cid=h->tx[4];h->last_cmd_type=h->tx[5];h->last_cmd_set=h->tx[6];h->last_cmd_sequence=h->tx[7];h->last_cmd_bytes=logical;
 }
 int e=a04_sdio_transfer(h->transport,1,0x34,1,0,h->tx,bus);
 return e?a04_wlan_fail(h,e):0;
}
static inline int a04_wlan_receive(struct a04_wlan_host *h,unsigned port,unsigned polls){
 if(!h || !h->initialized || port>1 || !polls || polls>20000)return A04_WLAN_ARGUMENT;
 h->rx_length=0;
 for(unsigned i=0;i<polls;i++){
  uint32_t lengths=0;int e=a04_wlan_read(h,0x90,&lengths);if(e)return e;
  unsigned n=(lengths>>(port*16))&0xffff;
  if(n){
   if(n<8 || n>4096)return a04_wlan_fail(h,A04_WLAN_FORMAT);
   /* nicRxWaitResponse: read ALIGN4(packet)+4 appended status, then SDIO
    * block padding. Appended status is never exposed as packet payload. */
   unsigned bus=((n+3)&~3u)+4;if(bus>512)bus=(bus+511)&~511u;
   e=a04_sdio_transfer(h->transport,1,port?0x54:0x50,0,0,h->rx,bus);
   if(e)return a04_wlan_fail(h,e);
   if(a04_wmt_le16(h->rx)!=n)return a04_wlan_fail(h,A04_WLAN_FORMAT);
   h->rx_length=n;h->rx_port=port;return 0;
  }
  h->transport->io->delay(h->transport->io->ctx,50);
 }
 return a04_wlan_fail(h,A04_WLAN_TIMEOUT);
}
static inline unsigned a04_wlan_sequence(struct a04_wlan_host *h){h->sequence=(h->sequence+1)&255;return h->sequence;}
static inline int a04_wlan_command(struct a04_wlan_host *h,unsigned cid,unsigned seq,unsigned type,
 const uint8_t *p,unsigned n){
 if(!h || !h->initialized || h->failed || h->transport->failed)return A04_WLAN_STATE;
 /* nicTxCmd overwrites ucPktTypeID with CMD_PACKET_TYPE_ID=0xa0.
  * WIFI_CMD_T comment saying0x20 is stale; reject it before bus access. */
 if(cid>255 || seq>255 || type!=A04_WLAN_CMD_PACKET || n>2048 || (!p&&n))return A04_WLAN_ARGUMENT;
 a04_wmt_put16(h->tx,n+8);a04_wmt_put16(h->tx+2,0x8000);
 h->tx[4]=(uint8_t)cid;h->tx[5]=(uint8_t)type;h->tx[6]=0;h->tx[7]=(uint8_t)seq;
 for(unsigned i=0;i<n;i++)h->tx[8+i]=p[i];
 return 0;
}
static inline int a04_wlan_init_result(struct a04_wlan_host *h,unsigned eid,unsigned seq){
 int e=a04_wlan_receive(h,0,20000);if(e)return e;
 if(h->rx_length!=12 || a04_wmt_le16(h->rx+2)!=0xe000 || h->rx[4]!=eid || h->rx[5]!=seq)
  return a04_wlan_fail(h,A04_WLAN_SEQUENCE);
 if(h->rx[8])return a04_wlan_fail(h,A04_WLAN_STATUS);
 return 0;
}
#endif
