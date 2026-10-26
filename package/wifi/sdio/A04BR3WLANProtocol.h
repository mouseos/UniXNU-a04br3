/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * MT6630 gen3 nic_cmd_event.h, scan_fsm.c, cnm.c, auth.c, assoc.c,
 * nic_tx.c/nic_rx.c. CPU wire codecs and explicit asynchronous transactions.
 * No automatic retries. Caller dispatches unrelated events and enforces its
 * absolute transaction deadline. No BSS/STA allocation or security handshake
 * is implied by an authentication/association response.
 */
#ifndef A04BR3_WLAN_PROTOCOL_H
#define A04BR3_WLAN_PROTOCOL_H
#include "A04BR3WLANHost.h"
static inline void a04_wlan_zero(uint8_t *p,unsigned n){for(unsigned i=0;i<n;i++)p[i]=0;}
static inline int a04_wlan_equal(const uint8_t *a,const uint8_t *b,unsigned n){
 for(unsigned i=0;i<n;i++)if(a[i]!=b[i])return 0;
 return 1;
}
static inline int a04_wlan_ies(const uint8_t *p,unsigned n){
 if(n && !p)return A04_WLAN_ARGUMENT;
 for(unsigned i=0;i<n;){if(n-i<2 || p[i+1]>n-i-2)return A04_WLAN_FORMAT;i+=2+p[i+1];}
 return 0;
}
struct a04_wlan_event {unsigned id,sequence,length;const uint8_t *payload;};
static inline int a04_wlan_event_parse(const uint8_t *p,unsigned n,struct a04_wlan_event *v){
 if(!p || !v)return A04_WLAN_ARGUMENT;
 if(n<8 || n>4096 || a04_wmt_le16(p)!=n || a04_wmt_le16(p+2)!=0xe000)return A04_WLAN_FORMAT;
 v->id=p[4];v->sequence=p[5];v->payload=p+8;v->length=n-8;return 0;
}
struct a04_wlan_scan {
 unsigned sequence,bss,active,probes,min_dwell,dwell,timeout;
 const uint8_t *ssid,*ie,*channels; /* channel list: band, channel pairs */
 unsigned ssid_length,ie_length,channel_count,channel_type,wire_version;
};
/* Native CMD_SCAN_REQ v1 variable length = OFFSET_OF(aucIE)114+IELen. */
static inline int a04_wlan_scan_encode(const struct a04_wlan_scan *s,uint8_t *p,unsigned cap,unsigned *length){
 if(!s || !p || !length)return A04_WLAN_ARGUMENT;
 *length=0;
 if(s->sequence>255 || s->bss>3 || s->active>1 || s->probes>255 || s->ssid_length>32 ||
    s->ie_length>600 || s->channel_count>32 || s->channel_type>4 ||
    (!s->ssid && s->ssid_length) || (!s->channels && s->channel_count) ||
    !s->timeout || s->timeout>65535 || s->min_dwell>s->dwell || s->dwell>65535 ||
    (s->channel_type==4?!s->channel_count:s->channel_count!=0) || cap<114+s->ie_length)return A04_WLAN_ARGUMENT;
 int e=a04_wlan_ies(s->ie,s->ie_length);if(e)return e;
 for(unsigned i=0;i<s->channel_count;i++){
  unsigned band=s->channels[2*i],ch=s->channels[2*i+1];
  if((band!=1 && band!=2) || !ch || (band==1 && ch>14))return A04_WLAN_ARGUMENT;
 }
 a04_wlan_zero(p,114+s->ie_length);
 p[0]=(uint8_t)s->sequence;p[1]=(uint8_t)s->bss;p[2]=(uint8_t)s->active;p[3]=s->ssid_length?4:1;
 p[4]=(uint8_t)s->ssid_length;p[5]=(uint8_t)s->probes;
 a04_wmt_put16(p+6,s->min_dwell);a04_wmt_put16(p+8,s->dwell);a04_wmt_put16(p+10,s->timeout);
 for(unsigned i=0;i<s->ssid_length;i++)p[12+i]=s->ssid[i];
 p[44]=(uint8_t)s->channel_type;p[45]=(uint8_t)s->channel_count;
 for(unsigned i=0;i<2*s->channel_count;i++)p[48+i]=s->channels[i];
 a04_wmt_put16(p+112,s->ie_length);
 for(unsigned i=0;i<s->ie_length;i++)p[114+i]=s->ie[i];
 *length=114+s->ie_length;return 0;
}
/* Native MT6630 AIS uses CMD_SCAN_REQ_V2 / struct version3,10 SSID
 * entries and54 channels. scnSendScanReqV2 uses aucIE offset494. */
