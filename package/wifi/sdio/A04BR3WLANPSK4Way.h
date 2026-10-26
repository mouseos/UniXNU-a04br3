/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Bounded WPA2-Personal/CCMP descriptor-v2 handshake owner. Original code.
 * Wire offsets follow IEEE RSN/AOSP wpa_common.h, not native SET3 key guesses.
 * No I/O, firmware acknowledgements, randomness or cryptography is synthesized.
 * Serialize calls under the real radio owner. Bind to one actual association;
 * teardown must cancel/drain callbacks before clearing/freeing this object.
 */
#ifndef A04BR3_WLAN_PSK4WAY_H
#define A04BR3_WLAN_PSK4WAY_H
#include <stdint.h>
#include "A04BR3WLANSecurity.h"
#define A04_PSK_MAX_KEYDATA 512u
#define A04_PSK_MAX_EAPOL (99u+A04_PSK_MAX_KEYDATA)
struct a04_psk_crypto {
 void *context;
 int (*pmk)(void *,const uint8_t *,unsigned,const uint8_t *,unsigned,uint8_t[32]);
 int (*random)(void *,uint8_t[32]);
 int (*ptk)(void *,const uint8_t[32],const uint8_t[6],const uint8_t[6],const uint8_t[32],const uint8_t[32],uint8_t[64]);
 /* Input MIC field is already zero. Return actual HMAC-SHA1-128. */
 int (*mic)(void *,const uint8_t[16],const uint8_t *,unsigned,uint8_t[16]);
 /* RFC3394 including integrity check; capacity is an input, length an output. */
 int (*unwrap)(void *,const uint8_t[16],const uint8_t *,unsigned,uint8_t *,unsigned,unsigned *);
};
enum { A04_PSK_WAIT_M1=1,A04_PSK_M2_READY,A04_PSK_WAIT_M3,A04_PSK_KEYS,
 A04_PSK_M4_READY,A04_PSK_COMPLETE,A04_PSK_FAILED };
struct a04_psk4way {
 struct a04_psk_crypto crypto;
 unsigned phase,tx_pending,tx_kind,tx_attempts,ptk_state,gtk_state,gtk_id,rsn_bytes,m3_bytes,tx_bytes;
 unsigned m4_confirmed,completion_reported;
 int failed;uint64_t deadline,replay,serial,tx_cookie,key_cookie;
 uint8_t ap[6],sta[6],pmk[32],ptk[64],anonce[32],snonce[32],gtk[16],rsc[8];
 uint8_t rsn[257],m3[A04_PSK_MAX_EAPOL],tx[121];
};
static inline unsigned a04_psk_equal(const uint8_t *a,const uint8_t *b,unsigned n){
 unsigned d=0;for(unsigned i=0;i<n;i++)d|=a[i]^b[i];return !d;
}
static inline unsigned a04_psk_zero(const uint8_t *p,unsigned n){
 unsigned v=0;for(unsigned i=0;i<n;i++)v|=p[i];return !v;
}
static inline void a04_psk_copy(uint8_t *d,const uint8_t *p,unsigned n){for(unsigned i=0;i<n;i++)d[i]=p[i];}
/* PRE-ASSOCIATION worker result. Never retain a passphrase in runtime state.
 * prepare is CPU work: caller runs it before association, outside SDIO/spin/
 * storage locks and pump path. Cancellation must wipe this single-use object.
 */
