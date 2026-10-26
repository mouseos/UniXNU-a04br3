/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * MT6630 gen3 encrypted MTKW divided download. wlan_lib.{h,c}, config.h.
 * Source fallback from corrupt header to raw image is deliberately refused.
 * Input must be an explicitly owned immutable blob; no firmware embedded here.
 */
#ifndef A04BR3_WLAN_FIRMWARE_H
#define A04BR3_WLAN_FIRMWARE_H
#include "A04BR3WLANHost.h"
#include "A04BR3WMTFirmware.h"
struct a04_wlan_section {const uint8_t *data;uint32_t address;unsigned length;};
struct a04_wlan_firmware {unsigned count,candidate;struct a04_wlan_section section[16];};
static inline int a04_wlan_firmware_parse(const void *data,unsigned length,struct a04_wlan_firmware *fw){
 if(!data || !fw)return A04_WLAN_ARGUMENT;
 fw->count=fw->candidate=0;const uint8_t *b=(const uint8_t *)data;
 if(length<32 || length>16*1024*1024)return A04_WLAN_FORMAT;
 if(a04_wmt_get32(b)!=0x574b544d || a04_wmt_get32(b+12))return A04_WLAN_FORMAT;
 if(a04_wmt_firmware_crc32(b+8,length-8)!=a04_wmt_get32(b+4))return A04_WLAN_CRC;
 unsigned n=a04_wmt_get32(b+8);if(!n || n>16 || length<16+n*16)return A04_WLAN_FORMAT;
 for(unsigned i=0;i<n;i++){
  const uint8_t *s=b+16+16*i;unsigned off=a04_wmt_get32(s),len=a04_wmt_get32(s+8);uint32_t addr=a04_wmt_get32(s+12);
  if(a04_wmt_get32(s+4) || off<16+n*16 || off>length || !len || len>length-off || len-1>0xffffffffu-addr)return A04_WLAN_FORMAT;
  for(unsigned j=0;j<i;j++){
   unsigned po=(unsigned)(fw->section[j].data-b),pn=fw->section[j].length;
   uint32_t pa=fw->section[j].address;
   if((off<po+pn && po<off+len) || ((uint64_t)addr<(uint64_t)pa+pn && (uint64_t)pa<(uint64_t)addr+len))return A04_WLAN_FORMAT;
  }
  fw->section[i].data=b+off;fw->section[i].address=addr;fw->section[i].length=len;
 }
 fw->count=n;return 0;
}
/* Explicit stock MT6630 candidate selection, not a guessed E2/E3 fallback.
 * gl_kal.c uses _MT6630 as an E3 name-table candidate. This one board blob
 * is pinned by SHA256 supplied by the owned-copy caller, after real WMT ID.
 * Format alone is NOT proof of chip compatibility. Other revisions fail shut.
 */
static inline int a04_wlan_firmware_select(const struct a04_wmt_identity *id,
 const void *bytes,unsigned length,const uint8_t digest[32],struct a04_wlan_firmware *fw){
 /* Candidate1 reference/staging stock; candidate2 explicit proprietary/vendor.
  * Both are separately pinned: same layout does not imply identical bytes. */
 static const uint8_t expected[2][32]={
  {0xf2,0x8e,0x4c,0x9f,0x03,0x82,0x1c,0x1e,0xc6,0x72,0x9b,0x3b,0x9b,0x9c,0x05,0xbf,0x82,0x57,0x5b,0x5e,0xd5,0x8c,0xce,0x44,0x7e,0x65,0x35,0x06,0xf6,0xbd,0xdd,0x45},
  {0x07,0xfe,0x1a,0x22,0x5e,0x6b,0x9d,0xe6,0xb3,0x10,0x89,0x4d,0xb3,0xb4,0x0b,0xcc,0x1c,0xb6,0x61,0x07,0xb9,0x13,0xe4,0xc4,0x3e,0xb6,0xa6,0x20,0x89,0xaa,0xda,0x22}
 };
 if(!fw)return A04_WLAN_ARGUMENT;
 fw->count=fw->candidate=0;
 if(!id || !digest || id->chip!=0x6630 || id->hardware!=0x8a12 || id->rom!=0x8a11 || length!=237008)return A04_WLAN_UNSUPPORTED;
 unsigned candidate=0;
 for(unsigned c=0;c<2;c++){
  unsigned mismatch=0;for(unsigned i=0;i<32;i++)mismatch|=digest[i]^expected[c][i];
  if(!mismatch){candidate=c+1;break;}
 }
 if(!candidate)return A04_WLAN_CRC;
 int e=a04_wlan_firmware_parse(bytes,length,fw);if(!e)fw->candidate=candidate;
 return e;
}

struct a04_wlan_download_state {unsigned attempted,section,offset,packets,configured,started,ready;int failed;};
static inline int a04_wlan_download(struct a04_wlan_host *h,const struct a04_wlan_firmware *fw,struct a04_wlan_download_state *s){
 if(!h || !fw || !s || !h->initialized || !fw->count || fw->count>2 || s->attempted)return A04_WLAN_STATE;
 if(h->wcir&(1u<<21))return A04_WLAN_STATE; /* never reset an already-running image */
 for(unsigned i=0;i<fw->count;i++)if(!fw->section[i].data || !fw->section[i].length || fw->section[i].length>16*1024*1024 || fw->section[i].length-1>0xffffffffu-fw->section[i].address)return A04_WLAN_ARGUMENT;
 s->attempted=1;
 int e=0;
 for(unsigned i=0;i<fw->count;i++){
  s->section=i+1;s->offset=0;
  uint8_t config[12];a04_wmt_put32(config,fw->section[i].address);a04_wmt_put32(config+4,fw->section[i].length);
  /* CFG_ENABLE_FW_DOWNLOAD_ACK1 / ENCRYPTION1; first section RESET bit3. */
  a04_wmt_put32(config+8,0x80000001u|(i?0:8));unsigned seq=a04_wlan_sequence(h);
  e=a04_wlan_command(h,1,seq,0xa0,config,12);if(e)goto fail;
  e=a04_wlan_send(h,20,1);if(e)goto fail;
  e=a04_wlan_init_result(h,1,seq);if(e)goto fail;
  s->configured++;
  while(s->offset<fw->section[i].length){
   unsigned n=fw->section[i].length-s->offset;if(n>2048)n=2048;
   a04_wmt_put16(h->tx,n+8);a04_wmt_put16(h->tx+2,0xc000);h->tx[4]=h->tx[6]=h->tx[7]=0;h->tx[5]=0xa0;
   for(unsigned k=0;k<n;k++)h->tx[8+k]=fw->section[i].data[s->offset+k];
   e=a04_wlan_send(h,n+8,2);if(e)goto fail;
   s->offset+=n;s->packets++;
  }
 }
 {uint8_t start[8]={0};unsigned seq=a04_wlan_sequence(h);
  e=a04_wlan_command(h,2,seq,0xa0,start,8);if(e)goto fail;
  e=a04_wlan_send(h,16,0);if(e)goto fail;s->started=1;
 }
 for(unsigned i=0;i<106;i++){
  e=a04_wlan_read(h,0,&h->wcir);if(e)goto fail;
  if(h->wcir&(1u<<21)){s->ready=h->ready=1;return 0;}
  h->transport->io->delay(h->transport->io->ctx,10000);
 }
 e=A04_WLAN_TIMEOUT;
fail:s->failed=e;return a04_wlan_fail(h,e);
}
#endif