static inline int a04_wlan_scan_v2_encode(const struct a04_wlan_scan *s,uint8_t *p,unsigned cap,unsigned *length){
 if(!s || !p || !length)return A04_WLAN_ARGUMENT;
 *length=0;
 if(s->wire_version!=3 || s->sequence>255 || s->bss>3 || s->active>1 || s->probes>255 ||
  s->ssid_length>32 || (!s->ssid && s->ssid_length) || s->ie_length>600 || s->channel_type>4 ||
  s->channel_count>54 || (!s->channels && s->channel_count) || s->min_dwell>s->dwell ||
  s->dwell>65535 || s->timeout>65535 || (s->channel_type==4?!s->channel_count:s->channel_count!=0) ||
  cap<494+s->ie_length)return A04_WLAN_ARGUMENT;
 int e=a04_wlan_ies(s->ie,s->ie_length);if(e)return e;
 for(unsigned i=0;i<s->channel_count;i++)if(s->channels[2*i]!=1 && s->channels[2*i]!=2)return A04_WLAN_ARGUMENT;
 for(unsigned i=0;i<s->channel_count;i++)if(!s->channels[2*i+1] || (s->channels[2*i]==1 && s->channels[2*i+1]>14))return A04_WLAN_ARGUMENT;
 a04_wlan_zero(p,494+s->ie_length);p[0]=(uint8_t)s->sequence;p[1]=(uint8_t)s->bss;p[2]=(uint8_t)s->active;
 p[3]=(uint8_t)(s->ssid_length?4:(s->active?1:0));p[4]=(uint8_t)!!s->ssid_length;p[5]=(uint8_t)s->probes;p[7]=3;
 a04_wmt_put32(p+8,s->ssid_length);for(unsigned i=0;i<s->ssid_length;i++)p[12+i]=s->ssid[i];
 a04_wmt_put16(p+370,s->min_dwell);a04_wmt_put16(p+372,s->dwell);a04_wmt_put16(p+374,s->timeout);
 for(unsigned i=0;i<6;i++)p[376+i]=255; /* AIS wildcard BSSID, never a fabricated AP */
 p[382]=(uint8_t)s->channel_type;p[383]=(uint8_t)s->channel_count;
 for(unsigned i=0;i<2*s->channel_count;i++)p[384+i]=s->channels[i];
 a04_wmt_put16(p+492,s->ie_length);for(unsigned i=0;i<s->ie_length;i++)p[494+i]=s->ie[i];
 *length=494+s->ie_length;return 0;
}
struct a04_wlan_scan_state {unsigned pending,sequence,bss,complete;int failed;};
static inline int a04_wlan_scan_begin(struct a04_wlan_host *h,struct a04_wlan_scan_state *s,const struct a04_wlan_scan *request){
 if(!h || !s || !request || !h->runtime || s->pending || s->complete || s->failed)return A04_WLAN_STATE;
 uint8_t payload[1094];unsigned n=0;int e=request->wire_version==3?a04_wlan_scan_v2_encode(request,payload,sizeof(payload),&n):a04_wlan_scan_encode(request,payload,sizeof(payload),&n);if(e)return e;
 unsigned seq=a04_wlan_sequence(h);e=a04_wlan_command(h,request->wire_version==3?3:0x1a,seq,A04_WLAN_CMD_PACKET,payload,n);if(e)return e;
 h->tx[6]=1;s->pending=1;s->sequence=request->sequence;s->bss=request->bss;
 e=a04_wlan_send(h,n+8,1);if(e)s->failed=e;return e;
}
/* SCAN_DONE is asynchronous: scan sequence is in payload0, NOT event seq. */
static inline int a04_wlan_scan_done(struct a04_wlan_scan_state *s,const struct a04_wlan_event *v){
 if(!s || !v || !s->pending || s->failed)return A04_WLAN_STATE;
 if(v->id!=0x0d)return A04_WLAN_UNSUPPORTED;
 if(v->length<4 || v->payload[0]!=s->sequence){s->failed=A04_WLAN_SEQUENCE;return s->failed;}
 s->pending=0;s->complete=1;return 0;
}
struct a04_wlan_channel {unsigned bss,token,channel,band,interval;};
static inline int a04_wlan_channel_encode(const struct a04_wlan_channel *c,unsigned abort,uint8_t p[16]){
 if(!c || !p || c->bss>3 || c->token>255 || abort>1 || (c->band!=1 && c->band!=2) ||
    !c->channel || c->channel>196 || (c->band==1 && c->channel>14) || !c->interval || c->interval>10000)return A04_WLAN_ARGUMENT;
 a04_wlan_zero(p,16);p[0]=(uint8_t)c->bss;p[1]=(uint8_t)c->token;p[2]=(uint8_t)abort;
 p[3]=(uint8_t)c->channel;p[5]=(uint8_t)c->band; /* JOIN0, 20MHz0, no SCO0 */
 a04_wmt_put32(p+12,c->interval);return 0;
}
static inline int a04_wlan_channel_send(struct a04_wlan_host *h,const struct a04_wlan_channel *c,unsigned abort){
 if(!h || !h->runtime)return A04_WLAN_STATE;
 uint8_t p[16];int e=a04_wlan_channel_encode(c,abort,p);if(e)return e;
 e=a04_wlan_command(h,0x1c,a04_wlan_sequence(h),A04_WLAN_CMD_PACKET,p,16);if(e)return e;
 h->tx[6]=1;return a04_wlan_send(h,24,1);
}
static inline int a04_wlan_channel_grant(const struct a04_wlan_channel *c,const struct a04_wlan_event *v,unsigned *interval){
 if(!c || !v || !interval)return A04_WLAN_ARGUMENT;
 *interval=0;if(v->id!=0x10)return A04_WLAN_UNSUPPORTED;
 if(v->length!=16)return A04_WLAN_FORMAT;
 const uint8_t *p=v->payload;
 if(p[0]!=c->bss || p[1]!=c->token || p[3]!=c->channel || p[5]!=c->band || p[4] || p[6] || p[7] || p[8] || p[9])return A04_WLAN_SEQUENCE;
 if(p[2])return A04_WLAN_STATUS;
 unsigned n=a04_wmt_get32(p+12);if(!n || n>c->interval)return A04_WLAN_FORMAT;
 *interval=n;return 0;
}
/* nicRxFillRFB: descriptor16 + groups4,1,2,3 + optional offset2. */
struct a04_wlan_rx_frame {const uint8_t *data;unsigned length,wlan,channel;};
static inline int a04_wlan_rx_frame_parse(const uint8_t *p,unsigned n,struct a04_wlan_rx_frame *v){
 if(!p || !v)return A04_WLAN_ARGUMENT;
 if(n<16 || n>4096 || a04_wmt_le16(p)!=n)return A04_WLAN_FORMAT;
 unsigned kind=a04_wmt_le16(p+2),groups=(kind>>9)&15;
 if((kind>>13)!=2 && (kind&0xe00f)!=0xe001)return A04_WLAN_UNSUPPORTED;
 if((p[6]&0x80) || (a04_wmt_le16(p+10)&0x0ffe))return A04_WLAN_STATUS;
 unsigned off=16+((groups&8)?16:0)+((groups&1)?16:0)+((groups&2)?8:0)+((groups&4)?24:0)+((p[6]&0x40)?2:0);
 if(off>n || (p[6]&63)>n-off)return A04_WLAN_FORMAT;
 v->data=p+off;v->length=n-off;v->wlan=p[8];v->channel=p[5];return 0;
}
/* 28-byte long MAC descriptor for a caller-owned, firmware-configured STA.
 * TC4 -> MCU port1/Q1, unicast management, explicit bounded HW attempts1.
 * No fabricated WLAN/OwnMAC indices: caller must allocate them via BSS/STA.
 */
