/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Next-phase MT6630 station backend. Not included by frozen SDIO.cpp.
 * Source: gen3 nic.c, cnm_mem.c, nic_cmd_event.h. Explicit legacy20MHz subset.
 * All indices must be reserved by the single device-wide resource owner.
 * Channel masks and real MAC must come from native board/regulatory setup;
 * no world-domain/country/MAC/calibration data is synthesized here.
 * A successful set command means queued, NOT an invented firmware ACK.
 */
#ifndef A04BR3_WLAN_STATION_H
#define A04BR3_WLAN_STATION_H
#include "A04BR3WLANProtocol.h"
enum { A04_WLAN_PENDING=89,A04_WLAN_REPLAY=90,A04_WLAN_SECURITY=91 };
struct a04_wlan_local {unsigned bss,own,bmc,sta,wlan,permitted_channels,active_channels;uint8_t mac[6];};
struct a04_wlan_ap {
 uint8_t mac[6],ssid[32],rates[12],rsn[257];
 unsigned ssid_length,rate_count,rsn_length,channel,band,capability,beacon,rcpi_valid,rcpi;
};
struct a04_wlan_station {
 struct a04_wlan_local local;struct a04_wlan_ap ap;
 struct a04_wlan_scan_state scan;struct a04_wlan_channel channel;
 struct a04_wlan_open_join join;
 unsigned configured,selected,secured,channel_pending,grant_ms,sta_pending,sta_sequence,sta_active;
 uint64_t deadline_us;int failed;
};
static inline int a04_wlan_mac_valid(const uint8_t *p){
 unsigned any=0;if(!p || (p[0]&1))return 0;
 for(unsigned i=0;i<6;i++)any|=p[i];
 return any!=0;
}
static inline int a04_wlan_local_valid(const struct a04_wlan_local *l){
 return l && l->bss<4 && l->own<4 && l->sta<16 && l->wlan<18 &&
  l->bmc>=19 && l->bmc<=27 && l->bmc!=l->wlan && a04_wlan_mac_valid(l->mac) &&
  l->permitted_channels && !(l->permitted_channels&~0x7ffeu) && !(l->active_channels&~l->permitted_channels);
}
/* Import/retain these maps in the device-wide allocator; never zero a new
 * private pool while another firmware context is live. On partial setup or
 * poisoned transport, allocations remain quarantined until explicit teardown
 * or externally verified radio reset. No automatic free/reconnect here. */
struct a04_wlan_slots {unsigned bss,own,sta;uint32_t wtbl;};
static inline int a04_wlan_slots_take(struct a04_wlan_slots *slots,const uint8_t mac[6],
 unsigned permitted,unsigned active,struct a04_wlan_local *out){
 if(!slots || !out || !a04_wlan_mac_valid(mac) || !permitted || (permitted&~0x7ffeu) || (active&~permitted))return A04_WLAN_ARGUMENT;
 unsigned bss,own,sta,wlan,bmc;
 for(bss=0;bss<4 && (slots->bss&(1u<<bss));bss++){}
 for(own=0;own<4 && (slots->own&(1u<<own));own++){}
 for(sta=0;sta<16 && (slots->sta&(1u<<sta));sta++){}
 for(wlan=0;wlan<18 && (slots->wtbl&(1u<<wlan));wlan++){}
 for(bmc=19;bmc<=27 && (slots->wtbl&(1u<<bmc));bmc++){}
 if(bss==4 || own==4 || sta==16 || wlan==18 || bmc==28)return A04_WLAN_STATE;
 out->bss=bss;out->own=own;out->sta=sta;out->wlan=wlan;out->bmc=bmc;
 out->permitted_channels=permitted;out->active_channels=active;
 for(unsigned i=0;i<6;i++)out->mac[i]=mac[i];
 slots->bss|=1u<<bss;slots->own|=1u<<own;slots->sta|=1u<<sta;slots->wtbl|=(1u<<wlan)|(1u<<bmc);
 return 0;
}
/* Source physical layout uses natural alignment: BSS88 / RLM20 / STA124.
 * Serialize offsets, never cast unaligned firmware payload to host structs. */
