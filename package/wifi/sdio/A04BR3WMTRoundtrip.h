/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Polled, serialized WMT SDIO channel. Board stp_sdio.c credit accounting:
 * cold FIFO 2080 bytes, completion count CHISR[6:4], FIFO charge round4,
 * CRDR bus count round512 above512. No retry after a destructive read/write.
 * Caller holds its transport lock; initialize only after cold card prepare.
 */
#ifndef A04BR3_WMT_ROUNDTRIP_H
#define A04BR3_WMT_ROUNDTRIP_H
#include "A04BR3SDIOTransport.h"
#include "A04BR3WMTProtocol.h"
enum { A04_WMT_CHANNEL_TIMEOUT=40, A04_WMT_CHANNEL_CREDIT=41,
 A04_WMT_CHANNEL_STALE=42, A04_WMT_CHANNEL_OWN=43,
 A04_WMT_CHANNEL_DEVICE=44, A04_WMT_CHANNEL_POISON=45,
 A04_WMT_CHANNEL_CODEC=100 };
struct a04_wmt_channel {
 uint8_t tx[2560],rx[2560],event[A04_STP_MAX_PAYLOAD];
 unsigned free_bytes,pending_cost,event_length,expected,polls,rx_length,rx_packets,skipped;
 uint32_t status,chcr;int failed;unsigned initialized;
};
static inline int a04_wmt_channel_fail(struct a04_sdio_transport *t,struct a04_wmt_channel *c,int e){
 c->failed=e;t->failed=e;return e;
}
static inline int a04_wmt_channel_init(struct a04_sdio_transport *t,struct a04_wmt_channel *c){
 if(!t || !c)return A04_SDIO_ARGUMENT;
 if(t->failed)return A04_WMT_CHANNEL_POISON;
 c->initialized=0;c->failed=0;c->free_bytes=2080;c->pending_cost=0;
 c->status=0;c->polls=c->event_length=c->expected=c->rx_length=c->rx_packets=c->skipped=0;c->chcr=0;
 uint32_t own=0;int e=a04_sdio_read32(t,2,4,&own);
 if(e)return a04_wmt_channel_fail(t,c,e);
 if(!(own&0x100))return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_OWN);
 if((e=a04_sdio_read32(t,2,0xc,&c->chcr)))return a04_wmt_channel_fail(t,c,e);
 /* Board enables all normal/abnormal sources in CHIER. Host MSDC INTEN and
  * CCCR IEN remain disabled: only this polling owner reads read-clear CHISR.
  * CHLPCR writes retain COHEC_00006052 CMD52 workaround. */
 if((e=a04_sdio_write32(t,2,0x14,0xfffe)))return a04_wmt_channel_fail(t,c,e);
 uint8_t v=1;
 if((e=a04_sdio_direct(t->io,2,4,1,&v)))return a04_wmt_channel_fail(t,c,e);
 c->initialized=1;return 0;
}
static inline int a04_wmt_channel_status(struct a04_sdio_transport *t,struct a04_wmt_channel *c){
 int e=a04_sdio_read32(t,2,0x10,&c->status);
 if(e)return a04_wmt_channel_fail(t,c,e);
 /* TX retry, FIFO overflow, firmware interrupt/assert: stop rather than
  * interpreting recovery traffic as a successful synchronous response. */
 if(c->status&0xff80)return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_DEVICE);
 unsigned count=(c->status>>4)&7;
 if(count){
  if(count!=1 || !c->pending_cost || c->free_bytes+c->pending_cost!=2080)
   return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_CREDIT);
  c->free_bytes+=c->pending_cost;c->pending_cost=0;
 }
 return 0;
}
/* Returns borrowed event bytes through c, valid until the next exchange.
 * All STP frames in an aggregate are checked before success, even frames after
 * the matching event. One event may span STP frames/SDIO packets. No unsolicited
 * WMT queue or ambiguous duplicate event is silently discarded.
 */