static inline int a04_wlan_management_send(struct a04_wlan_host *h,unsigned wlan,unsigned own,
 const uint8_t *frame,unsigned n){
 if(!h || !h->runtime)return A04_WLAN_STATE;
 if(!frame || n<24 || n>1532 || wlan>255 || own>63 || (frame[0]&15) || (frame[1]&0x43))return A04_WLAN_ARGUMENT;
 a04_wlan_zero(h->tx,28);a04_wmt_put16(h->tx,n+28);h->tx[2]=26;h->tx[3]=0x88;
 h->tx[4]=(uint8_t)wlan;h->tx[5]=0xcc;h->tx[7]=(uint8_t)(own<<2);
 h->tx[8]=frame[0]>>4;a04_wmt_put16(h->tx+12,1u<<11);
 for(unsigned i=0;i<n;i++)h->tx[28+i]=frame[i];
 return a04_wlan_send(h,n+28,1);
}
/* Open-system host SAA wire path only: WPA/EAPOL/key install is a later step.
 * AID_received does not mean an authorized network interface or secure link. */
struct a04_wlan_open_join {uint8_t client[6],ap[6];unsigned phase,aid;int failed;};
static inline int a04_wlan_join_fail(struct a04_wlan_open_join *s,int e){s->failed=e;return e;}
static inline void a04_wlan_mgmt_header(struct a04_wlan_open_join *s,uint8_t *p,unsigned fc){
 a04_wlan_zero(p,24);p[0]=(uint8_t)fc;
 for(unsigned i=0;i<6;i++){p[4+i]=s->ap[i];p[10+i]=s->client[i];p[16+i]=s->ap[i];}
}
static inline int a04_wlan_auth_encode(struct a04_wlan_open_join *s,uint8_t *p,unsigned cap,unsigned *n){
 if(!s || !p || !n || cap<30)return A04_WLAN_ARGUMENT;
 *n=0;if(s->phase || s->failed)return A04_WLAN_STATE;
 if((s->client[0]&1) || (s->ap[0]&1))return A04_WLAN_ARGUMENT;
 a04_wlan_mgmt_header(s,p,0xb0);a04_wlan_zero(p+24,6);a04_wmt_put16(p+26,1);*n=30;
 return 0;
}
static inline int a04_wlan_join_response_header(struct a04_wlan_open_join *s,const uint8_t *p,unsigned n,unsigned fc){
 if(!p || n<30 || p[0]!=fc || (p[1]&0x47) || (a04_wmt_le16(p+22)&15) ||
    !a04_wlan_equal(p+4,s->client,6) || !a04_wlan_equal(p+10,s->ap,6) || !a04_wlan_equal(p+16,s->ap,6))return A04_WLAN_SEQUENCE;
 return 0;
}
static inline int a04_wlan_auth_response(struct a04_wlan_open_join *s,const uint8_t *p,unsigned n){
 if(!s || s->phase!=1 || s->failed)return A04_WLAN_STATE;
 int e=a04_wlan_join_response_header(s,p,n,0xb0);if(e)return a04_wlan_join_fail(s,e);
 if(a04_wmt_le16(p+24) || a04_wmt_le16(p+26)!=2)return a04_wlan_join_fail(s,A04_WLAN_SEQUENCE);
 if(a04_wmt_le16(p+28))return a04_wlan_join_fail(s,A04_WLAN_STATUS);
 e=a04_wlan_ies(p+30,n-30);if(e)return a04_wlan_join_fail(s,e);
 s->phase=2;return 0;
}
static inline int a04_wlan_assoc_encode(struct a04_wlan_open_join *s,unsigned capability,unsigned listen,
 const uint8_t *ssid,unsigned ssidlen,const uint8_t *rates,unsigned nrates,uint8_t *p,unsigned cap,unsigned *n){
 if(!s || !p || !n)return A04_WLAN_ARGUMENT;
 *n=0;if(s->phase!=2 || s->failed)return A04_WLAN_STATE;
 if(!ssid || !ssidlen || ssidlen>32 || !rates || !nrates || nrates>12 || capability>65535 ||
    (capability&0x10) || !listen || listen>65535 || cap<32+ssidlen+nrates+(nrates>8?2:0))return A04_WLAN_ARGUMENT;
 a04_wlan_mgmt_header(s,p,0);a04_wmt_put16(p+24,capability);a04_wmt_put16(p+26,listen);
 unsigned k=28;p[k++]=0;p[k++]=(uint8_t)ssidlen;
 for(unsigned i=0;i<ssidlen;i++)p[k++]=ssid[i];
 p[k++]=1;p[k++]=(uint8_t)(nrates>8?8:nrates);
 for(unsigned i=0;i<nrates;i++){
  if(i==8){p[k++]=50;p[k++]=(uint8_t)(nrates-8);}
  p[k++]=rates[i];
 }
 *n=k;return 0;
}
static inline int a04_wlan_assoc_response(struct a04_wlan_open_join *s,const uint8_t *p,unsigned n){
 if(!s || s->phase!=3 || s->failed)return A04_WLAN_STATE;
 int e=a04_wlan_join_response_header(s,p,n,0x10);if(e)return a04_wlan_join_fail(s,e);
 if(a04_wmt_le16(p+26))return a04_wlan_join_fail(s,A04_WLAN_STATUS);
 unsigned aid=a04_wmt_le16(p+28)&0x3fff;if(!aid || aid>2007)return a04_wlan_join_fail(s,A04_WLAN_FORMAT);
 e=a04_wlan_ies(p+30,n-30);if(e)return a04_wlan_join_fail(s,e);
 s->aid=aid;s->phase=4;return 0;
}
/* Commit phase only after a successful FIFO transfer. Errors poison host/join. */
static inline int a04_wlan_join_send(struct a04_wlan_host *h,struct a04_wlan_open_join *s,
 unsigned wlan,unsigned own,const uint8_t *frame,unsigned n){
 if(!s || s->failed || (s->phase!=0 && s->phase!=2))return A04_WLAN_STATE;
 if(!frame || n<30 || frame[0]!=(s->phase==0?0xb0:0))return A04_WLAN_ARGUMENT;
 int e=a04_wlan_management_send(h,wlan,own,frame,n);if(e)return a04_wlan_join_fail(s,e);
 s->phase++;return 0;
}
#endif