struct a04_psk_prepared {unsigned ready,ssid_bytes;uint8_t ssid[32],pmk[32];};
static inline void a04_psk_prepared_clear(struct a04_psk_prepared *p){
 if(p)a04_wlan_secret_clear(p,sizeof(*p));
}
static inline int a04_psk_prepare(struct a04_psk_prepared *out,const struct a04_psk_crypto *c,
 const uint8_t *secret,unsigned length,unsigned passphrase,const uint8_t *ssid,unsigned ssid_bytes){
 if(!out)return A04_WLAN_ARGUMENT;
 /* Output must not alias any input. Work is committed only after success. */
 if(out->ready || !secret || !ssid || !ssid_bytes || ssid_bytes>32 || passphrase>1 ||
  (passphrase?(!c || !c->pmk || length<8 || length>63):length!=32))return A04_WLAN_ARGUMENT;
 struct a04_psk_prepared work; a04_wlan_zero((uint8_t *)&work,sizeof(work));
 int e=0;
 if(passphrase)e=c->pmk(c->context,secret,length,ssid,ssid_bytes,work.pmk);
 else a04_psk_copy(work.pmk,secret,32);
 if(!e){work.ssid_bytes=ssid_bytes;a04_psk_copy(work.ssid,ssid,ssid_bytes);work.ready=1;*out=work;}
 a04_wlan_secret_clear(&work,sizeof(work));return e;
}
static inline int a04_psk_fail(struct a04_psk4way *s,int e){
 if(!s)return A04_WLAN_ARGUMENT;
 a04_wlan_secret_clear(s->pmk,32);a04_wlan_secret_clear(s->ptk,64);
 a04_wlan_secret_clear(s->gtk,16);a04_wlan_secret_clear(s->snonce,32);
 s->phase=A04_PSK_FAILED;s->failed=e;s->tx_pending=0;return e;
}
static inline int a04_psk_live(struct a04_psk4way *s,uint64_t now){
 if(!s || !s->phase || s->phase==A04_PSK_FAILED)return A04_WLAN_STATE;
 /* Handshake deadline bounds unfinished/pending work, not an idle live
  * association whose keys and M4 have already really completed. */
 if(s->phase==A04_PSK_COMPLETE && !s->tx_pending && s->m4_confirmed && s->ptk_state==2 && s->gtk_state==2)return 0;
 if(now>=s->deadline)return a04_psk_fail(s,A04_WLAN_TIMEOUT);
 return 0;
}
/* generation MUST be nonzero and unique for this radio across associations.
 * Tokens occupy generation<<16 | sequence; object reset cannot admit old ACKs.
 * Low-level PMK-only entry. Native callers use begin_prepared below.
 * passphrase MUST be0: PBKDF2 here would be after association/possibly locked.
 * Only actual secured association, AP RSN and real station address are accepted.
 */
static inline int a04_psk_begin(struct a04_psk4way *s,const struct a04_wlan_station *sta,
 const struct a04_psk_crypto *c,const uint8_t *secret,unsigned length,unsigned passphrase,
 uint64_t generation,uint64_t now,uint64_t deadline){
 if(!s || s->phase || !sta || !c || !c->random || !c->ptk || !c->mic || !c->unwrap || !secret ||
  passphrase || length!=32 ||
  !generation || generation>0xffffffffffffULL || deadline<=now || deadline-now>10000000 ||
  !sta->secured || !sta->sta_active || sta->failed || sta->join.phase!=4 ||
  !a04_wlan_mac_valid(sta->ap.mac) || !a04_wlan_mac_valid(sta->local.mac) ||
  a04_psk_equal(sta->ap.mac,sta->local.mac,6) || !sta->ap.ssid_length || sta->ap.ssid_length>32 ||
  sta->ap.rsn_length>257)return A04_WLAN_ARGUMENT;
 int e=a04_wlan_rsn_psk_ccmp(sta->ap.rsn,sta->ap.rsn_length);if(e)return e;
 a04_wlan_zero((uint8_t *)s,sizeof(*s));s->crypto=*c;s->serial=generation<<16;s->deadline=deadline;
 s->rsn_bytes=sta->ap.rsn_length;a04_psk_copy(s->rsn,sta->ap.rsn,s->rsn_bytes);
 a04_psk_copy(s->ap,sta->ap.mac,6);a04_psk_copy(s->sta,sta->local.mac,6);
 a04_psk_copy(s->pmk,secret,32);
 s->phase=A04_PSK_WAIT_M1;return 0;
}
/* Consume exactly once, only for the same byte-exact SSID. Actual peer and
 * association generation are independently bound by begin, never inferred
 * from having derived a PMK. Even failed admission consumes the credential.
 */
