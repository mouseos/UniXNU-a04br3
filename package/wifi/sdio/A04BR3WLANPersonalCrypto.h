/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * WPA2-Personal / CCMP descriptor-v2 primitives. No supplicant state or I/O.
 * SHA1 and constant-time bitsliced AES are the REAL pinned BearSSL providers
 * already compiled by libkern/conf/files, not g_crypto_funcs dispatch.
 * See A04BR3WLANPersonalCrypto.sources.json for source/specification provenance.
 *
 * All lengths are bytes. Inputs remain caller-owned and stable during a call.
 * Output buffers must have the indicated fixed size/capacity. On error their
 * contents are unchanged; unwrap sets *plain_bytes=0. Unwrap permits in-place
 * input/output, but plain_bytes must not alias any input/output. Other fixed
 * outputs may alias inputs (results are committed only after computation).
 * Caller must wipe PMK/PTK/GTK after use. PBKDF2 is CPU work: never invoke it
 * while holding SDIO/storage/spin locks or on the event-pump critical path.
 * No authentication, association or firmware success is inferred by this API.
 */
#ifndef A04BR3_WLAN_PERSONAL_CRYPTO_H
#define A04BR3_WLAN_PERSONAL_CRYPTO_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include "../bearssl/inc/bearssl_hash.h"
#include "../bearssl/inc/bearssl_block.h"

#define A04_WLAN_PERSONAL_CRYPTO_API 1
#define A04_WLAN_PERSONAL_PMK_BYTES 32u
#define A04_WLAN_PERSONAL_PTK_BYTES 64u /* CCMP consumes KCK16 | KEK16 | TK16 */
#define A04_WLAN_PERSONAL_MAX_EAPOL 2048u
#define A04_WLAN_PERSONAL_MAX_WRAP 2048u

enum a04_wlan_personal_crypto_result {
 A04_WLAN_PERSONAL_OK=0,
 A04_WLAN_PERSONAL_ARGUMENT=1,
 A04_WLAN_PERSONAL_INTEGRITY=2
};
static inline void a04_wlan_personal_clear(void *p, size_t n) {
 volatile uint8_t *q=(volatile uint8_t *)p;
 while(n--) *q++=0;
}
/* Fixed public length, content-independent comparison. */
static inline int a04_wlan_personal_equal(const void *a,const void *b,size_t n) {
 const uint8_t *x=(const uint8_t *)a,*y=(const uint8_t *)b;
 volatile uint32_t different=0;
 if(!x || !y) return 0;
 for(size_t i=0;i<n;i++) different|=(uint32_t)(x[i]^y[i]);
 return different==0;
}
struct a04_wlan_personal_hmac_state { br_sha1_context inner,outer; };
static inline void a04_wlan_personal_hmac_init(struct a04_wlan_personal_hmac_state *s,
 const uint8_t *key,unsigned key_bytes) {
 uint8_t block[64]={0},digest[20];
 if(key_bytes>64) {
  br_sha1_context h;br_sha1_init(&h);br_sha1_update(&h,key,key_bytes);
  br_sha1_out(&h,digest);memcpy(block,digest,20);
  a04_wlan_personal_clear(&h,sizeof(h));
 } else if(key_bytes) memcpy(block,key,key_bytes);
 for(unsigned i=0;i<64;i++) block[i]^=0x36;
 br_sha1_init(&s->inner);br_sha1_update(&s->inner,block,64);
 for(unsigned i=0;i<64;i++) block[i]^=0x36^0x5c;
 br_sha1_init(&s->outer);br_sha1_update(&s->outer,block,64);
 a04_wlan_personal_clear(block,sizeof(block));a04_wlan_personal_clear(digest,sizeof(digest));
}
static inline void a04_wlan_personal_hmac_finish(struct a04_wlan_personal_hmac_state *s,uint8_t out[20]) {
 uint8_t digest[20];br_sha1_out(&s->inner,digest);
 br_sha1_update(&s->outer,digest,20);br_sha1_out(&s->outer,out);
 a04_wlan_personal_clear(digest,sizeof(digest));a04_wlan_personal_clear(s,sizeof(*s));
}
/* Bounded generic HMAC for vectors and callers that already zeroed MIC data. */
static inline int a04_wlan_personal_hmac_sha1(const uint8_t *key,unsigned key_bytes,
 const uint8_t *data,unsigned bytes,uint8_t out[20]) {
 if(!out || (!key && key_bytes) || key_bytes>2048 || (!data && bytes) || bytes>2048)
  return A04_WLAN_PERSONAL_ARGUMENT;
 struct a04_wlan_personal_hmac_state s;a04_wlan_personal_hmac_init(&s,key,key_bytes);
 if(bytes) br_sha1_update(&s.inner,data,bytes);
 a04_wlan_personal_hmac_finish(&s,out);return 0;
}
/* IEEE 802.11 PSK derivation: PBKDF2-HMAC-SHA1(P,SSID,4096,32).
 * Passphrase bytes are 8..63; native text/64-hex PSK parsing is owner's job.
 * SSID is binary (1..32), including embedded NUL. No strlen or normalization. */