static inline int a04_wmt_channel_exchange(struct a04_sdio_transport *t,struct a04_wmt_channel *c,
 const uint8_t *command,unsigned command_length){
 if(!t || !c || !command)return A04_SDIO_ARGUMENT;
 if(t->failed || c->failed)return A04_WMT_CHANNEL_POISON;
 if(!c->initialized)return A04_SDIO_ARGUMENT;
 struct a04_wmt_sdio_lengths sizes;
 int e=a04_wmt_sdio_encode(command,command_length,c->tx,sizeof(c->tx),&sizes);
 if(e)return A04_WMT_CHANNEL_CODEC+e; /* preflight input errors send nothing */
 if(c->pending_cost || c->free_bytes!=2080)return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_CREDIT);
 c->event_length=c->expected=c->polls=c->rx_length=c->rx_packets=c->skipped=0;
 uint32_t own=0;
 if((e=a04_sdio_read32(t,2,4,&own)))return a04_wmt_channel_fail(t,c,e);
 if(!(own&0x100))return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_OWN);
 /* Reject a pre-existing RX/completion before sending a command. */
 if((e=a04_wmt_channel_status(t,c)))return e;
 if(c->status&2)return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_STALE);
 if(sizes.fifo>c->free_bytes)return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_CREDIT);
 c->free_bytes-=sizes.fifo;c->pending_cost=sizes.fifo;
 if((e=a04_sdio_transfer(t,2,0x18,1,0,c->tx,sizes.bus)))return a04_wmt_channel_fail(t,c,e);
 /* 1 second polling slack; each SDIO transaction has its own finite deadline.
  * At most one aggregate per poll, at most 1000 polls; no unbounded RX loop. */
 for(unsigned n=0;n<1000;n++){
  c->polls=n+1;
  if((e=a04_wmt_channel_status(t,c)))return e;
  if(c->status&2){
   c->rx_length=c->status>>16;
   if(c->rx_length<=4 || c->rx_length>2304)return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_CODEC+(int)A04_WMT_LENGTH);
   unsigned bus=(c->rx_length+3)&~3u;if(bus>512)bus=(bus+511)&~511u;
   if((e=a04_sdio_transfer(t,2,0x1c,0,0,c->rx,bus)))return a04_wmt_channel_fail(t,c,e);
   c->rx_packets++;
   struct a04_wmt_sdio_cursor cursor;struct a04_stp_frame frame;
   if((e=a04_wmt_sdio_open(c->rx,bus,c->rx_length,&cursor)))return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_CODEC+e);
   while((e=a04_wmt_sdio_next(&cursor,&frame))==0){
    if(frame.task==5 || frame.task==6)return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_DEVICE);
    if(frame.task!=A04_STP_WMT){c->skipped++;continue;}
    if(c->expected && c->event_length==c->expected)return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_STALE);
    if(frame.payload.length>sizeof(c->event)-c->event_length)return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_CODEC+(int)A04_WMT_LENGTH);
    for(unsigned i=0;i<frame.payload.length;i++)c->event[c->event_length++]=frame.payload.data[i];
    if(c->event_length>=4){
     c->expected=4+a04_wmt_le16(c->event+2);
     if(c->expected<5 || c->expected>sizeof(c->event) || c->event_length>c->expected)
      return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_CODEC+(int)A04_WMT_LENGTH);
    }
   }
   if(e!=A04_WMT_DONE)return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_CODEC+e);
  }
  if(c->expected && c->event_length==c->expected){
   struct a04_wmt_view reply;
   e=a04_wmt_event(command,command_length,c->event,c->event_length,&reply);
   if(e)return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_CODEC+e);
   if(!c->pending_cost)return 0;
  }
  if(n!=999)t->io->delay(t->io->ctx,1000);
 }
 return a04_wmt_channel_fail(t,c,A04_WMT_CHANNEL_TIMEOUT);
}
static inline uint32_t a04_wmt_get32(const uint8_t *p){
 return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
}
/* wmt_core_reg_rw_raw read op2, one register, value0, GEN_VER_MASK0xffff.
 * Public startup API only permits the three vendor identity registers. */
static inline int a04_wmt_version_command(uint32_t address,uint8_t *command,unsigned capacity){
 if(!command || capacity<20)return A04_SDIO_ARGUMENT;
 if(address!=0x80000000u && address!=0x80000004u && address!=0x80000008u)return A04_SDIO_ARGUMENT;
 const uint8_t prefix[8]={1,8,16,0,2,1,0,1};
 for(unsigned i=0;i<8;i++)command[i]=prefix[i];
 a04_wmt_put32(command+8,address);a04_wmt_put32(command+12,0);a04_wmt_put32(command+16,0xffff);return 0;
}
static inline int a04_wmt_version_reply(const uint8_t *command,const uint8_t *event,unsigned length,uint32_t *value){
 if(!command || !event || !value)return A04_SDIO_ARGUMENT;
 struct a04_wmt_view reply;int e=a04_wmt_event(command,20,event,length,&reply);
 if(e)return A04_WMT_CHANNEL_CODEC+e;
 /* Board RD_EVT template has stale length4 although sizeof=16 and address/
  * value at8/12. Require the actual wire length12; do not normalize bad bytes. */
 if(reply.length!=11 || reply.data[0] || reply.data[1] || reply.data[2]!=1 ||
    a04_wmt_get32(event+8)!=a04_wmt_get32(command+8))return A04_WMT_CHANNEL_CODEC+(int)A04_WMT_MISMATCH;
 *value=a04_wmt_get32(event+12);return 0;
}
static inline int a04_wmt_read_version(struct a04_sdio_transport *t,struct a04_wmt_channel *c,uint32_t address,uint32_t *value){
 if(!value)return A04_SDIO_ARGUMENT;
 uint8_t command[20];int e=a04_wmt_version_command(address,command,sizeof(command));if(e)return e;
 if((e=a04_wmt_channel_exchange(t,c,command,sizeof(command))))return e;
 if((e=a04_wmt_version_reply(command,c->event,c->event_length,value)))return a04_wmt_channel_fail(t,c,e);
 return 0;
}
#endif