static inline int a04_wlan_activate_encode(const struct a04_wlan_local *l,unsigned active,uint8_t p[12]){
 if(!a04_wlan_local_valid(l) || !p || active>1)return A04_WLAN_ARGUMENT;
 a04_wlan_zero(p,12);p[0]=(uint8_t)l->bss;p[1]=(uint8_t)active;p[3]=(uint8_t)l->own;
 for(unsigned i=0;i<6;i++)p[4+i]=l->mac[i];
 p[10]=(uint8_t)l->bmc;return 0; /* AIS network0 */
}
static inline int a04_wlan_rate_mask(const struct a04_wlan_ap *a,unsigned *all,unsigned *basic){
 static const uint8_t rates[12]={2,4,11,22,12,18,24,36,48,72,96,108};
 if(!a || !all || !basic || !a->rate_count || a->rate_count>12)return A04_WLAN_ARGUMENT;
 *all=*basic=0;
 for(unsigned i=0;i<a->rate_count;i++){
  unsigned j;for(j=0;j<12;j++)if((a->rates[i]&127)==rates[j])break;
  if(j==12)return A04_WLAN_UNSUPPORTED;
  *all|=1u<<j;if(a->rates[i]&128)*basic|=1u<<j;
 }
 return *basic?0:A04_WLAN_FORMAT;
}
static inline unsigned a04_wlan_basic_rate_code(unsigned basic){
 static const uint8_t codes[12]={0,1,2,3,0x4b,0x4f,0x4a,0x4e,0x49,0x4d,0x48,0x4c};
 for(unsigned i=0;i<12;i++)if(basic&(1u<<i))return codes[i];
 return 0; /* caller rate-mask validation requires a basic rate */
}
static inline int a04_wlan_bss_encode(const struct a04_wlan_local *l,const struct a04_wlan_ap *a,
 unsigned connected,unsigned secured,uint8_t p[88]){
 if(!a04_wlan_local_valid(l) || !a || !p || connected>1 || secured>1 || !a04_wlan_mac_valid(a->mac) ||
  a->ssid_length>32 || a->band!=1 || !a->channel || a->channel>14)return A04_WLAN_ARGUMENT;
 unsigned all=0,basic=0;int e=a04_wlan_rate_mask(a,&all,&basic);if(e)return e;
 a04_wlan_zero(p,88);p[0]=(uint8_t)l->bss;p[1]=(uint8_t)!connected;p[3]=(uint8_t)a->ssid_length;
 for(unsigned i=0;i<a->ssid_length;i++)p[4+i]=a->ssid[i];
 for(unsigned i=0;i<6;i++)p[36+i]=a->mac[i];
 a04_wmt_put16(p+44,all);a04_wmt_put16(p+46,basic);p[48]=(uint8_t)l->sta;
 /* Native nicTxUpdateBss/StaDefaultRate selects lowest basic rate index.
  * ERP basic PHY1; open AUTH1/encryption-disabled1 or WPA2PSK8/CCMPabsent7. */
 a04_wmt_put16(p+50,a04_wlan_basic_rate_code(basic));
 p[52]=1;p[53]=(uint8_t)(secured?8:1);p[54]=(uint8_t)(secured?7:1);
 p[55]=3;p[58]=(uint8_t)l->bmc;
 p[68]=(uint8_t)l->bss;p[69]=1;p[70]=(uint8_t)a->channel;
 p[82]=(uint8_t)!!(a->capability&0x400);return 0;
}
static inline int a04_wlan_sta_encode(const struct a04_wlan_local *l,const struct a04_wlan_ap *a,
 unsigned state,unsigned aid,unsigned listen,uint8_t p[124]){
 if(!a04_wlan_local_valid(l) || !a || !p || !a04_wlan_mac_valid(a->mac) || state>2 ||
  (state==2?(!aid || aid>2007):aid!=0) || !listen || listen>65535 || a->band!=1)return A04_WLAN_ARGUMENT;
 unsigned all=0,basic=0;int e=a04_wlan_rate_mask(a,&all,&basic);if(e)return e;
 a04_wlan_zero(p,124);p[0]=(uint8_t)l->sta;p[1]=0x41; /* LEGACY bit0 | AP bit6 */
 for(unsigned i=0;i<6;i++)p[2+i]=a->mac[i];
 a04_wmt_put16(p+8,aid);a04_wmt_put16(p+10,listen);p[12]=(uint8_t)l->bss;p[13]=3;
 a04_wmt_put16(p+14,all);a04_wmt_put16(p+16,basic);p[20]=(uint8_t)state;
 p[51]=(uint8_t)(state==2);p[54]=(uint8_t)l->wlan;p[55]=(uint8_t)l->bmc;
 a04_wmt_put16(p+74,a04_wlan_basic_rate_code(basic));
 p[68]=3; /* RTS_POLICY_LEGACY; QoS, HT/VHT/AMPDU disabled in subset. */
 return 0;
}
static inline int a04_wlan_set(struct a04_wlan_host *h,unsigned cid,const uint8_t *p,unsigned n,unsigned *seq){
 if(!h || !h->runtime || !h->ready || h->failed)return A04_WLAN_STATE;
 unsigned s=a04_wlan_sequence(h);int e=a04_wlan_command(h,cid,s,A04_WLAN_CMD_PACKET,p,n);if(e)return e;
 h->tx[6]=1;e=a04_wlan_send(h,n+8,1);if(!e && seq)*seq=s;return e;
}
static inline int a04_wlan_station_fail(struct a04_wlan_station *s,struct a04_wlan_host *h,int e){
 s->failed=e;return a04_wlan_fail(h,e);
}
static inline int a04_wlan_station_setup(struct a04_wlan_station *s,struct a04_wlan_host *h,const struct a04_wlan_local *l){
 if(!s || !h || s->configured || s->failed || !a04_wlan_local_valid(l))return A04_WLAN_STATE;
 uint8_t active[12],basic[8]={0},filter[4];int e=a04_wlan_activate_encode(l,1,active);if(e)return e;
 /* Native ucNative80211=0, checksum offload0. RX directed+broadcast+multicast. */
 a04_wmt_put32(filter,13);
 e=a04_wlan_set(h,2,basic,8,0);if(!e)e=a04_wlan_set(h,0x11,active,12,0);
 if(!e)e=a04_wlan_set(h,0x0a,filter,4,0);
 if(e)return a04_wlan_station_fail(s,h,e);
 s->local=*l;s->configured=1;return 0;
}
/* Parse actual beacon/probe response; results are observations, not scan-done.
 * Caller owns bounded result storage. No invented RSSI/country/channel policy. */
