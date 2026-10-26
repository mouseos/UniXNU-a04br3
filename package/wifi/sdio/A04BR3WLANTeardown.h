/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Open-session teardown, gen3 ais_fsm.c/auth.c/cnm_mem.c/nic.c contracts.
 * All calls serialized by the SDIO transport lock. TX submission is admission,
 * never a firmware ACK. Old slots remain quarantined (no event epoch guessing).
 * Secure key cancellation is deliberately outside this component's contract.
 */
#ifndef A04BR3_WLAN_TEARDOWN_H
#define A04BR3_WLAN_TEARDOWN_H
#include "A04BR3WLANData.h"
#include <sys/errno.h>
struct a04_wlan_teardown {
 unsigned pending,completed,pid,retired_bss,retired_wlan;
 uint64_t deadline_us;
 struct a04_wlan_local replacement;
};
static inline int a04_wlan_teardown_secure(const struct a04_wlan_station *s,const struct a04_wlan_security *k){
 return s->secured || k->enabled || k->phase || k->pending_tx || k->ptk_pending ||
  k->ptk_confirmed || k->gtk_accepted || k->m4_confirmed || k->authorized || k->failed;
}
/* Source command removal has fgNeedResp=FALSE. This completes command queueing,
 * not a claimed hardware completion. On partial send failure retain old host
 * context and all reserved indices; caller poisons runtime, never reconnects.
 */
static inline int a04_wlan_teardown_finish(struct a04_wlan_teardown *t,
 struct a04_wlan_station *s,struct a04_wlan_host *h){
 uint8_t bss[88],active[12],pm[4]={0},remove[4]={0},abort[16]={0};
 int e=a04_wlan_bss_encode(&s->local,&s->ap,0,0,bss);
 if(!e)e=a04_wlan_activate_encode(&s->local,0,active);
 if(e)return e;
 /* nicDeactivateNetwork zeroes the address on deactivation, unlike activate. */
 for(unsigned i=4;i<10;i++)active[i]=0;
 pm[0]=(uint8_t)s->local.bss;
 remove[0]=0; /* STA_REC_CMD_ACTION_STA, NOT all-network removal */
 remove[1]=(uint8_t)s->local.sta;remove[2]=(uint8_t)s->local.bss;
 /* cnmChMngrAbortPrivilege consumes only BSS/token/action. No new grant. */
 abort[0]=(uint8_t)s->local.bss;abort[1]=(uint8_t)s->channel.token;abort[2]=1;
 if(s->channel_pending || (s->grant_ms && !s->sta_active))e=a04_wlan_set(h,0x1c,abort,sizeof(abort),0);
 if(!e)e=a04_wlan_set(h,0x17,pm,sizeof(pm),0);
 if(!e)e=a04_wlan_set(h,0x12,bss,sizeof(bss),0);
 if(!e)e=a04_wlan_set(h,0x11,active,sizeof(active),0);
 if(!e)e=a04_wlan_set(h,0x14,remove,sizeof(remove),0);
 if(e)return e;
 t->retired_bss|=1u<<s->local.bss;t->retired_wlan|=1u<<s->local.wlan;
 a04_wlan_zero((uint8_t *)s,sizeof(*s));
 e=a04_wlan_station_setup(s,h,&t->replacement);
 if(e)return e;
 t->pending=0;t->deadline_us=0;t->completed++;return 0;
}
/* Preflight rejects all unsupported work before RF writes/state changes.
 * One real command path owns each pending packet; do not erase an in-flight
 * Ethernet/key TX to simulate cancellation. Caller may retry after completion.
 */