static inline int a04_psk_begin_prepared(struct a04_psk4way *s,const struct a04_wlan_station *sta,
 const struct a04_psk_crypto *c,struct a04_psk_prepared *prepared,uint64_t generation,
 uint64_t now,uint64_t deadline){
 if(!prepared || !prepared->ready)return A04_WLAN_ARGUMENT;
 int e=A04_WLAN_SECURITY;
 if(sta && prepared->ssid_bytes==sta->ap.ssid_length && prepared->ssid_bytes<=32 &&
  a04_psk_equal(prepared->ssid,sta->ap.ssid,prepared->ssid_bytes))
  e=a04_psk_begin(s,sta,c,prepared->pmk,32,0,generation,now,deadline);
 a04_psk_prepared_clear(prepared);return e;
}
/* This callback can bridge the existing backend verifier, never NULL->allow. */
static inline int a04_psk_verify(void *ctx,const uint8_t *p,unsigned n,unsigned off,unsigned count){
 struct a04_psk4way *s=(struct a04_psk4way *)ctx;
 if(!s || !p || n<99 || n>A04_PSK_MAX_EAPOL || off!=81 || count!=16 ||
  s->phase<A04_PSK_M2_READY || s->phase==A04_PSK_FAILED || !s->crypto.mic)return A04_WLAN_SECURITY;
 uint8_t input[A04_PSK_MAX_EAPOL],mic[16];a04_psk_copy(input,p,n);a04_wlan_zero(input+81,16);
 int e=s->crypto.mic(s->crypto.context,s->ptk,input,n,mic);
 if(!e && !a04_psk_equal(mic,p+81,16))e=A04_WLAN_SECURITY;
 a04_wlan_secret_clear(input,sizeof(input));a04_wlan_secret_clear(mic,sizeof(mic));return e;
}
static inline int a04_psk_make_tx(struct a04_psk4way *s,unsigned kind,uint8_t version){
 a04_wlan_zero(s->tx,sizeof(s->tx));s->tx_bytes=kind==2?121:99;
 uint8_t *p=s->tx;p[0]=version;p[1]=3;p[3]=(uint8_t)(s->tx_bytes-4);p[4]=2;
 p[5]=(uint8_t)(kind==2?1:3);p[6]=10;
 uint64_t r=s->replay;for(unsigned i=0;i<8;i++){p[16-i]=(uint8_t)r;r>>=8;}
 if(kind==2){unsigned n=0;const uint8_t *rsn=a04_wlan_rsn_selected(&n);
  a04_psk_copy(p+17,s->snonce,32);p[98]=(uint8_t)n;a04_psk_copy(p+99,rsn,n);}
 uint8_t mic[16];int e=s->crypto.mic(s->crypto.context,s->ptk,p,s->tx_bytes,mic);
 if(!e)a04_psk_copy(p+81,mic,16);
 a04_wlan_secret_clear(mic,sizeof(mic));return e;
}
/* Authenticated decrypted M3 only: exact beacon RSN, single CCMP GTK KDE,
 * optional IEEE AES-wrap dd/zero padding; unsupported KDEs fail closed.
 * RSC is copied from the authenticated packet, not made up by the caller. */
static inline int a04_psk_keydata(const struct a04_psk4way *s,const uint8_t *p,unsigned n,uint8_t key[16],unsigned *id){
 unsigned k=0,rsn=0,gtk=0;
 while(k<n){
  if(p[k]==0xdd && (k+1==n || p[k+1]==0)){
   for(unsigned j=k+1;j<n;j++)if(p[j])return A04_WLAN_FORMAT;
   k=n;break;
  }
  if(n-k<2 || (unsigned)p[k+1]>n-k-2)return A04_WLAN_FORMAT;
  unsigned len=p[k+1],size=len+2;
  if(p[k]==48){
   if(rsn++ || size!=s->rsn_bytes || !a04_psk_equal(p+k,s->rsn,size))return A04_WLAN_SECURITY;
  }else if(p[k]==221 && len==22 && p[k+2]==0 && p[k+3]==15 && p[k+4]==172 && p[k+5]==1){
   if(gtk++ || (p[k+6]&0xf8) || p[k+7])return A04_WLAN_SECURITY;
   *id=p[k+6]&3;a04_psk_copy(key,p+k+8,16);
  }else return A04_WLAN_UNSUPPORTED;
  k+=size;
 }
 return rsn==1 && gtk==1?0:A04_WLAN_SECURITY;
}
/* Caller validates Ethernet addresses/type against this association before RX.
 * M1/M3 retransmission never regenerates nonce or reinstalls keys. At most four
 * actual transmissions of each response, bounded by the original deadline.
 * Rekey/new ANonce requires explicit teardown; no implicit reinstall.
 */