static inline int a04_wlan_ap_parse(const uint8_t *p,unsigned n,unsigned channel,struct a04_wlan_ap *a){
 if(!p || !a)return A04_WLAN_ARGUMENT;
 if(n<36 || n>2304 || (p[0]!=0x80 && p[0]!=0x50) || (p[1]&0x47) ||
  (a04_wmt_le16(p+22)&15) || !a04_wlan_mac_valid(p+16) || !a04_wlan_equal(p+10,p+16,6) ||
  !channel || channel>14)return A04_WLAN_FORMAT;
 if(a04_wlan_ies(p+36,n-36))return A04_WLAN_FORMAT;
 a04_wlan_zero((uint8_t *)a,sizeof(*a));a->channel=channel;a->band=1;
 a->beacon=a04_wmt_le16(p+32);a->capability=a04_wmt_le16(p+34);
 if(!(a->capability&1) || (a->capability&2) || !a->beacon)return A04_WLAN_UNSUPPORTED;
 for(unsigned i=0;i<6;i++)a->mac[i]=p[16+i];
 unsigned ssid=0,rates=0,ds=0;
 for(unsigned i=36;i<n;i+=2+p[i+1]){
  unsigned size=p[i+1];const uint8_t *v=p+i+2;
  if(p[i]==0){if(ssid++ || size>32)return A04_WLAN_FORMAT;a->ssid_length=size;for(unsigned j=0;j<size;j++)a->ssid[j]=v[j];}
  if(p[i]==1 || p[i]==50){
   if(!size || a->rate_count+size>12 || (p[i]==1 && (rates++ || size>8)))return A04_WLAN_FORMAT;
   for(unsigned j=0;j<size;j++)a->rates[a->rate_count++]=v[j];
  }
  if(p[i]==3){if(ds++ || size!=1 || v[0]!=channel)return A04_WLAN_FORMAT;}
  if(p[i]==48){if(a->rsn_length)return A04_WLAN_FORMAT;a->rsn_length=size+2;for(unsigned j=0;j<size+2;j++)a->rsn[j]=p[i+j];}
 }
 unsigned all=0,basic_rates=0;
 if(!ssid || !rates || a04_wlan_rate_mask(a,&all,&basic_rates))return A04_WLAN_FORMAT;
 return 0;
}
static inline int a04_wlan_scan_observe(const struct a04_wlan_station *s,const uint8_t *p,unsigned n,struct a04_wlan_ap *a){
 if(!s || !p || !a || !s->configured || !s->scan.pending || s->failed)return A04_WLAN_STATE;
 struct a04_wlan_rx_frame frame;int e=a04_wlan_rx_frame_parse(p,n,&frame);if(e)return e;
 if(!frame.channel || frame.channel>14 || !(s->local.permitted_channels&(1u<<frame.channel)))return A04_WLAN_UNSUPPORTED;
 if(frame.length<36)return A04_WLAN_FORMAT;
 if(!(frame.data[4]&1) && !a04_wlan_equal(frame.data+4,s->local.mac,6))return A04_WLAN_UNSUPPORTED;
 e=a04_wlan_ap_parse(frame.data,frame.length,frame.channel,a);if(e)return e;
 unsigned groups=(a04_wmt_le16(p+2)>>9)&15;
 if(groups&4){
  unsigned group3=16+((groups&8)?16:0)+((groups&1)?16:0)+((groups&2)?8:0);
  unsigned rcpi=p[group3+9]; /* vector2 bits8..15, HAL_RX_STATUS_GET_RCPI */
  if(rcpi<=220){a->rcpi_valid=1;a->rcpi=rcpi;} /* 255 means unavailable */
 }
 return 0;
}
static inline int a04_wlan_station_scan(struct a04_wlan_station *s,struct a04_wlan_host *h,
 const struct a04_wlan_scan *request,uint64_t now,uint64_t deadline){
 if(!s || !s->configured || s->failed || !request || request->bss!=s->local.bss ||
  s->selected || deadline<=now || deadline-now>30000000)return A04_WLAN_STATE;
 if(request->channel_type!=4 || !request->channels || !request->channel_count || request->channel_count>14)return A04_WLAN_ARGUMENT;
 for(unsigned i=0;i<request->channel_count;i++){
  unsigned band=request->channels[2*i],ch=request->channels[2*i+1];
  unsigned mask=request->active?s->local.active_channels:s->local.permitted_channels;
  if(band!=1 || !ch || ch>14 || !(mask&(1u<<ch)))return A04_WLAN_ARGUMENT;
 }
 if(s->scan.complete){
  if(s->scan.sequence==request->sequence)return A04_WLAN_SEQUENCE;
  a04_wlan_zero((uint8_t *)&s->scan,sizeof(s->scan)); /* explicitly new scan, not a retry */
 }
 int e=a04_wlan_scan_begin(h,&s->scan,request);if(e)return e;
 s->deadline_us=deadline;return 0;
}
static inline int a04_wlan_station_select(struct a04_wlan_station *s,struct a04_wlan_host *h,
 const struct a04_wlan_ap *a,unsigned secured,unsigned token,unsigned interval,uint64_t now,uint64_t deadline){
 if(!s || !h || !a || !s->configured || !s->scan.complete || s->selected || s->failed ||
  deadline<=now || deadline-now>10000000 || !interval || interval>10000 || token>255)return A04_WLAN_STATE;
 if(!a->channel || a->channel>14 || !(s->local.active_channels&(1u<<a->channel)))return A04_WLAN_ARGUMENT;
 if(secured>1 || (!!(a->capability&16)!=!!secured) || (secured && !a->rsn_length))return A04_WLAN_SECURITY;
 uint8_t bss[88],sta[124];int e=a04_wlan_bss_encode(&s->local,a,0,secured,bss);
 if(!e)e=a04_wlan_sta_encode(&s->local,a,0,0,10,sta);
 if(e)return e;
 e=a04_wlan_set(h,0x12,bss,88,0);if(!e)e=a04_wlan_set(h,0x13,sta,124,0);
 if(e)return a04_wlan_station_fail(s,h,e);
 s->ap=*a;s->selected=1;s->secured=secured;s->channel.bss=s->local.bss;s->channel.token=token;
 s->channel.channel=a->channel;s->channel.band=a->band;s->channel.interval=interval;
 e=a04_wlan_channel_send(h,&s->channel,0);if(e)return a04_wlan_station_fail(s,h,e);
 s->channel_pending=1;s->deadline_us=deadline;
 for(unsigned i=0;i<6;i++){s->join.client[i]=s->local.mac[i];s->join.ap[i]=a->mac[i];}
 return 0;
}
static inline int a04_wlan_station_event(struct a04_wlan_station *s,struct a04_wlan_host *h,
 const struct a04_wlan_event *v,uint64_t now){
 if(!s || !h || !v || s->failed)return A04_WLAN_STATE;
 if(s->deadline_us && now>=s->deadline_us)return a04_wlan_station_fail(s,h,A04_WLAN_TIMEOUT);
 int e=A04_WLAN_UNSUPPORTED;
 if(v->id==0x0d && s->scan.pending)e=a04_wlan_scan_done(&s->scan,v);
 else if(v->id==0x10 && s->channel_pending){
  e=a04_wlan_channel_grant(&s->channel,v,&s->grant_ms);
  if(!e){s->channel_pending=0;uint64_t until=now+(uint64_t)s->grant_ms*1000;
   if(until<now)return a04_wlan_station_fail(s,h,A04_WLAN_ARGUMENT);
   if(until<s->deadline_us)s->deadline_us=until;
  }
 }else if(v->id==0x0c && s->sta_pending){
  if(v->length!=8 || v->sequence!=s->sta_sequence || v->payload[6]!=s->local.sta ||
   v->payload[7]!=s->local.bss || !a04_wlan_equal(v->payload,s->ap.mac,6))e=A04_WLAN_SEQUENCE;
  else{s->sta_pending=0;s->sta_active=1;e=a04_wlan_channel_send(h,&s->channel,1);
   if(!e)s->deadline_us=0;}
 }
 if(e && e!=A04_WLAN_UNSUPPORTED)return a04_wlan_station_fail(s,h,e);
 return e;
}
/* Native management TX uses an explicit lowest basic fixed rate, unlike
 * auto-rate Ethernet. Implement here without changing the frozen codec. */