static inline int a04_wlan_teardown_begin(struct a04_wlan_teardown *t,
 struct a04_wlan_station *s,const struct a04_wlan_security *k,
 struct a04_wlan_data_state *d,struct a04_wlan_slots *slots,
 struct a04_wlan_host *h,uint64_t now){
 if(!t || !s || !k || !d || !slots || !h)return EINVAL;
 if(!s->configured || s->failed || s->join.failed || d->failed || h->failed || !h->runtime || !h->ready)return ENETDOWN;
 if(a04_wlan_teardown_secure(s,k))return EOPNOTSUPP;
 if(t->pending || d->pending || s->scan.pending)return EBUSY;
 if(!s->selected)return ENOTCONN;
 if(s->join.phase>4 || !a04_wlan_local_valid(&s->local) || !a04_wlan_mac_valid(s->ap.mac))return EINVAL;
 if(now>UINT64_MAX-100000)return EINVAL;
 unsigned all=0,basic=0;int e=a04_wlan_rate_mask(&s->ap,&all,&basic);if(e)return EINVAL;
 struct a04_wlan_slots next=*slots;
 /* Preserve owner allocations, also quarantine current context explicitly.
  * Never reclaim a slot based merely on a no-response removal submission. */
 next.bss|=1u<<s->local.bss;next.own|=1u<<s->local.own;next.sta|=1u<<s->local.sta;
 next.wtbl|=(1u<<s->local.wlan)|(1u<<s->local.bmc);
 struct a04_wlan_local replacement;
 if(a04_wlan_slots_take(&next,s->local.mac,s->local.permitted_channels,s->local.active_channels,&replacement))return ENOSPC;
 *slots=next;t->replacement=replacement;
 if(s->join.phase<3 && !s->sta_active && !s->sta_pending){
  e=a04_wlan_teardown_finish(t,s,h);return e?EIO:0;
 }
 /* Actual deauth (reason leaving BSS3), peer/local/BSSID, no PMF for open.
  * Same long management TXD as existing station_mgmt_send, plus real MCU
  * TX_DONE request. PID shares data's monotonic allocator, not a reused key. */
 unsigned pid=d->next_pid%127+1;
 a04_wlan_zero(h->tx,54);a04_wmt_put16(h->tx,54);h->tx[2]=26;h->tx[3]=0x88;
 h->tx[4]=(uint8_t)s->local.wlan;h->tx[5]=0xcc;h->tx[7]=(uint8_t)(s->local.own<<2);
 h->tx[8]=12;h->tx[11]=0x80;a04_wmt_put16(h->tx+12,1u<<11);
 h->tx[20]=(uint8_t)pid;h->tx[21]=2;
 a04_wmt_put16(h->tx+26,a04_wlan_basic_rate_code(basic)<<2);
 uint8_t *p=h->tx+28;p[0]=0xc0;
 for(unsigned i=0;i<6;i++){p[4+i]=s->ap.mac[i];p[10+i]=s->local.mac[i];p[16+i]=s->ap.mac[i];}
 a04_wmt_put16(p+24,3);
 e=a04_wlan_send(h,54,1);if(e)return EIO;
 d->next_pid=pid;t->pid=pid;t->pending=1;t->deadline_us=now+100000;
 s->deadline_us=0; /* stop host join timer; no new auth/assoc may be sent */
 return 0;
}
static inline int a04_wlan_teardown_event(struct a04_wlan_teardown *t,
 struct a04_wlan_station *s,struct a04_wlan_host *h,const struct a04_wlan_event *v,uint64_t now){
 if(!t->pending || v->id!=0x0f)return A04_WLAN_UNSUPPORTED;
 if(now>=t->deadline_us)return A04_WLAN_TIMEOUT;
 if(v->length!=16 || v->payload[0]!=t->pid || v->payload[4]!=s->local.wlan)return A04_WLAN_SEQUENCE;
 if(v->payload[1])return A04_WLAN_STATUS;
 return a04_wlan_teardown_finish(t,s,h);
}
/* These async events are uniquely attributed by their actual retired BSS id;
 * they cannot grant/activate a newly allocated context. No payload guessing. */
static inline int a04_wlan_teardown_retired(const struct a04_wlan_teardown *t,const struct a04_wlan_event *v){
 if(v->id==0x0f && v->length==16 && v->payload[4]<18)return !!(t->retired_wlan&(1u<<v->payload[4]));
 unsigned bss;
 if(v->id==0x10 && v->length==16)bss=v->payload[0];
 else if(v->id==0x0c && v->length==8)bss=v->payload[7];
 else return 0;
 return bss<4 && (t->retired_bss&(1u<<bss));
}
#endif