static inline int a04_psk_rx(struct a04_psk4way *s,const uint8_t *p,unsigned n,uint64_t now){
 int e=a04_psk_live(s,now);if(e)return e;
 /* Late exact retransmissions do not extend the completed handshake budget
  * and must not tear down an otherwise idle completed association. */
 if(s->phase==A04_PSK_COMPLETE && now>=s->deadline)return A04_WLAN_PENDING;
 struct a04_wlan_eapol v;e=a04_wlan_eapol_parse(p,n,&v);if(e)return a04_psk_fail(s,e);
 if(n>A04_PSK_MAX_EAPOL)return a04_psk_fail(s,A04_WLAN_FORMAT);
 if(v.kind==1){
  if(s->phase!=A04_PSK_WAIT_M1){
   if((s->phase==A04_PSK_M2_READY || s->phase==A04_PSK_WAIT_M3) &&
    v.replay>=s->replay && p[0]==s->tx[0] && a04_psk_equal(v.nonce,s->anonce,32) &&
    a04_psk_zero(v.mic,16) && a04_psk_zero(p+49,32)){
    if(s->tx_pending)return v.replay==s->replay?0:A04_WLAN_PENDING;
    s->replay=v.replay;e=a04_psk_make_tx(s,2,p[0]);if(e)return a04_psk_fail(s,e);
    s->phase=A04_PSK_M2_READY;return 0;
   }
   return a04_psk_fail(s,A04_WLAN_REPLAY);
  }
  if(a04_psk_zero(v.nonce,32) || !a04_psk_zero(v.mic,16) || !a04_psk_zero(p+49,32))return a04_psk_fail(s,A04_WLAN_SECURITY);
  a04_psk_copy(s->anonce,v.nonce,32);s->replay=v.replay;
  e=s->crypto.random(s->crypto.context,s->snonce);
  if(!e && (a04_psk_zero(s->snonce,32) || a04_psk_equal(s->snonce,s->anonce,32)))e=A04_WLAN_SECURITY;
  if(!e)e=s->crypto.ptk(s->crypto.context,s->pmk,s->ap,s->sta,s->anonce,s->snonce,s->ptk);
  a04_wlan_secret_clear(s->pmk,sizeof(s->pmk));
  if(!e)e=a04_psk_make_tx(s,2,p[0]);
  if(e)return a04_psk_fail(s,e);
  s->phase=A04_PSK_M2_READY;return 0;
 }
 if(v.kind!=3)return a04_psk_fail(s,A04_WLAN_SEQUENCE);
 unsigned duplicate=s->phase>=A04_PSK_KEYS && s->phase<=A04_PSK_COMPLETE;
 if(duplicate && v.replay==s->replay){
  if(n!=s->m3_bytes || !a04_psk_equal(p,s->m3,n))return a04_psk_fail(s,A04_WLAN_REPLAY);
  if(s->phase==A04_PSK_COMPLETE && !s->tx_pending)s->phase=A04_PSK_M4_READY;
  return 0;
 }
 if(!duplicate && s->phase!=A04_PSK_WAIT_M3)return a04_psk_fail(s,A04_WLAN_SEQUENCE);
 if(v.replay<=s->replay)return a04_psk_fail(s,A04_WLAN_REPLAY);
 /* Never overwrite the borrowed packet while its real TX is in flight. */
 if(s->tx_pending)return A04_WLAN_PENDING;
 if(!a04_psk_equal(v.nonce,s->anonce,32) || v.data_length<24 || (v.data_length&7) ||
  !a04_psk_zero(p+49,16) || !a04_psk_zero(p+71,10))return a04_psk_fail(s,A04_WLAN_SECURITY);
 e=a04_psk_verify(s,p,n,81,16);if(e)return a04_psk_fail(s,e);
 uint8_t plain[A04_PSK_MAX_KEYDATA],gtk[16]={0};unsigned bytes=0,id=0;
 e=s->crypto.unwrap(s->crypto.context,s->ptk+16,v.data,v.data_length,plain,sizeof(plain),&bytes);
 if(!e && (bytes!=v.data_length-8 || bytes>sizeof(plain)))e=A04_WLAN_FORMAT;
 if(!e)e=a04_psk_keydata(s,plain,bytes,gtk,&id);
 if(!e && duplicate && (id!=s->gtk_id || !a04_psk_equal(gtk,s->gtk,16) || !a04_psk_equal(s->rsc,p+65,8)))e=A04_WLAN_SECURITY;
 if(!e && !duplicate){a04_psk_copy(s->gtk,gtk,16);s->gtk_id=id;}
 a04_wlan_secret_clear(gtk,sizeof(gtk));
 a04_wlan_secret_clear(plain,sizeof(plain));if(e)return a04_psk_fail(s,e);
 s->replay=v.replay;a04_psk_copy(s->rsc,p+65,8);a04_psk_copy(s->m3,p,n);s->m3_bytes=n;
 if(!duplicate)s->tx_attempts=0;
 e=a04_psk_make_tx(s,4,p[0]);if(e)return a04_psk_fail(s,e);
 if(!duplicate)s->phase=A04_PSK_KEYS;
 else if(s->phase==A04_PSK_COMPLETE)s->phase=A04_PSK_M4_READY;
 return 0;
}
/* Mark a unique TX in flight BEFORE invoking the real send. The returned
 * borrowed packet remains valid until done/teardown. A queue failure must be
 * reported as failure, not TX_DONE. Packet cookies must follow real TX IDs. */
