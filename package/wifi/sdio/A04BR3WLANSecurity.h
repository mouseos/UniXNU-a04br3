/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Native supplicant/backend boundary, NOT a replacement WPA supplicant.
 * gen3 wlan_oid.c CMD_802_11_KEY / nic_rx.c ADD_PKEY_DONE; AOSP
 * wpa_supplicant_8/src/common/wpa_common.h and ieee802_1x_defs.h.
 * Only RSN WPA2-PSK/CCMP128 descriptor-v2 is supported. No TKIP/PMF/WPA3.
 * Trusted supplicant owns PSK/PMK/PTK derivation, MIC and GTK unwrap. Required
 * MIC callback must cryptographically verify the whole EAPOL packet with its
 * MIC field zeroed. Missing/failed verifier never authorizes key installation.
 */
#ifndef A04BR3_WLAN_SECURITY_H
#define A04BR3_WLAN_SECURITY_H
#include "A04BR3WLANStation.h"
static inline unsigned a04_wlan_be16(const uint8_t *p){return ((unsigned)p[0]<<8)|p[1];}
static inline uint64_t a04_wlan_be64(const uint8_t *p){
 uint64_t n=0;for(unsigned i=0;i<8;i++)n=(n<<8)|p[i];return n;
}
static inline void a04_wlan_secret_clear(void *p,unsigned n){
 volatile uint8_t *b=(volatile uint8_t *)p;for(unsigned i=0;i<n;i++)b[i]=0;
}
/* Reject an advertised suite set we cannot safely negotiate, including PMF.
 * The selected association IE is canonical CCMP/PSK, not the AP's whole list. */
