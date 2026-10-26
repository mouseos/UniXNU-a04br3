/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * MT6630 legacy Ethernet HIF path: gen3 nic_tx.h/nic_rx.h, nic_tx.c.
 * No ifnet registration. Caller owns buffers and serializes through host lock.
 * FIFO completion, firmware TX status and WTQCR page return are independent.
 */
#ifndef A04BR3_WLAN_DATA_H
#define A04BR3_WLAN_DATA_H
#include "A04BR3WLANSecurity.h"
struct a04_wlan_data_state {unsigned pending,pid,next_pid,eapol_kind,confirmed;uint64_t deadline_us;int failed;};
struct a04_wlan_ethernet {const uint8_t *data;unsigned length,eapol,wlan,cipher;};
static inline int a04_wlan_port_allowed(const struct a04_wlan_station *s,const struct a04_wlan_security *sec,unsigned eapol){
 if(!s || s->failed || !s->sta_active || s->join.phase!=4 || !sec || sec->failed || s->secured!=sec->enabled)return 0;
 return eapol || (sec->enabled?sec->authorized:1);
}
static inline int a04_wlan_ethernet_rx(const struct a04_wlan_station *s,struct a04_wlan_security *sec,
 const uint8_t *p,unsigned n,struct a04_wlan_ethernet *v){
 if(!s || !sec || !p || !v)return A04_WLAN_ARGUMENT;
 if(n<30 || n>4096 || a04_wmt_le16(p)!=n || (a04_wmt_le16(p+2)>>13)!=2 || !(p[6]&0x80) ||
  (p[7]&3) || (a04_wmt_le16(p+10)&0x0fff))return A04_WLAN_FORMAT;
 unsigned groups=(a04_wmt_le16(p+2)>>9)&15;
 unsigned off=16+((groups&8)?16:0)+((groups&1)?16:0)+((groups&2)?8:0)+((groups&4)?24:0)+((p[6]&0x40)?2:0);
 if(off>n || n-off<14 || n-off>1514 || (p[6]&63)!=14)return A04_WLAN_FORMAT;
 const uint8_t *eth=p+off;unsigned eapol=a04_wlan_be16(eth+12)==0x888e;
 if(!a04_wlan_port_allowed(s,sec,eapol))return A04_WLAN_SECURITY;
 unsigned group=eth[0]&1,cipher=p[9]>>4;
 if((!group && (!a04_wlan_equal(eth,s->local.mac,6) || p[8]!=s->local.wlan)) ||
  (group && p[8]!=s->local.bmc))return A04_WLAN_SEQUENCE;
 if(!sec->enabled && cipher)return A04_WLAN_SECURITY;
 if(eapol){if(!sec->enabled || group || !a04_wlan_equal(eth+6,s->ap.mac,6))return A04_WLAN_SECURITY;}
 else if(sec->enabled && (cipher!=4 || (group && !sec->gtk_accepted)))return A04_WLAN_SECURITY;
 /* gen3 que_mgt.c qmHandleRxReplay: group1 PN is six LE octets.
  * No AMPDU/reorder support in this subset. Reject missing security metadata
  * rather than accepting a packet whose anti-replay check cannot be proved. */
 if(cipher==4){
  if(!(groups&1))return A04_WLAN_SECURITY;
  unsigned pn_off=16+((groups&8)?16:0),kid=p[4]>>6,tid=p[9]&15;
  if((group && (!sec->gtk_accepted || kid!=sec->gtk_id)) || (!group && (!sec->ptk_confirmed || kid)))return A04_WLAN_SECURITY;
  uint64_t pn=0;for(unsigned i=0;i<6;i++)pn|=(uint64_t)p[pn_off+i]<<(8*i);
  uint64_t *last=group?&sec->gtk_pn[kid]:&sec->rx_pn[tid];
  if(pn<=*last)return A04_WLAN_REPLAY;
  *last=pn;
 }
 v->data=eth;v->length=n-off;v->eapol=eapol;v->wlan=p[8];v->cipher=cipher;return 0;
}
/* Normal authenticated IP gets a fresh per-TX deadline even after the old
 * handshake window. Only unfinished security and EAPOL remain clamped. */