static inline int a04_psk_tx_take(struct a04_psk4way *s,uint64_t now,const uint8_t **p,unsigned *n,uint64_t *cookie){
 int e=a04_psk_live(s,now);if(e)return e;
 if(!p || !n || !cookie || s->tx_pending || (s->phase!=A04_PSK_M2_READY && s->phase!=A04_PSK_M4_READY))return A04_WLAN_STATE;
 if(s->tx_attempts>=4)return a04_psk_fail(s,A04_WLAN_TIMEOUT);
 s->tx_attempts++;s->tx_pending=1;s->tx_kind=s->phase==A04_PSK_M2_READY?2:4;
 s->tx_cookie=++s->serial;*cookie=s->tx_cookie;*p=s->tx;*n=s->tx_bytes;return 0;
}
static inline int a04_psk_tx_done(struct a04_psk4way *s,uint64_t cookie,unsigned status,uint64_t now){
 int e=a04_psk_live(s,now);if(e)return e;
 if(!s->tx_pending || cookie!=s->tx_cookie)return A04_WLAN_SEQUENCE;
 if(status)return a04_psk_fail(s,A04_WLAN_STATUS);
 s->tx_pending=0;if(s->tx_kind==4)s->m4_confirmed=1;
 s->phase=s->tx_kind==2?A04_PSK_WAIT_M3:A04_PSK_COMPLETE;return 0;
}
/* Only PTK first, then GTK, one submission each. Keys are borrowed until the
 * synchronous command has copied them. Cookies MUST bind real command/event
 * identity plus BSS/peer. PTK completion requires its real firmware event;
 * GTK completion is successful full synchronous HIF transfer (noNeedResp),
 * not software enqueue and not a claimed firmware key-install ACK.
 */
static inline int a04_psk_key_take(struct a04_psk4way *s,unsigned pairwise,uint64_t now,
 const uint8_t **key,unsigned *id,const uint8_t **rsc,uint64_t *cookie){
 int e=a04_psk_live(s,now);if(e)return e;
 if(s->phase!=A04_PSK_KEYS || !key || !id || !rsc || !cookie || pairwise>1 ||
  (pairwise?s->ptk_state!=0:(s->ptk_state!=2 || s->gtk_state!=0)))return A04_WLAN_STATE;
 if(pairwise)s->ptk_state=1;else s->gtk_state=1;
 *key=pairwise?s->ptk+32:s->gtk;*id=pairwise?0:s->gtk_id;
 /* Pairwise starts with PN zero, group with authenticated M3 RSC. */
 static const uint8_t zero_rsc[8]={0};*rsc=pairwise?zero_rsc:s->rsc;
 s->key_cookie=++s->serial;*cookie=s->key_cookie;return 0;
}
static inline int a04_psk_key_done(struct a04_psk4way *s,unsigned pairwise,uint64_t cookie,unsigned status,uint64_t now){
 int e=a04_psk_live(s,now);if(e)return e;
 if(s->phase!=A04_PSK_KEYS || pairwise>1 || cookie!=s->key_cookie ||
  (pairwise?s->ptk_state!=1:(s->ptk_state!=2 || s->gtk_state!=1)))return A04_WLAN_SEQUENCE;
 if(status)return a04_psk_fail(s,A04_WLAN_STATUS);
 if(pairwise)s->ptk_state=2;else{s->gtk_state=2;s->phase=A04_PSK_M4_READY;}
 return 0;
}
/* Advisory only: bridge must additionally enforce existing security/backend
 * TX_DONE and actual key completion state before opening ordinary IP traffic. */
static inline int a04_psk_authenticated(const struct a04_psk4way *s){
 return s && !s->failed && s->m4_confirmed && s->ptk_state==2 && s->gtk_state==2;
}
/* One-shot eligibility only. Invoke AFTER the existing backend independently
 * agrees to authorize. The runtime owns actual port opening/interest emission;
 * no message (including0xe0000130) is emitted by this pure state header. */
static inline int a04_psk_completion_take(struct a04_psk4way *s){
 if(!a04_psk_authenticated(s) || s->completion_reported)return A04_WLAN_STATE;
 s->completion_reported=1;return 0;
}
#endif
