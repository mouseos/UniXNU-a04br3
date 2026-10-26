/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Serialized, polled live backend. All storage is separately heap-owned.
 * No backup assets, fabricated MAC, scan result, ACK, or association.
 */
#ifndef A04BR3_WLAN_RUNTIME_H
#define A04BR3_WLAN_RUNTIME_H
#include "A04BR3WLANTeardown.h"
#include "A04BR3WLANScanSnapshot.h"
#include "A04BR3WLANPSK4Way.h"
enum { A04_WLAN_DELIVERY_NONE=0,A04_WLAN_DELIVERY_ETHERNET=1,A04_WLAN_DELIVERY_EAPOL=2 };
struct a04_wlan_runtime {
 struct a04_wlan_station station;struct a04_wlan_security security;
 struct a04_wlan_teardown teardown;
 struct a04_psk4way personal;struct a04_psk_prepared credential;
 unsigned personal_admitted,personal_busy;
 unsigned auth_lower,auth_upper,auth_upper_ext;
 uint64_t association_counter,association_generation,personal_tx_cookie,personal_key_cookie;
 struct a04_wlan_data_state data;struct a04_wlan_slots slots;
 struct a04_wlan_ap scan[16];unsigned scan_count,scan_dropped;
 struct a04_wlan_scan_extra scan_extra[16];
 unsigned scan_generation,scan_mode,sleepy_events,sleepy_state;uint64_t scan_started_us,scan_completed_us;
 struct {unsigned id,sequence,bytes;} event_trace[16];unsigned event_trace_count;
 uint8_t calibration[512],mac[6];unsigned calibration_valid,calibration_sent,mac_valid,mac_source;
 unsigned begun,cap_pending,cap_sequence,configured,admin,scan_sequence,port;
 unsigned phase,cap_received,last_event_id,last_event_sequence,last_event_bytes;
 unsigned firmware_product,firmware_version,rf_cal_fail,bb_cal_fail;
 uint64_t cap_deadline;unsigned events,frames,unknown,delivery_kind,delivery_length;
 const uint8_t *delivery;
 int failed;
};
static inline int a04_wlan_runtime_fail(struct a04_wlan_runtime *r,struct a04_wlan_host *h,int e){
 r->failed=e;r->security.authorized=0;r->station.sta_active=0;
 if(r->personal_admitted)a04_psk_fail(&r->personal,e);
 a04_psk_prepared_clear(&r->credential);return a04_wlan_fail(h,e);
}
static inline int a04_wlan_runtime_calibration(struct a04_wlan_runtime *r,const void *p,unsigned n){
 if(!r || !p || n!=512 || r->calibration_sent || r->calibration_valid)return A04_WLAN_ARGUMENT;
 const uint8_t *b=(const uint8_t *)p;
 if(!a04_wlan_mac_valid(b+4) || !a04_wmt_le16(b) || !a04_wmt_le16(b+256) || !b[196])return A04_WLAN_FORMAT;
 /* This first live policy is ONLY the source's null-country/default domain.
  * Other explicit country/custom maps require their native table, not guessing. */
 if(b[10] || b[11] || b[202] || b[203])return A04_WLAN_UNSUPPORTED;
 for(unsigned i=0;i<36;i++)if(b[204+i])return A04_WLAN_UNSUPPORTED;
 for(unsigned i=0;i<512;i++)r->calibration[i]=b[i];
 r->calibration_valid=1;return 0;
}
/* wlan_lib.c post-ready reads WHISR, discards init release counters, resets
 * resource accounting to native runtime allocation. This is NOT an ACK or a
 * claimed return of init pages. Firmware NIC-resource query is a source stub. */