static inline int a04_wlan_rsn_psk_ccmp(const uint8_t *p,unsigned n){
 static const uint8_t ccmp[4]={0,15,172,4},psk[4]={0,15,172,2};
 if(!p || n<20 || p[0]!=48 || p[1]+2u!=n || a04_wmt_le16(p+2)!=1 || !a04_wlan_equal(p+4,ccmp,4))return A04_WLAN_SECURITY;
 unsigned k=8,count=a04_wmt_le16(p+k),found=0;k+=2;
 if(!count || count>16 || count>(n-k)/4)return A04_WLAN_FORMAT;
 for(unsigned i=0;i<count;i++,k+=4)found|=a04_wlan_equal(p+k,ccmp,4);
 if(!found || n-k<2)return A04_WLAN_SECURITY;
 count=a04_wmt_le16(p+k);k+=2;found=0;
 if(!count || count>16 || count>(n-k)/4)return A04_WLAN_FORMAT;
 for(unsigned i=0;i<count;i++,k+=4)found|=a04_wlan_equal(p+k,psk,4);
 if(!found)return A04_WLAN_SECURITY;
 if(k==n)return 0;
 if(n-k!=2 || (a04_wmt_le16(p+k)&0xc0))return A04_WLAN_UNSUPPORTED;
 return 0;
}
static inline const uint8_t *a04_wlan_rsn_selected(unsigned *n){
 static const uint8_t p[22]={48,20,1,0,0,15,172,4,1,0,0,15,172,4,1,0,0,15,172,2,0,0};
 if(n)*n=22;
 return p;
}
struct a04_wlan_eapol {unsigned info,kind,data_length;uint64_t replay;const uint8_t *nonce,*mic,*data;};
static inline int a04_wlan_eapol_parse(const uint8_t *p,unsigned n,struct a04_wlan_eapol *v){
 if(!p || !v)return A04_WLAN_ARGUMENT;
 /* Correct RSN nonce is32 bytes. Vendor privacy.h's unused16-byte nonce
  * declaration is NOT used; AOSP's packed wire struct defines offsets. */
 if(n<99 || n>2048 || p[0]<1 || p[0]>3 || p[1]!=3 || a04_wlan_be16(p+2)!=n-4 || p[4]!=2 ||
  a04_wlan_be16(p+97)!=n-99)return A04_WLAN_FORMAT;
 unsigned info=a04_wlan_be16(p+5),kind=0;
 if((info&7)!=2 || (info&0xec30))return A04_WLAN_UNSUPPORTED;
 if(info==0x008a)kind=1;else if(info==0x010a)kind=2;
 else if(info==0x13ca)kind=3;else if(info==0x030a)kind=4;
 else return A04_WLAN_UNSUPPORTED;
 if(a04_wlan_be16(p+7)!=((kind==1 || kind==3)?16u:0u))return A04_WLAN_SECURITY;
 if((kind==1 || kind==4) && n!=99)return A04_WLAN_FORMAT;
 v->info=info;v->kind=kind;v->replay=a04_wlan_be64(p+9);v->nonce=p+17;v->mic=p+81;v->data=p+99;v->data_length=n-99;return 0;
}
typedef int (*a04_wlan_mic_verify)(void *,const uint8_t *,unsigned,unsigned,unsigned);
struct a04_wlan_security {
 unsigned enabled,phase,pending_tx,ptk_pending,ptk_confirmed,gtk_accepted,m4_confirmed,authorized;
 uint64_t replay,deadline_us,rx_pn[16],gtk_pn[4];unsigned gtk_id;uint8_t anonce[32];int failed;
};
static inline int a04_wlan_security_fail(struct a04_wlan_security *s,int e){
 s->failed=e;s->authorized=0;return e;
}
static inline int a04_wlan_security_begin(struct a04_wlan_security *s,const struct a04_wlan_station *sta,
 uint64_t now,uint64_t deadline){
 if(!s || !sta || !sta->secured || !sta->sta_active || sta->join.phase!=4 || sta->failed ||
  s->enabled || s->failed || deadline<=now || deadline-now>10000000)return A04_WLAN_STATE;
 int e=a04_wlan_rsn_psk_ccmp(sta->ap.rsn,sta->ap.rsn_length);if(e)return e;
 s->enabled=1;s->deadline_us=deadline;return 0;
}
static inline int a04_wlan_security_rx(struct a04_wlan_security *s,const uint8_t *p,unsigned n,
 a04_wlan_mic_verify verify,void *ctx,uint64_t now){
 if(!s || !s->enabled || s->failed || !s->deadline_us)return A04_WLAN_STATE;
 if(s->authorized)return a04_wlan_security_fail(s,A04_WLAN_UNSUPPORTED); /* explicit rekey unsupported */
 if(now>=s->deadline_us)return a04_wlan_security_fail(s,A04_WLAN_TIMEOUT);
 struct a04_wlan_eapol v;int e=a04_wlan_eapol_parse(p,n,&v);if(e)return a04_wlan_security_fail(s,e);
 if(v.kind==1 && !s->phase){
  unsigned nonzero=0,mic=0;for(unsigned i=0;i<32;i++)nonzero|=v.nonce[i];
  for(unsigned i=0;i<16;i++)mic|=v.mic[i];
  if(!nonzero || mic)return a04_wlan_security_fail(s,A04_WLAN_SECURITY);
  for(unsigned i=0;i<32;i++)s->anonce[i]=v.nonce[i];
  s->replay=v.replay;s->phase=1;return 0;
 }
 if(v.kind!=3 || s->phase!=2 || s->pending_tx)return a04_wlan_security_fail(s,A04_WLAN_SEQUENCE);
 if(v.replay<=s->replay)return a04_wlan_security_fail(s,A04_WLAN_REPLAY);
 if(!a04_wlan_equal(v.nonce,s->anonce,32) || !v.data_length || (v.data_length&7) || v.data_length<24 ||
  !verify || verify(ctx,p,n,81,16))return a04_wlan_security_fail(s,A04_WLAN_SECURITY);
 s->replay=v.replay;s->phase=3;return 0;
}
/* Validate native supplicant output before FIFO send. Advance only on TX_DONE. */
static inline int a04_wlan_security_tx_check(struct a04_wlan_security *s,const uint8_t *p,unsigned n,
 a04_wlan_mic_verify verify,void *ctx,uint64_t now,unsigned *kind){
 if(!s || !kind || !s->enabled || s->failed || s->pending_tx || now>=s->deadline_us)return A04_WLAN_STATE;
 struct a04_wlan_eapol v;int e=a04_wlan_eapol_parse(p,n,&v);if(e)return e;
 if(v.replay!=s->replay || !((s->phase==1 && v.kind==2) || (s->phase==3 && v.kind==4)))return A04_WLAN_SEQUENCE;
 if(!verify || verify(ctx,p,n,81,16))return A04_WLAN_SECURITY;
 if(v.kind==2){unsigned nonzero=0;for(unsigned i=0;i<32;i++)nonzero|=v.nonce[i];
  if(!nonzero || a04_wlan_rsn_psk_ccmp(v.data,v.data_length))return A04_WLAN_SECURITY;
 }else if(!s->ptk_confirmed)return A04_WLAN_STATE;
 *kind=v.kind;return 0;
}
static inline int a04_wlan_security_tx_done(struct a04_wlan_security *s,unsigned kind,unsigned status){
 if(!s || s->failed || s->pending_tx!=kind || (kind!=2 && kind!=4))return A04_WLAN_STATE;
 if(status)return a04_wlan_security_fail(s,A04_WLAN_STATUS);
 s->pending_tx=0;if(kind==2)s->phase=2;else s->m4_confirmed=1;return 0;
}
/* 64-byte native CMD_802_11_KEY: caller supplies keys from verified supplicant.
 * Serialize CCMP only. No implicit key reinstall/rekey or WPA completion. */
