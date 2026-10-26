/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * MT8167 MSDC PIO engine; register offsets/command fields from board sd.c and
 * msdc_reg.h. Only the mapped MSDC2 instance is supplied by the caller.
 */
#ifndef A04BR3_SDIO_PIO_H
#define A04BR3_SDIO_PIO_H
#include "A04BR3SDIOTransport.h"
struct a04_sdio_regs {
 void *ctx;
 uint32_t (*read)(void *,unsigned,unsigned); /* offset, access bytes (1/4) */
 void (*write)(void *,unsigned,uint32_t,unsigned);
 void (*delay)(void *,unsigned);
};
static inline int a04_sdio_pio(const struct a04_sdio_regs *r,uint32_t arg,void *buffer,unsigned length)
{
 unsigned block=(arg>>27)&1;
 unsigned blocks=block?(arg&511):1,blksz=block?512:length;
 if(!buffer || !length || length>65536 || !blocks ||
    (block?(length!=blocks*512):(length>512 || (arg&511)!=(length&511))))return A04_SDIO_ARGUMENT;
 unsigned n;
 for(n=0;n<10000;n++){
  if(!(r->read(r->ctx,0x3c,4)&3))break;
  r->delay(r->ctx,10);
 }
 if(n==10000)return A04_SDIO_TIMEOUT;
 r->write(r->ctx,0x14,1u<<31,4);
 for(n=0;n<10000;n++){
  if(!(r->read(r->ctx,0x14,4)&(1u<<31)))break;
  r->delay(r->ctx,10);
 }
 if(n==10000)return A04_SDIO_TIMEOUT;
 r->write(r->ctx,0x0c,0xd700,4); /* W1C only command/data status we own. */
 r->write(r->ctx,0x50,blocks,4);
 /* Board CMD53 end-bit counter workaround, also on the MSDC2 instance. */
 uint32_t cfg=r->read(r->ctx,0x208,4);
 unsigned endbit=(!(arg&0x80000000u)&&blksz>256)?blksz+17:273;
 r->write(r->ctx,0x208,(cfg&~(0x3ffu<<11))|(endbit<<11),4);
 r->write(r->ctx,0x38,arg,4);
 r->write(r->ctx,0x34,53|(1u<<7)|((blocks>1?2u:1u)<<11)|((arg>>31)<<13)|(blksz<<16),4);
 uint8_t *p=(uint8_t *)buffer;unsigned done=0;int cmdready=0,complete=0;
 /* At the initial ~400kHz bus clock, allow bounded wire time as well as
  * 100ms command slack (40us/byte, including FIFO polling overhead). */
 unsigned budget=10000+length*4;
 for(n=0;n<budget;n++){
  uint32_t st=r->read(r->ctx,0x0c,4);
  if(st&0x600)return A04_SDIO_COMMAND;
  if(st&0xc000)return A04_SDIO_DATA;
  if(st&0x100){
   r->write(r->ctx,0x0c,0x100,4);
   if(r->read(r->ctx,0x40,4)&0xcb00)return A04_SDIO_R5;
   cmdready=1;
  }
  if(st&0x1000){r->write(r->ctx,0x0c,0x1000,4);complete=1;}
  if(complete && (arg&0x80000000u) && done<length)return A04_SDIO_DATA;
  if(cmdready && done<length){
   uint32_t fifo=r->read(r->ctx,0x14,4);
   unsigned available=(arg&0x80000000u)?(((fifo>>16)&255)?0:128):(fifo&255);
   unsigned count=length-done;if(count>available)count=available;
   while(count){
    unsigned width=count>=4?4:1;uint32_t v=0;
    if(arg&0x80000000u){
     for(unsigned k=0;k<width;k++)v|=(uint32_t)p[done+k]<<(k*8);
     r->write(r->ctx,0x18,v,width);
    }else{
     v=r->read(r->ctx,0x1c,width);
     for(unsigned k=0;k<width;k++)p[done+k]=(uint8_t)(v>>(k*8));
    }
    done+=width;count-=width;
   }
  }
  /* Host completion and full byte count are both required. RX may complete
   * before software drains the final FIFO; TX must never complete short. */
  if(cmdready && complete && done==length)return 0;
  r->delay(r->ctx,10);
 }
 return A04_SDIO_TIMEOUT;
}
#endif