static inline int a04_wlan_runtime_native_boundary(struct a04_wlan_host *h){
 if(!h || !h->ready || !(h->wcir&(1u<<21)) || h->runtime || h->failed)return A04_WLAN_STATE;
 uint32_t status=0;int e=a04_wlan_interrupt_status(h,&status);if(e)return e;
 if(status&1)for(unsigned i=0;i<8;i++){uint32_t discarded=0;e=a04_wlan_read(h,0x130+4*i,&discarded);if(e)return e;}
 h->cpu_free=26;h->tc0_free=13;h->cpu_used=h->cpu_done=h->tc0_used=h->tc0_done=h->ffa=0;h->runtime=1;return 0;
}
static inline int a04_wlan_live_begin(struct a04_wlan_runtime *r,struct a04_wlan_host *h,uint64_t now){
 if(!r || !h || r->begun || r->failed || !r->calibration_valid || now>UINT64_MAX-1000000)return A04_WLAN_STATE;
 r->phase=1;int e=a04_wlan_runtime_native_boundary(h);if(e)return a04_wlan_runtime_fail(r,h,e);
 r->phase=2;r->begun=1;r->cap_sequence=a04_wlan_sequence(h);uint8_t query[116]={0};
 /* wlanQueryNicCapability CID80, query flag0, payload sizeof(event)=116,
  * response EID1 from RX1. Never confuse INIT CMD1 with runtime query80. */
 e=a04_wlan_command(h,0x80,r->cap_sequence,A04_WLAN_CMD_PACKET,query,sizeof(query));
 if(!e)e=a04_wlan_send(h,124,1);
 if(e)return a04_wlan_runtime_fail(r,h,e);
 r->cap_pending=1;r->cap_deadline=now+1000000;r->port=1;return 0;
}
static inline void a04_wlan_default_domain(uint8_t p[56],unsigned passive){
 /* rlm_domain.c last/default groups: original country00, not a guessed JP.
  * Preserve native full domain table, scan request itself limits band2.4.
  * ucBandwidth20M=1 from source enum, unused reserved bytes remain0. */
 static const uint8_t normal[5][5]={{81,1,1,1,13},{115,2,4,36,4},{118,2,4,52,4},{121,2,4,100,12},{125,2,4,149,5}};
 static const uint8_t limited[6][5]={{81,1,1,1,0},{82,1,1,14,0},{115,2,4,36,0},{118,2,4,52,4},{121,2,4,100,12},{125,2,4,149,0}};
 a04_wlan_zero(p,56);a04_wmt_put16(p+2,passive);p[52]=p[53]=1;
 unsigned count=passive?6:5;
 for(unsigned i=0;i<count;i++)for(unsigned j=0;j<5;j++)p[4+8*i+j]=passive?limited[i][j]:normal[i][j];
}
/* rlmDomainGetChnlList + legal normal default domain, not passive-scan table.
 * Explicit active request only after original dynamic board/NIC/config state.
 * Unsupported country/table/custom map never acquires active permission. */
