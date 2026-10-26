/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * SDIO identification and common command callbacks.
 */
#ifndef A04BR3_SDIO_PROTOCOL_H
#define A04BR3_SDIO_PROTOCOL_H
struct a04_sdio_io {
 void *ctx;
 int (*command)(void *,unsigned,uint32_t,unsigned,uint32_t *);
 void (*delay)(void *,unsigned);
};
struct a04_sdio_card {uint32_t ocr,rca;uint16_t vendor,device;uint8_t revision,functions;};
static inline int a04_sdio_read(const struct a04_sdio_io *io,uint32_t address,uint8_t *value)
{
 uint32_t response=0;
 if(address>0x1ffff || io->command(io->ctx,52,address<<9,1,&response))return 1;
 if(response&0xcb00)return 1;
 *value=(uint8_t)response;return 0;
}
static inline int a04_sdio_identify(const struct a04_sdio_io *io,struct a04_sdio_card *card)
{
 uint32_t r=0;uint8_t v=0;
 if(io->command(io->ctx,0,0,0,&r))return 1;
 if(io->command(io->ctx,5,0,3,&r))return 2;
 card->functions=(r>>28)&7;
 uint32_t voltage=r&0x00ff8000;
 if(!voltage || !card->functions)return 3;
 for(unsigned n=0;n<100;n++){
  if(io->command(io->ctx,5,voltage,3,&r))return 4;
  if(r&0x80000000)break;
  if(n==99)return 5;
  io->delay(io->ctx,10000);
 }
 card->ocr=r;
 if(io->command(io->ctx,3,0,1,&r) || (r&0xe000) || !(r&0xffff0000))return 6;
 card->rca=r&0xffff0000;
 if(io->command(io->ctx,7,card->rca,7,&r) || (r&0xfdffa080))return 7;
 if(a04_sdio_read(io,0,&card->revision))return 8;
 uint32_t cis=0;
 for(unsigned n=0;n<3;n++){
  if(a04_sdio_read(io,9+n,&v))return 9;
  cis|=(uint32_t)v<<(8*n);
 }
 if(!cis || cis>0x1ffff)return 10;
 for(unsigned n=0;n<64;n++){
  uint8_t code=0,len=0;
  if(a04_sdio_read(io,cis++,&code))return 11;
  if(code==0xff)return 12;
  if(code==0)continue;
  if(a04_sdio_read(io,cis++,&len) || len>0x20000-cis)return 13;
  if(code==0x20){
   if(len<4)return 14;
   uint8_t id[4];for(unsigned k=0;k<4;k++)if(a04_sdio_read(io,cis+k,&id[k]))return 15;
   card->vendor=id[0]|((uint16_t)id[1]<<8);card->device=id[2]|((uint16_t)id[3]<<8);return 0;
  }
  cis+=len;
 }
 return 16;
}
#endif