static inline int a04_wlan_station_mgmt_send(struct a04_wlan_station *s,struct a04_wlan_host *h,
 const uint8_t *p,unsigned n){
 if(!s || !h || !p || !h->runtime || s->failed || n<30 || n>384 ||
  (s->join.phase!=0 && s->join.phase!=2) || p[0]!=(s->join.phase==0?0xb0:0) || (p[1]&0x47) ||
  !a04_wlan_equal(p+4,s->ap.mac,6) || !a04_wlan_equal(p+10,s->local.mac,6) || !a04_wlan_equal(p+16,s->ap.mac,6))return A04_WLAN_ARGUMENT;
 unsigned all=0,basic=0;int e=a04_wlan_rate_mask(&s->ap,&all,&basic);if(e)return e;
 a04_wlan_zero(h->tx,28);a04_wmt_put16(h->tx,n+28);h->tx[2]=26;h->tx[3]=0x88;
 h->tx[4]=(uint8_t)s->local.wlan;h->tx[5]=0xcc;h->tx[7]=(uint8_t)(s->local.own<<2);
 h->tx[8]=p[0]>>4;h->tx[11]=0x80;a04_wmt_put16(h->tx+12,1u<<11);
 a04_wmt_put16(h->tx+26,a04_wlan_basic_rate_code(basic)<<2);
 for(unsigned i=0;i<n;i++)h->tx[28+i]=p[i];
 e=a04_wlan_send(h,n+28,1);if(!e)s->join.phase++;
 return e;
}
/* Auth request is only sent after a real channel-grant event. No SW retries. */
static inline int a04_wlan_station_auth(struct a04_wlan_station *s,struct a04_wlan_host *h,uint64_t now){
 if(!s || !h || !s->selected || !s->grant_ms || s->channel_pending || s->failed || now>=s->deadline_us)return A04_WLAN_STATE;
 uint8_t p[30];unsigned n=0;int e=a04_wlan_auth_encode(&s->join,p,sizeof(p),&n);
 if(!e)e=a04_wlan_station_mgmt_send(s,h,p,n);
 return e?a04_wlan_station_fail(s,h,e):0;
}
/* Native cnmStaRecChangeState1->2 is host-only: no synthetic STA update ACK. */
static inline int a04_wlan_station_auth_rx(struct a04_wlan_station *s,struct a04_wlan_host *h,
 const uint8_t *p,unsigned n,uint64_t now){
 if(!s || !h || s->failed || now>=s->deadline_us)return A04_WLAN_STATE;
 int e=a04_wlan_auth_response(&s->join,p,n);return e?a04_wlan_station_fail(s,h,e):0;
}
static inline int a04_wlan_station_assoc(struct a04_wlan_station *s,struct a04_wlan_host *h,
 unsigned capability,unsigned listen,const uint8_t *security_ie,unsigned security_length,uint64_t now){
 if(!s || !h || s->failed || now>=s->deadline_us || security_length>257 || (!!security_length!=!!s->secured) ||
  (security_length && (!security_ie || security_ie[0]!=48 || security_length<2 || security_ie[1]+2u!=security_length)) ||
  a04_wlan_ies(security_ie,security_length) || (!!(capability&16)!=!!security_length))return A04_WLAN_ARGUMENT;
 uint8_t p[384];unsigned n=0;int e=a04_wlan_assoc_encode(&s->join,capability&~16u,listen,
  s->ap.ssid,s->ap.ssid_length,s->ap.rates,s->ap.rate_count,p,sizeof(p),&n);if(e)return e;
 a04_wmt_put16(p+24,capability);
 for(unsigned i=0;i<security_length;i++)p[n+i]=security_ie[i];
 e=a04_wlan_station_mgmt_send(s,h,p,n+security_length);
 return e?a04_wlan_station_fail(s,h,e):0;
}
static inline int a04_wlan_station_assoc_rx(struct a04_wlan_station *s,struct a04_wlan_host *h,
 const uint8_t *p,unsigned n,unsigned secured,uint64_t now){
 if(!s || !h || s->failed || secured!=s->secured || now>=s->deadline_us)return A04_WLAN_STATE;
 int e=a04_wlan_assoc_response(&s->join,p,n);if(e)return a04_wlan_station_fail(s,h,e);
 uint8_t bss[88],sta[124];e=a04_wlan_bss_encode(&s->local,&s->ap,1,secured,bss);
 if(!e)e=a04_wlan_sta_encode(&s->local,&s->ap,2,s->join.aid,10,sta);
 if(!e)e=a04_wlan_set(h,0x12,bss,88,0);
 if(!e)e=a04_wlan_set(h,0x13,sta,124,&s->sta_sequence);
 if(e)return a04_wlan_station_fail(s,h,e);
 s->sta_pending=1;return 0; /* wait ACTIVATE_STA_REC, never assume success */
}
#endif