static inline int a04_wlan_runtime_scan_mode(struct a04_wlan_runtime *r,struct a04_wlan_host *h,uint64_t now,unsigned mode){
 if(mode>A04_WLAN_SCAN_ACTIVE)return A04_WLAN_ARGUMENT;
 if(!r || !h || !h->ready || !h->runtime || h->failed || !r->configured || !r->admin || r->failed ||
  !r->cap_received || !r->mac_valid || !r->calibration_valid || !r->calibration_sent ||
  r->station.scan.pending || r->station.selected || now>UINT64_MAX-30000000 || r->scan_generation==UINT32_MAX)return A04_WLAN_STATE;
 if(r->calibration[10] || r->calibration[11] || r->calibration[202] || r->calibration[203])return A04_WLAN_UNSUPPORTED;
 for(unsigned i=0;i<36;i++)if(r->calibration[204+i])return A04_WLAN_UNSUPPORTED;
 uint8_t domain[56];a04_wlan_default_domain(domain,0);unsigned allowed=0;
 for(unsigned i=0;i<6;i++){const uint8_t *entry=domain+4+8*i;if(entry[1]!=1)continue;
  for(unsigned j=0;j<entry[4];j++){unsigned ch=entry[3]+j*entry[2];if(ch>=1 && ch<=13)allowed|=1u<<ch;}}
 allowed&=r->station.local.permitted_channels;
 if(!allowed)return A04_WLAN_UNSUPPORTED;
 uint8_t channels[26];unsigned count=0;for(unsigned ch=1;ch<=13;ch++)if(allowed&(1u<<ch)){channels[2*count]=1;channels[2*count+1]=(uint8_t)ch;count++;}
 struct a04_wlan_scan request;a04_wlan_zero((uint8_t *)&request,sizeof(request));
 request.sequence=(r->scan_sequence+1)&255;request.bss=r->station.local.bss;
 request.active=mode; /* exact native scan enum0 passive,1 active */
 /* Native scnSendScanReqV2 leaves probe count0 (FW default), AIS timings0. */
 request.channel_type=4;request.wire_version=3;request.channels=channels;request.channel_count=count;
 unsigned previous=r->station.local.active_channels;
 /* This temporary permission is scoped to this explicit legal request, not
  * an init-time blanket promotion or a change to join/active-scan policy. */
 if(mode==A04_WLAN_SCAN_ACTIVE)r->station.local.active_channels=allowed;
 r->phase=12;int e=a04_wlan_station_scan(&r->station,h,&request,now,now+30000000);
 r->station.local.active_channels=previous;
 if(!e){r->scan_sequence=request.sequence;r->scan_mode=mode;r->scan_count=r->scan_dropped=0;
  r->scan_generation++;r->scan_started_us=now;r->scan_completed_us=0;}
 return e;
}
static inline int a04_wlan_runtime_scan(struct a04_wlan_runtime *r,struct a04_wlan_host *h,uint64_t now){
 return a04_wlan_runtime_scan_mode(r,h,now,A04_WLAN_SCAN_PASSIVE);
}
static inline int a04_wlan_runtime_configure(struct a04_wlan_runtime *r,struct a04_wlan_host *h,uint64_t now){
 if(!r || !r->mac_valid || !r->calibration_valid || r->configured || r->failed)return A04_WLAN_STATE;
 r->phase=4;const uint8_t *nv=r->calibration;int e=a04_wlan_set(h,0x24,nv+12,40,0);
 /* Original 512 byte variant: preserve original efuse and optional RSSI/edge
  * settings, no PCC extension or generated calibration bytes. */
 if(!e && nv[83]){r->phase=5;uint8_t offset[12]={1};for(unsigned i=0;i<3;i++)offset[1+i]=nv[80+i];offset[4]=nv[84];e=a04_wlan_set(h,0x41,offset,12,0);}
 if(!e && nv[266]){r->phase=6;e=a04_wlan_set(h,0x43,nv+264,2,0);}
 uint8_t domain[56];a04_wlan_default_domain(domain,1);
 if(!e){r->phase=7;e=a04_wlan_set(h,0x0f,domain,56,0);}
 a04_wlan_default_domain(domain,0);if(!e){r->phase=8;e=a04_wlan_set(h,0x0f,domain,56,0);}
 if(!e && nv[198]){r->phase=9;uint8_t edge[4]={nv[199],nv[200],nv[201],0};e=a04_wlan_set(h,0x36,edge,4,0);}
 if(!e){r->phase=10;e=a04_wlan_set(h,0x48,nv,512,0);}
 if(e)return a04_wlan_runtime_fail(r,h,e);
 r->calibration_sent=1;
 r->phase=11;struct a04_wlan_local local;
 /* Default native domain permits1..13; live candidate deliberately restricts
  * host to passive first. No active scan/association permission is invented. */
 e=a04_wlan_slots_take(&r->slots,r->mac,0x3ffe,0,&local);
 if(!e)e=a04_wlan_station_setup(&r->station,h,&local);
 if(e)return a04_wlan_runtime_fail(r,h,e);
 r->configured=1;r->admin=1;return a04_wlan_runtime_scan(r,h,now);
}
static inline int a04_wlan_runtime_event(struct a04_wlan_runtime *r,struct a04_wlan_host *h,
 const struct a04_wlan_event *v,uint64_t now){
 if(r->event_trace_count<16){unsigned i=r->event_trace_count++;r->event_trace[i].id=v->id;r->event_trace[i].sequence=v->sequence;r->event_trace[i].bytes=v->length;}
 r->events++;r->last_event_id=v->id;r->last_event_sequence=v->sequence;r->last_event_bytes=v->length;
 /* EVENT_ID_SLEEPY_INFO7 is not SCAN_DONE0d. Never release ownership or
  * finish a scan from a power notification. */
 if(v->id==7){if(v->length!=4)return A04_WLAN_FORMAT;r->sleepy_events++;r->sleepy_state=v->payload[0];return 0;}
 if(r->cap_pending && v->id==1){
  if(v->sequence!=r->cap_sequence || v->length!=116)return a04_wlan_runtime_fail(r,h,A04_WLAN_SEQUENCE);
  r->phase=3;r->cap_received=1;r->firmware_product=a04_wmt_le16(v->payload);r->firmware_version=a04_wmt_le16(v->payload+2);
  r->rf_cal_fail=v->payload[18];r->bb_cal_fail=v->payload[19];
  const uint8_t *mac=v->payload+8;r->mac_source=1;
  if(!a04_wlan_mac_valid(mac)){mac=r->calibration+4;r->mac_source=2;}
  if(!a04_wlan_mac_valid(mac))return a04_wlan_runtime_fail(r,h,A04_WLAN_FORMAT);
  for(unsigned i=0;i<6;i++)r->mac[i]=mac[i];
  r->mac_valid=1;r->cap_pending=0;return a04_wlan_runtime_configure(r,h,now);
 }
 if(a04_wlan_teardown_retired(&r->teardown,v)){r->unknown++;return 0;}
 if(r->teardown.pending){
  if(v->id==0x0f){int result=a04_wlan_teardown_event(&r->teardown,&r->station,h,v,now);
   if(result)return a04_wlan_runtime_fail(r,h,result);
   r->scan_count=0;r->scan_completed_us=0;return 0;}
  /* Do not advance the canceled auth/STA machine while deauth is on air. */
  if(v->id==0x0c || v->id==0x10){
   if((v->id==0x0c && (v->length!=8 || v->payload[6]!=r->station.local.sta || v->payload[7]!=r->station.local.bss || !a04_wlan_equal(v->payload,r->station.ap.mac,6))) ||
      (v->id==0x10 && (v->length!=16 || v->payload[0]!=r->station.local.bss || v->payload[1]!=r->station.channel.token)))
    return a04_wlan_runtime_fail(r,h,A04_WLAN_SEQUENCE);
   r->unknown++;return 0;
  }
 }
 if(v->id==0x0f && r->data.pending){
  unsigned kind=r->data.eapol_kind;int e=a04_wlan_data_tx_event(h,&r->station,&r->security,&r->data,v,now);
  if(!e && kind && r->personal_admitted)e=a04_psk_tx_done(&r->personal,r->personal_tx_cookie,0,now);
  return e;
 }
 if(v->id==0x24 && r->security.ptk_pending){
  int e=a04_wlan_security_key_event(&r->security,&r->station,v,now);
  if(!e && r->personal_admitted)e=a04_psk_key_done(&r->personal,1,r->personal_key_cookie,0,now);
  return e;
 }
 unsigned was_scan=r->station.scan.pending;
 int e=a04_wlan_station_event(&r->station,h,v,now);
 if(!e && was_scan && r->station.scan.complete && !r->station.selected){r->station.deadline_us=0;r->scan_completed_us=now;}
 if(e==A04_WLAN_UNSUPPORTED){r->unknown++;return 0;}
 return e;
}
static inline int a04_wlan_runtime_step(struct a04_wlan_runtime *r,struct a04_wlan_host *h,uint64_t now,
 a04_wlan_mic_verify verify,void *ctx){
 if(!r || !h || !r->begun || r->failed || h->failed)return A04_WLAN_STATE;
 r->delivery_kind=r->delivery_length=0;r->delivery=0;
 if(r->cap_pending && now>=r->cap_deadline)return a04_wlan_runtime_fail(r,h,A04_WLAN_TIMEOUT);
 if(r->teardown.pending && now>=r->teardown.deadline_us)return a04_wlan_runtime_fail(r,h,A04_WLAN_TIMEOUT);
 uint32_t status=0;int e=a04_wlan_interrupt_status(h,&status);
 if(!e && (status&1))e=a04_wlan_resources(h);
 if(e)return a04_wlan_runtime_fail(r,h,e);
 e=a04_wlan_backend_receive(h,&r->station,&r->security,&r->data,now,r->port);r->port^=1;
 if(e==A04_WLAN_PENDING)return e;
 if(e)return a04_wlan_runtime_fail(r,h,e);
 if((a04_wmt_le16(h->rx+2)&0xe00f)==0xe000){
  struct a04_wlan_event event;e=a04_wlan_event_parse(h->rx,h->rx_length,&event);
  if(!e)e=a04_wlan_runtime_event(r,h,&event,now);
 }else if(r->station.scan.pending){
  struct a04_wlan_ap observation;e=a04_wlan_scan_observe(&r->station,h->rx,h->rx_length,&observation);
  if(!e){unsigned slot=0;for(;slot<r->scan_count;slot++)if(a04_wlan_equal(r->scan[slot].mac,observation.mac,6))break;
   if(slot<16){
    struct a04_wlan_rx_frame frame;e=a04_wlan_rx_frame_parse(h->rx,h->rx_length,&frame);
    if(!e && frame.length>=36 && frame.length-36<=A04_WLAN_SCAN_MAX_IE){
     r->scan[slot]=observation;struct a04_wlan_scan_extra *extra=&r->scan_extra[slot];
     extra->observed_us=now;extra->generation=r->scan_generation;extra->from_probe=frame.data[0]==0x50;extra->ie_bytes=frame.length-36;
     for(unsigned i=0;i<extra->ie_bytes;i++)extra->ies[i]=frame.data[36+i];
     if(slot==r->scan_count)r->scan_count++;
    }else if(!e)e=A04_WLAN_FORMAT;
   }else r->scan_dropped++;
  }else if(e==A04_WLAN_UNSUPPORTED || e==A04_WLAN_FORMAT || e==A04_WLAN_STATUS)e=0;
 }else if(r->teardown.pending){r->unknown++;e=0;
 }else if(r->station.sta_active && (a04_wmt_le16(h->rx+2)>>13)==2){
  struct a04_wlan_ethernet packet;e=a04_wlan_ethernet_rx(&r->station,&r->security,h->rx,h->rx_length,&packet);
  /* Initial-only owner: 802.11 Protected/cipher metadata is distinct from
   * the normal EAPOL-Key encrypted-key-data bit used for GTK KEK unwrap. */
  if(!e && r->personal_admitted && packet.eapol && packet.cipher)
   return a04_wlan_runtime_fail(r,h,A04_WLAN_UNSUPPORTED);
  if(!e){r->frames++;r->delivery=packet.data;r->delivery_length=packet.length;
   r->delivery_kind=packet.eapol?A04_WLAN_DELIVERY_EAPOL:A04_WLAN_DELIVERY_ETHERNET;
   if(packet.eapol && !r->personal_admitted)e=a04_wlan_security_rx(&r->security,packet.data+14,packet.length-14,verify,ctx,now);
  }else if(e==A04_WLAN_REPLAY || e==A04_WLAN_SECURITY || e==A04_WLAN_SEQUENCE || e==A04_WLAN_FORMAT)e=0;
 }else if(r->station.selected){
  struct a04_wlan_rx_frame frame;e=a04_wlan_rx_frame_parse(h->rx,h->rx_length,&frame);
  if(!e && frame.length>=24 && a04_wlan_equal(frame.data+10,r->station.ap.mac,6) && a04_wlan_equal(frame.data+16,r->station.ap.mac,6)){unsigned fc=frame.data[0];
   if(fc==0xb0 && r->station.join.phase==1)e=a04_wlan_station_auth_rx(&r->station,h,frame.data,frame.length,now);
   else if(fc==0x10 && r->station.join.phase==3)e=a04_wlan_station_assoc_rx(&r->station,h,frame.data,frame.length,r->station.secured,now);
   else if(fc==0xa0 || fc==0xc0)e=a04_wlan_disconnect_rx(&r->station,&r->security,frame.data,frame.length);
   else {r->unknown++;e=0;}
  }else{r->unknown++;e=0;}
 }else{r->unknown++;e=0;}
 if(e)return a04_wlan_runtime_fail(r,h,e);
 return 0;
}
#endif