static inline int a04_wlan_personal_pmk(const uint8_t *pass,unsigned pass_bytes,
 const uint8_t *ssid,unsigned ssid_bytes,uint8_t pmk[32]) {
 if(!pass || pass_bytes<8 || pass_bytes>63 || !ssid || !ssid_bytes || ssid_bytes>32 || !pmk)
  return A04_WLAN_PERSONAL_ARGUMENT;
 struct a04_wlan_personal_hmac_state seed,s;
 uint8_t u[20],t[20],result[32],counter[4]={0,0,0,0};
 a04_wlan_personal_hmac_init(&seed,pass,pass_bytes);
 for(unsigned block=1;block<=2;block++) {
  counter[3]=(uint8_t)block;s=seed;
  br_sha1_update(&s.inner,ssid,ssid_bytes);br_sha1_update(&s.inner,counter,4);
  a04_wlan_personal_hmac_finish(&s,u);memcpy(t,u,20);
  for(unsigned j=1;j<4096;j++) {
   s=seed;br_sha1_update(&s.inner,u,20);a04_wlan_personal_hmac_finish(&s,u);
   for(unsigned k=0;k<20;k++) t[k]^=u[k];
  }
  memcpy(result+(block-1)*20,t,block==1?20:12);
 }
 memcpy(pmk,result,32);
 a04_wlan_personal_clear(&seed,sizeof(seed));a04_wlan_personal_clear(u,sizeof(u));
 a04_wlan_personal_clear(t,sizeof(t));a04_wlan_personal_clear(result,sizeof(result));
 return 0;
}
/* Lexical order of public addresses/nonces. Not used for secret comparison. */
static inline int a04_wlan_personal_order(const uint8_t *a,const uint8_t *b,unsigned n) {
 for(unsigned i=0;i<n;i++) if(a[i]!=b[i]) return a[i]<b[i];
 return 1;
}
/* WPA PRF-512: HMAC(PMK,label||NUL||sorted addresses||sorted nonces||i).
 * Counter starts at zero; last SHA1 block is truncated to 64 total bytes. */
static inline int a04_wlan_personal_ptk(const uint8_t pmk[32],const uint8_t aa[6],
 const uint8_t spa[6],const uint8_t anonce[32],const uint8_t snonce[32],uint8_t ptk[64]) {
 if(!pmk || !aa || !spa || !anonce || !snonce || !ptk) return A04_WLAN_PERSONAL_ARGUMENT;
 static const uint8_t label[]="Pairwise key expansion";
 uint8_t input[sizeof(label)+76+1],digest[20],result[64];
 const uint8_t *first=a04_wlan_personal_order(aa,spa,6)?aa:spa;
 const uint8_t *second=first==aa?spa:aa;
 memcpy(input,label,sizeof(label));memcpy(input+sizeof(label),first,6);
 memcpy(input+sizeof(label)+6,second,6);
 first=a04_wlan_personal_order(anonce,snonce,32)?anonce:snonce;
 second=first==anonce?snonce:anonce;
 memcpy(input+sizeof(label)+12,first,32);memcpy(input+sizeof(label)+44,second,32);
 for(unsigned i=0;i<4;i++) {
  input[sizeof(input)-1]=(uint8_t)i;
  (void)a04_wlan_personal_hmac_sha1(pmk,32,input,sizeof(input),digest);
  memcpy(result+20*i,digest,i==3?4:20);
 }
 memcpy(ptk,result,64);a04_wlan_personal_clear(input,sizeof(input));
 a04_wlan_personal_clear(digest,sizeof(digest));a04_wlan_personal_clear(result,sizeof(result));return 0;
}
/* Descriptor-v2 EAPOL-Key MIC. Entire supplied packet must equal its declared
 * EAPOL length. Offset81..96 is streamed as zero without modifying the input.
 * Semantic/replay/RSN checks remain mandatory in the 4-way handshake owner. */