static inline int a04_wlan_data_deadline(const struct a04_wlan_security *sec,unsigned eapol,uint64_t now,uint64_t *out){
 if(!sec || !out || eapol>1 || now>UINT64_MAX-900000)return A04_WLAN_ARGUMENT;
 uint64_t until=now+900000;
 if(sec->enabled && (!sec->authorized || eapol)){
  if(!sec->deadline_us || now>=sec->deadline_us)return A04_WLAN_TIMEOUT;
  if(until>sec->deadline_us)until=sec->deadline_us;
 }
 *out=until;return 0;
}
static inline int a04_wlan_ethernet_send(struct a04_wlan_host *h,const struct a04_wlan_station *s,
 struct a04_wlan_security *sec,struct a04_wlan_data_state *d,const uint8_t *eth,unsigned n,
 a04_wlan_mic_verify verify,void *ctx,uint64_t now,uint64_t deadline){
 if(!h || !s || !sec || !d || !eth || !h->runtime || h->failed || d->failed || d->pending ||
  n<14 || n>1514 || deadline<=now || deadline-now>1000000)return A04_WLAN_STATE;
 unsigned eapol=a04_wlan_be16(eth+12)==0x888e,kind=0;
 if(!a04_wlan_port_allowed(s,sec,eapol) || !a04_wlan_equal(eth+6,s->local.mac,6))return A04_WLAN_SECURITY;
 if(eapol){
  if(!sec->enabled || !a04_wlan_equal(eth,s->ap.mac,6))return A04_WLAN_SECURITY;
  if(deadline>sec->deadline_us)return A04_WLAN_ARGUMENT;
  int e=a04_wlan_security_tx_check(sec,eth+14,n-14,verify,ctx,now,&kind);if(e)return e;
 }
 unsigned pages=(n+28+127)/128;
 for(unsigned tries=0;!eapol && h->tc0_free<pages;tries++){
  if(tries==100){d->failed=A04_WLAN_TIMEOUT;return a04_wlan_fail(h,d->failed);}
  int e=a04_wlan_resources(h);if(e){d->failed=e;return e;}
  if(h->tc0_free<pages)h->transport->io->delay(h->transport->io->ctx,1000);
 }
 /* Ethernet station TX always uses peer WTBL, even DA multicast: air receiver
  * is the AP (ToDS), not our BMC receive entry. HW ACK remains enabled. */
 unsigned pid=d->next_pid%127+1;
 a04_wlan_zero(h->tx,28);a04_wmt_put16(h->tx,n+28);h->tx[2]=20;h->tx[3]=(uint8_t)(eapol?0x88:0);h->tx[4]=(uint8_t)s->local.wlan;
 h->tx[5]=0x90;h->tx[6]=(uint8_t)(sec->enabled && (!eapol || sec->ptk_confirmed)?0x80:0);
 h->tx[7]=(uint8_t)(s->local.own<<2);a04_wmt_put16(h->tx+12,1u<<11);
 if(eapol){
  unsigned all=0,basic=0;int rate=a04_wlan_rate_mask(&s->ap,&all,&basic);if(rate)return rate;
  h->tx[11]=0x80;a04_wmt_put16(h->tx+26,a04_wlan_basic_rate_code(basic)<<2);
 }
 h->tx[20]=(uint8_t)pid;h->tx[21]=2; /* MCU TX_DONE event, not raw HW TXS. */
 for(unsigned i=0;i<n;i++)h->tx[28+i]=eth[i];
 int e=a04_wlan_send(h,n+28,eapol?1:0);if(e){d->failed=e;sec->authorized=0;return e;}
 d->pending=1;d->pid=d->next_pid=pid;d->eapol_kind=kind;d->deadline_us=deadline;
 if(kind)sec->pending_tx=kind;
 return 0;
}
static inline int a04_wlan_data_tx_event(struct a04_wlan_host *h,const struct a04_wlan_station *s,
 struct a04_wlan_security *sec,struct a04_wlan_data_state *d,const struct a04_wlan_event *v,uint64_t now){
 if(!h || !s || !sec || !d || !v || d->failed)return A04_WLAN_STATE;
 if(v->id!=0x0f)return A04_WLAN_UNSUPPORTED;
 if(!d->pending)return A04_WLAN_SEQUENCE;
 if(now>=d->deadline_us || (d->eapol_kind && now>=sec->deadline_us)){d->failed=A04_WLAN_TIMEOUT;sec->authorized=0;return a04_wlan_fail(h,d->failed);}
 if(v->length!=16 || v->payload[0]!=d->pid || v->payload[4]!=s->local.wlan){
  d->failed=A04_WLAN_SEQUENCE;sec->authorized=0;return a04_wlan_fail(h,d->failed);
 }
 if(v->payload[1]){d->failed=A04_WLAN_STATUS;sec->authorized=0;return a04_wlan_fail(h,d->failed);}
 if(d->eapol_kind){int e=a04_wlan_security_tx_done(sec,d->eapol_kind,0);if(e){d->failed=e;return a04_wlan_fail(h,e);}}
 d->pending=0;d->confirmed++;return 0; /* NOT a page credit */
}
/* This subset does not negotiate PMF. A peer deauth/disassoc invalidates the
 * controlled port immediately; only an explicit new session may reconnect. */
