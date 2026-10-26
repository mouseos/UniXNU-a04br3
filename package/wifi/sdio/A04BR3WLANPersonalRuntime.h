/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Production Personal-state/real-HIF adapter. Caller holds transportLock for
 * firmware transactions; CSPRNG/M1 crypto runs separately outside that lock.
 */
#ifndef A04BR3_WLAN_PERSONAL_RUNTIME_H
#define A04BR3_WLAN_PERSONAL_RUNTIME_H
#include "A04BR3WLANRuntime.h"
static inline int a04_wlan_personal_start(struct a04_wlan_runtime *r,const struct a04_psk_crypto *ops,uint64_t now){
 if(!r || !ops || !r->personal_admitted || r->personal.phase || !r->station.sta_active || !r->station.secured)return A04_WLAN_STATE;
 int e=a04_wlan_security_begin(&r->security,&r->station,now,now+10000000);
 if(!e)e=a04_psk_begin_prepared(&r->personal,&r->station,ops,&r->credential,r->association_generation,now,now+10000000);
 return e;
}
/* Captured consumer epoch is invalidated by bind/unbind/stop even when the
 * station association generation has not changed. Retaining the old client
 * keeps memory alive; it does NOT authorize reviving its canceled keys. */
static inline int a04_wlan_personal_commit_allowed(const struct a04_wlan_runtime *r,uint64_t association,
 uint64_t captured_consumer,uint64_t current_consumer,unsigned same_bound_consumer){
 return r && same_bound_consumer && captured_consumer==current_consumer && association==r->association_generation &&
  r->configured && r->admin && r->personal_admitted && !r->failed && !r->teardown.pending &&
  !r->station.failed && !r->station.join.failed && r->station.sta_active && r->station.join.phase==4 &&
  !r->security.failed && !r->personal.failed && r->personal.phase && r->personal.phase!=A04_PSK_FAILED;
}
/* Commit a crypto result only after owner checked the same association epoch.
 * These phases derive from validated actual M1/M3, never from native SET20. */
static inline int a04_wlan_personal_observed(struct a04_wlan_runtime *r,const struct a04_psk4way *verified){
 if(!r || !verified || !r->personal_admitted || !r->security.enabled || verified->failed || r->failed ||
  r->personal.failed || r->personal.phase==A04_PSK_FAILED || !r->personal.phase || r->security.failed ||
  r->teardown.pending || !r->admin || !r->configured || !r->station.sta_active || r->station.failed ||
  (verified->serial>>16)!=r->association_generation)return A04_WLAN_STATE;
 r->personal=*verified;
 if(verified->phase==A04_PSK_M2_READY)r->security.phase=1;
 else if(verified->phase>=A04_PSK_KEYS && verified->phase<=A04_PSK_COMPLETE)r->security.phase=3;
 else if(verified->phase!=A04_PSK_WAIT_M3)return A04_WLAN_SEQUENCE;
 r->security.replay=verified->replay;
 for(unsigned i=0;i<32;i++)r->security.anonce[i]=verified->anonce[i];
 return 0;
}
static inline int a04_wlan_personal_drive(struct a04_wlan_runtime *r,struct a04_wlan_host *h,uint64_t now){
 if(!r || !h || !r->personal_admitted || !r->personal.phase || r->personal_busy)return 0;
 int e=a04_psk_live(&r->personal,now);if(e)return e;
 if(!a04_wlan_personal_commit_allowed(r,r->personal.serial>>16,0,0,1))return A04_WLAN_STATE;
 struct a04_psk4way *s=&r->personal;
 if(s->phase==A04_PSK_KEYS){
  unsigned pair=s->ptk_state==0?1:0;
  if(s->ptk_state==1 || s->gtk_state)return 0;
  const uint8_t *key=0,*rsc=0;unsigned id=0;uint64_t cookie=0;
  e=a04_psk_key_take(s,pair,now,&key,&id,&rsc,&cookie);
  if(!e)e=a04_wlan_security_install(&r->security,&r->station,h,pair,id,key,rsc,now);
  if(e)return a04_psk_fail(s,e);
  r->personal_key_cookie=cookie;
  /* Matching gen3 set_key completes at actual full synchronous HIF transfer.
   * PTK still needs EID24; GTK has no separate firmware ACK. Caller holds
   * transportLock, so association/consumer cannot change during transfer. */
  if(!pair){
   if(!r->security.gtk_accepted)return a04_psk_fail(s,A04_WLAN_STATE);
   e=a04_psk_key_done(s,0,cookie,0,now);
   if(e)return a04_psk_fail(s,e);
  }
  return 0;
 }
 if((s->phase==A04_PSK_M2_READY || s->phase==A04_PSK_M4_READY) && !r->data.pending && !s->tx_pending){
  const uint8_t *packet=0;unsigned bytes=0;uint64_t cookie=0;
  e=a04_psk_tx_take(s,now,&packet,&bytes,&cookie);if(e)return e;
  uint8_t eth[14+121];
  for(unsigned i=0;i<6;i++){eth[i]=r->station.ap.mac[i];eth[6+i]=r->station.local.mac[i];}
  eth[12]=0x88;eth[13]=0x8e;
  for(unsigned i=0;i<bytes;i++)eth[14+i]=packet[i];
  uint64_t deadline=now+900000;if(deadline>s->deadline)deadline=s->deadline;
  e=a04_wlan_ethernet_send(h,&r->station,&r->security,&r->data,eth,bytes+14,a04_psk_verify,s,now,deadline);
  a04_wlan_secret_clear(eth,sizeof(eth));if(e)return e;
  r->personal_tx_cookie=cookie;
 }
 return 0;
}
#endif