static inline int a04_wlan_ccmp_key_encode(const struct a04_wlan_local *l,const uint8_t peer[6],
 unsigned pairwise,unsigned id,const uint8_t *key,unsigned key_length,const uint8_t rsc[8],uint8_t p[64]){
 if(!a04_wlan_local_valid(l) || !peer || !a04_wlan_mac_valid(peer) || !p || pairwise>1 || id>3 ||
  (pairwise && id) || !key || key_length!=16 || !rsc)return A04_WLAN_ARGUMENT;
 if(rsc[6] || rsc[7])return A04_WLAN_ARGUMENT;
 a04_wlan_zero(p,64);p[0]=1;p[1]=(uint8_t)pairwise;p[2]=(uint8_t)pairwise;
 for(unsigned i=0;i<6;i++)p[4+i]=peer[i];
 p[10]=(uint8_t)l->bss;p[11]=4;p[12]=(uint8_t)id;p[13]=16;p[14]=(uint8_t)(pairwise?l->wlan:l->bmc);
 for(unsigned i=0;i<16;i++)p[16+i]=key[i];
 for(unsigned i=0;i<8;i++)p[48+i]=rsc[i];
 return 0;
}
static inline int a04_wlan_security_install(struct a04_wlan_security *s,struct a04_wlan_station *sta,
 struct a04_wlan_host *h,unsigned pairwise,unsigned id,const uint8_t key[16],const uint8_t rsc[8],uint64_t now){
 if(!s || !sta || !h || !s->enabled || s->phase!=3 || s->failed || !sta->sta_active ||
  (pairwise?(s->ptk_pending || s->ptk_confirmed):(!s->ptk_confirmed || s->gtk_accepted)))return A04_WLAN_STATE;
 if(now>=s->deadline_us)return a04_wlan_security_fail(s,A04_WLAN_TIMEOUT);
 uint8_t p[64];int e=a04_wlan_ccmp_key_encode(&sta->local,sta->ap.mac,pairwise,id,key,16,rsc,p);
 if(!e)e=a04_wlan_set(h,7,p,64,0);
 a04_wlan_secret_clear(p,sizeof(p));
 /* FIFO scratch is shared: erase key material after the synchronous transfer. */
 a04_wlan_secret_clear(h->tx,sizeof(h->tx));
 if(e)return a04_wlan_security_fail(s,e);
 uint64_t pn=0;for(unsigned i=0;i<6;i++)pn|=(uint64_t)rsc[i]<<(8*i);
 if(pairwise){s->ptk_pending=1;for(unsigned i=0;i<16;i++)s->rx_pn[i]=pn;}
 /* Set only after a04_wlan_set returned full synchronous HIF success. */
 else{s->gtk_accepted=1;s->gtk_id=id;s->gtk_pn[id]=pn;}
 return 0;
}
static inline int a04_wlan_security_key_event(struct a04_wlan_security *s,const struct a04_wlan_station *sta,
 const struct a04_wlan_event *v,uint64_t now){
 if(!s || !sta || !v || s->failed)return A04_WLAN_STATE;
 if(v->id!=0x24)return A04_WLAN_UNSUPPORTED;
 if(now>=s->deadline_us)return a04_wlan_security_fail(s,A04_WLAN_TIMEOUT);
 if(!s->ptk_pending || s->ptk_confirmed || v->length!=8 || v->payload[1] || v->payload[0]!=sta->local.bss ||
  !a04_wlan_equal(v->payload+2,sta->ap.mac,6))return a04_wlan_security_fail(s,A04_WLAN_SEQUENCE);
 s->ptk_pending=0;s->ptk_confirmed=1;return 0;
}
/* Called only by the native supplicant after its authenticated handshake,
 * RSN/GTK checks and successful M4 TX completion. Never from a set-key ioctl.
 * gtk_accepted means CID7 passed synchronous full HIF transfer, matching
 * gen3 noNeedResp set_key completion. It is NOT a firmware key-install ACK.
 * This trusted callback is a security boundary, not a synthetic success flag. */
static inline int a04_wlan_security_authorize(struct a04_wlan_security *s,int supplicant_result,uint64_t now){
 if(!s || s->failed || !s->enabled || s->phase!=3 || !s->ptk_confirmed || !s->gtk_accepted || !s->m4_confirmed)return A04_WLAN_STATE;
 if(now>=s->deadline_us)return a04_wlan_security_fail(s,A04_WLAN_TIMEOUT);
 if(supplicant_result)return a04_wlan_security_fail(s,A04_WLAN_SECURITY);
 s->authorized=1;return 0;
}
#endif