static inline int a04_wlan_disconnect_rx(struct a04_wlan_station *s,struct a04_wlan_security *sec,
 const uint8_t *p,unsigned n){
 if(!s || !sec || !p || !s->sta_active || s->failed)return A04_WLAN_STATE;
 if(n<26 || (p[0]!=0xc0 && p[0]!=0xa0) || (p[1]&0x47) || (a04_wmt_le16(p+22)&15) ||
  !a04_wlan_equal(p+4,s->local.mac,6) || !a04_wlan_equal(p+10,s->ap.mac,6) || !a04_wlan_equal(p+16,s->ap.mac,6))return A04_WLAN_SEQUENCE;
 s->sta_active=0;s->failed=A04_WLAN_STATE;sec->authorized=0;sec->failed=A04_WLAN_STATE;
 return 0;
}
/* One bounded poll step. Caller supplies monotonic time and consumes returned
 * event/frame before the next call overwrites h->rx. Empty FIFO is PENDING,
 * not a timeout poison; deadlines are checked even with no RX traffic. */
static inline int a04_wlan_backend_receive(struct a04_wlan_host *h,struct a04_wlan_station *s,
 struct a04_wlan_security *sec,struct a04_wlan_data_state *d,uint64_t now,unsigned preferred_port){
 if(!h || !s || !sec || !d || preferred_port>1 || h->failed || s->failed || sec->failed || d->failed)return A04_WLAN_STATE;
 if((s->deadline_us && now>=s->deadline_us) || (sec->enabled && !sec->authorized && sec->deadline_us && now>=sec->deadline_us) ||
  (d->pending && now>=d->deadline_us)){
  sec->authorized=0;s->failed=A04_WLAN_TIMEOUT;return a04_wlan_fail(h,s->failed);
 }
 uint32_t lengths=0;int e=a04_wlan_read(h,0x90,&lengths);if(e)return e;
 unsigned first=(lengths>>(preferred_port*16))&65535,other=(lengths>>((1-preferred_port)*16))&65535;
 if(!first && !other)return A04_WLAN_PENDING;
 return a04_wlan_receive(h,first?preferred_port:1-preferred_port,1);
}
#endif
