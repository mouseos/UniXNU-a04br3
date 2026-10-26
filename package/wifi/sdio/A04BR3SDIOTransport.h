/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Synchronous, single-caller SDIO transport. Caller serializes all commands.
 * No automatic retries: a failed data transfer may already have consumed FIFO
 * bytes. Reinitialize the card/host and this object before using it again.
 */
#ifndef A04BR3_SDIO_TRANSPORT_H
#define A04BR3_SDIO_TRANSPORT_H
#include "A04BR3SDIOProtocol.h"
enum { A04_SDIO_OK=0, A04_SDIO_ARGUMENT=20, A04_SDIO_COMMAND=21,
 A04_SDIO_R5=22, A04_SDIO_TIMEOUT=23, A04_SDIO_DATA=24,
 A04_SDIO_POISONED=25, A04_SDIO_CHIP=26 };
struct a04_sdio_transport {
 const struct a04_sdio_io *io;
 int (*transfer)(void *,uint32_t,void *,unsigned); /* one byte/block-mode CMD53 */
 unsigned functions;
 unsigned enabled;
 int failed;
};
static inline int a04_sdio_direct(const struct a04_sdio_io *io,unsigned fn,
 unsigned address,int write,uint8_t *value)
{
 uint32_t r=0;
 if(fn>7 || address>0x1ffff || !value)return A04_SDIO_ARGUMENT;
 uint32_t a=(write?0x80000000u:0)|(fn<<28)|(address<<9)|(write?*value:0);
 if(io->command(io->ctx,52,a,1,&r))return A04_SDIO_COMMAND;
 if(r&0xcb00)return A04_SDIO_R5;
 if(!write)*value=(uint8_t)r;
 return 0;
}
static inline int a04_sdio_enable(struct a04_sdio_transport *t,unsigned fn)
{
 uint8_t v=0;int e;
 if(t->failed)return A04_SDIO_POISONED;
 if(!fn || fn>7 || fn>t->functions)return A04_SDIO_ARGUMENT;
 if((e=a04_sdio_direct(t->io,0,2,0,&v)))return e;
 v|=1u<<fn;
 if((e=a04_sdio_direct(t->io,0,2,1,&v)))return e;
 for(unsigned n=0;n<100;n++){
  if((e=a04_sdio_direct(t->io,0,3,0,&v)))return e;
  if(v&(1u<<fn)){
   /* FBR block size, board STP uses 512. Byte-mode works without block mode. */
   v=0;if((e=a04_sdio_direct(t->io,0,(fn<<8)+0x10,1,&v)))return e;
   v=2;if((e=a04_sdio_direct(t->io,0,(fn<<8)+0x11,1,&v)))return e;
   t->enabled|=1u<<fn;return 0;
  }
  if(n!=99)t->io->delay(t->io->ctx,1000);
 }
 return A04_SDIO_TIMEOUT;
}
/* Preserve a padded STP packet in one CMD53: fixed-address FIFO transfers
 * above 512 bytes must be block aligned, as in the board STP implementation.
 * Incrementing memory/register transfers can safely use byte-mode chunks.
 * SDIO byte count 512 is encoded as zero; block count is always explicit.
 */
static inline int a04_sdio_transfer(struct a04_sdio_transport *t,unsigned fn,
 unsigned address,int write,int increment,void *buffer,unsigned length)
{
 if(t->failed)return A04_SDIO_POISONED;
 if(!fn || fn>7 || !(t->enabled&(1u<<fn)) || address>0x1ffff ||
    !buffer || !length || length>65536 || (!increment && length>512 && (length&511)) ||
    (increment && length>0x20000-address))return A04_SDIO_ARGUMENT;
 uint8_t *p=(uint8_t *)buffer;
 while(length){
  unsigned block=(!increment && length>512);
  unsigned n=block?length:(length>512?512:length);
  unsigned count=block?n/512:(n&511);
  uint32_t a=(write?0x80000000u:0)|(fn<<28)|(increment?1u<<26:0)|(address<<9)|(block?1u<<27:0)|count;
  int e=t->transfer(t->io->ctx,a,p,n);
  if(e){t->failed=e;return e;}
  length-=n;p+=n;if(increment)address+=n;
 }
 return 0;
}
static inline int a04_sdio_read32(struct a04_sdio_transport *t,unsigned fn,unsigned address,uint32_t *v)
{
 if(!v)return A04_SDIO_ARGUMENT;
 uint8_t b[4];int e=a04_sdio_transfer(t,fn,address,0,1,b,4);
 if(!e)*v=(uint32_t)b[0]|((uint32_t)b[1]<<8)|((uint32_t)b[2]<<16)|((uint32_t)b[3]<<24);
 return e;
}
static inline int a04_sdio_write32(struct a04_sdio_transport *t,unsigned fn,unsigned address,uint32_t v)
{
 uint8_t b[4]={(uint8_t)v,(uint8_t)(v>>8),(uint8_t)(v>>16),(uint8_t)(v>>24)};
 return a04_sdio_transfer(t,fn,address,1,1,b,4);
}
struct a04_sdio_wmt_state {uint32_t ccir,chlpcr_before,chlpcr_after;unsigned polls;};
static inline int a04_sdio_wmt_prepare(struct a04_sdio_transport *t,struct a04_sdio_wmt_state *s)
{
 int e=a04_sdio_enable(t,2);if(e)return e;
 if((e=a04_sdio_read32(t,2,0,&s->ccir)))return e;
 if((s->ccir&0xffff)!=0x6630)return A04_SDIO_CHIP;
 if((e=a04_sdio_read32(t,2,4,&s->chlpcr_before)))return e;
 /* COHEC_00006052: use CMD52, never CMD53 write for CHLPCR. Disable common
  * interrupt output while we use polling; preserve firmware ownership state. */
 uint8_t v=2;
 if((e=a04_sdio_direct(t->io,2,4,1,&v)))return e;
 v=2;if((e=a04_sdio_direct(t->io,2,5,1,&v)))return e;
 for(unsigned n=0;n<100;n++){
  s->polls=n+1;
  if((e=a04_sdio_read32(t,2,4,&s->chlpcr_after)))return e;
  if(s->chlpcr_after&0x100)return 0;
  if(n!=99)t->io->delay(t->io->ctx,1000);
 }
 return A04_SDIO_TIMEOUT;
}
#endif