static inline int a04_wlan_personal_mic(const uint8_t kck[16],const uint8_t *eapol,
 unsigned bytes,uint8_t mic[16]) {
 if(!kck || !eapol || !mic || bytes<99 || bytes>A04_WLAN_PERSONAL_MAX_EAPOL ||
  eapol[0]<1 || eapol[0]>3 || eapol[1]!=3 || eapol[4]!=2 || (eapol[6]&7)!=2 ||
  (((unsigned)eapol[2]<<8)|eapol[3])!=bytes-4 ||
  (((unsigned)eapol[97]<<8)|eapol[98])!=bytes-99) return A04_WLAN_PERSONAL_ARGUMENT;
 static const uint8_t zero[16]={0};uint8_t digest[20];
 struct a04_wlan_personal_hmac_state s;a04_wlan_personal_hmac_init(&s,kck,16);
 br_sha1_update(&s.inner,eapol,81);br_sha1_update(&s.inner,zero,16);
 br_sha1_update(&s.inner,eapol+97,bytes-97);a04_wlan_personal_hmac_finish(&s,digest);
 memcpy(mic,digest,16);a04_wlan_personal_clear(digest,sizeof(digest));return 0;
}
static inline int a04_wlan_personal_mic_verify(const uint8_t kck[16],const uint8_t *eapol,unsigned bytes) {
 uint8_t mic[16];int error=a04_wlan_personal_mic(kck,eapol,bytes,mic);
 if(error) return error;
 int valid=a04_wlan_personal_equal(mic,eapol+81,16);a04_wlan_personal_clear(mic,sizeof(mic));
 return valid?0:A04_WLAN_PERSONAL_INTEGRITY;
}
/* RFC3394 section2.2.2, AES128 only. No RFC5649 padding, no retry/alternate IV.
 * Authenticated plaintext is released once A equals A6 repeated eight times.
 * Caller parses GTK KDE/padding and enforces negotiated CCMP key length. */
static inline int a04_wlan_personal_unwrap(const uint8_t kek[16],const uint8_t *cipher,
 unsigned cipher_bytes,uint8_t *plain,unsigned capacity,unsigned *plain_bytes) {
 if(!plain_bytes) return A04_WLAN_PERSONAL_ARGUMENT;
 *plain_bytes=0;
 if(!kek || !cipher || !plain || cipher_bytes<24 || cipher_bytes>A04_WLAN_PERSONAL_MAX_WRAP ||
  (cipher_bytes&7) || capacity<cipher_bytes-8) return A04_WLAN_PERSONAL_ARGUMENT;
 br_aes_ct_cbcdec_keys key;uint8_t work[A04_WLAN_PERSONAL_MAX_WRAP],block[16],iv[16];
 br_aes_ct_cbcdec_init(&key,kek,16);memcpy(work,cipher,cipher_bytes);
 unsigned n=cipher_bytes/8-1;
 for(unsigned j=6;j>0;j--) for(unsigned i=n;i>0;i--) {
  uint64_t t=(uint64_t)(j-1)*n+i;
  memcpy(block,work,8);
  for(unsigned k=0;k<8;k++) {block[7-k]^=(uint8_t)t;t>>=8;}
  memcpy(block+8,work+8*i,8);memset(iv,0,sizeof(iv));
  br_aes_ct_cbcdec_run(&key,iv,block,16);
  memcpy(work,block,8);memcpy(work+8*i,block+8,8);
 }
 static const uint8_t initial[8]={0xa6,0xa6,0xa6,0xa6,0xa6,0xa6,0xa6,0xa6};
 int valid=a04_wlan_personal_equal(work,initial,8);
 if(valid) {memcpy(plain,work+8,cipher_bytes-8);*plain_bytes=cipher_bytes-8;}
 a04_wlan_personal_clear(&key,sizeof(key));a04_wlan_personal_clear(work,sizeof(work));
 a04_wlan_personal_clear(block,sizeof(block));a04_wlan_personal_clear(iv,sizeof(iv));
 return valid?0:A04_WLAN_PERSONAL_INTEGRITY;
}
#endif
