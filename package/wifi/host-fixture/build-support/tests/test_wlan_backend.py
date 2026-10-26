# SPDX-License-Identifier: MIT
"""New backend only; frozen CPP/codec and parent-owned boot helpers untouched."""
from pathlib import Path
import re
import unittest
import test_wmt_protocol as codecs
from test_wlan_host import FAKE

NATIVE_FIXTURE = Path(__file__).resolve().parent/'fixtures/mt6630_native/nic_cmd_event_fixture.h'
SETUP = FAKE + r'''
#include "A04BR3WLANData.h"
static inline void local_profile(struct a04_wlan_local *l){
 memset(l,0,sizeof(*l));l->bss=1;l->own=1;l->sta=2;l->wlan=3;l->bmc=19;l->permitted_channels=l->active_channels=0x0ffe;
 const uint8_t mac[6]={2,1,2,3,4,5};memcpy(l->mac,mac,6);
}
static inline void ap_profile(struct a04_wlan_ap *a){
 memset(a,0,sizeof(*a));const uint8_t mac[6]={2,6,7,8,9,10};memcpy(a->mac,mac,6);
 memcpy(a->ssid,"test",4);a->ssid_length=4;a->channel=6;a->band=1;a->beacon=100;a->capability=0x401;
 const uint8_t rates[4]={0x82,0x84,0x8b,0x96};memcpy(a->rates,rates,4);a->rate_count=4;
}
static inline void response(uint8_t *p,const struct a04_wlan_station *s,unsigned fc){
 memset(p,0,30);p[0]=(uint8_t)fc;memcpy(p+4,s->local.mac,6);memcpy(p+10,s->ap.mac,6);memcpy(p+16,s->ap.mac,6);
}
'''

class WLANBackend(unittest.TestCase):
    run_c = codecs.WMTProtocol.run_c

    def test_layout_matches_extracted_primary_structs(self):
        text=NATIVE_FIXTURE.read_text()
        decl='typedef uint8_t UINT_8;typedef uint16_t UINT_16;typedef uint32_t UINT_32;\n#define MAC_ADDR_LEN 6\n'
        for name in ('CMD_SET_BSS_RLM_PARAM_T','CMD_SET_BSS_INFO','CMD_UPDATE_STA_RECORD_T','CMD_802_11_KEY','CMD_BSS_ACTIVATE_CTRL'):
            match=re.search(r'typedef struct _'+name+r'\s*\{.*?\}\s*'+name+r'\s*,[^;]*;',text,re.S)
            self.assertIsNotNone(match)
            decl+=match.group(0)+'\n'
        self.run_c(SETUP+'\n#include <stddef.h>\n'+decl+r'''
int main(void){
 assert(sizeof(CMD_SET_BSS_INFO)==88 && sizeof(CMD_UPDATE_STA_RECORD_T)==124 && sizeof(CMD_802_11_KEY)==64);
 assert(offsetof(CMD_SET_BSS_INFO,rBssRlmParam)==68 && offsetof(CMD_UPDATE_STA_RECORD_T,ucNeedResp)==51);
 struct a04_wlan_local l;struct a04_wlan_ap a;local_profile(&l);ap_profile(&a);
 uint8_t b[88],sta[124],key[64],material[16],rsc[8];memset(material,0x55,16);memset(rsc,0,8);
 assert(!a04_wlan_bss_encode(&l,&a,1,0,b));
 CMD_SET_BSS_INFO expected;memset(&expected,0,sizeof(expected));
 expected.ucBssIndex=1;expected.ucConnectionState=0;expected.ucCurrentOPMode=0;expected.ucSSIDLen=4;
 memcpy(expected.aucSSID,a.ssid,4);memcpy(expected.aucBSSID,a.mac,6);
 expected.u2OperationalRateSet=15;expected.u2BSSBasicRateSet=15;expected.ucStaRecIdxOfAP=2;
 expected.ucNonHTBasicPhyType=1;expected.ucAuthMode=1;expected.ucEncStatus=1;expected.ucPhyTypeSet=3;
 expected.ucBMCWlanIndex=19;expected.rBssRlmParam.ucBssIndex=1;expected.rBssRlmParam.ucRfBand=1;
 expected.rBssRlmParam.ucPrimaryChannel=6;expected.rBssRlmParam.ucUseShortSlotTime=1;
 assert(!memcmp(b,&expected,sizeof(expected)));
 assert(!a04_wlan_sta_encode(&l,&a,2,42,10,sta));
 CMD_UPDATE_STA_RECORD_T e;memset(&e,0,sizeof(e));e.ucStaIndex=2;e.ucStaType=0x41;memcpy(e.aucMacAddr,a.mac,6);
 e.u2AssocId=42;e.u2ListenInterval=10;e.ucBssIndex=1;e.ucDesiredPhyTypeSet=3;
 e.u2DesiredNonHTRateSet=15;e.u2BSSBasicRateSet=15;e.ucStaState=2;e.ucNeedResp=1;
 e.ucWlanIndex=3;e.ucBMCWlanIndex=19;e.ucRtsPolicy=3;assert(!memcmp(sta,&e,sizeof(e)));
 assert(!a04_wlan_ccmp_key_encode(&l,a.mac,1,0,material,16,rsc,key));
 CMD_802_11_KEY k;memset(&k,0,sizeof(k));k.ucAddRemove=k.ucTxKey=k.ucKeyType=1;memcpy(k.aucPeerAddr,a.mac,6);
 k.ucBssIdx=1;k.ucAlgorithmId=4;k.ucKeyLen=16;k.ucWlanIndex=3;memcpy(k.aucKeyMaterial,material,16);
 assert(!memcmp(key,&k,sizeof(k)));
 assert(sizeof(struct a04_wlan_station)<2048 && sizeof(struct a04_wlan_security)<512);
 return 0;
}
''')

    def test_actual_command_scan_channel_auth_assoc_sta_event_order(self):
        self.run_c(SETUP+r'''
int main(void){
 struct card c;struct a04_sdio_io io;struct a04_sdio_transport t;struct a04_wlan_host h;
 setup(&c,&io,&t);assert(!a04_wlan_host_open(&h,&t));h.ready=1;assert(!a04_wlan_runtime_begin(&h));
 struct a04_wlan_station s;memset(&s,0,sizeof(s));struct a04_wlan_local l;local_profile(&l);
 assert(!a04_wlan_station_setup(&s,&h,&l) && s.configured && c.txs==3 && !s.sta_active);
 assert(h.tx[4]==0x0a && a04_wmt_get32(h.tx+8)==13);
 struct a04_wlan_scan scan;memset(&scan,0,sizeof(scan));scan.sequence=55;scan.bss=1;scan.active=1;
 scan.probes=1;scan.min_dwell=10;scan.dwell=20;scan.timeout=1000;scan.channel_type=4;
 const uint8_t channels[]={1,1,1,6,1,11};scan.channels=channels;scan.channel_count=3;
 assert(!a04_wlan_station_scan(&s,&h,&scan,1000,1000000) && s.scan.pending);
 uint8_t payload[16];memset(payload,0,16);payload[0]=55;
 struct a04_wlan_event v={0x0d,0,4,payload};assert(!a04_wlan_station_event(&s,&h,&v,2000) && s.scan.complete);
 struct a04_wlan_ap ap;ap_profile(&ap);
 assert(!a04_wlan_station_select(&s,&h,&ap,0,44,500,3000,900000) && s.channel_pending && !s.grant_ms);
 unsigned tx=c.txs;assert(a04_wlan_station_auth(&s,&h,4000)==A04_WLAN_STATE && c.txs==tx);
 memset(payload,0,16);payload[0]=1;payload[1]=44;payload[3]=6;payload[5]=1;a04_wmt_put32(payload+12,500);
 v.id=0x10;v.length=16;assert(!a04_wlan_station_event(&s,&h,&v,5000) && s.grant_ms==500 && s.deadline_us==505000);
 assert(!a04_wlan_station_auth(&s,&h,6000) && s.join.phase==1);
 uint8_t frame[30];response(frame,&s,0xb0);a04_wmt_put16(frame+26,2);
 assert(!a04_wlan_station_auth_rx(&s,&h,frame,30,7000) && s.join.phase==2);
 assert(!a04_wlan_station_assoc(&s,&h,0x401,10,0,0,8000) && s.join.phase==3);
 response(frame,&s,0x10);a04_wmt_put16(frame+28,0xc02a);
 assert(!a04_wlan_station_assoc_rx(&s,&h,frame,30,0,9000));
 assert(s.join.phase==4 && s.join.aid==42 && s.sta_pending && !s.sta_active);
 memset(payload,0,16);memcpy(payload,ap.mac,6);payload[6]=2;payload[7]=1;
 v.id=0x0c;v.sequence=s.sta_sequence;v.length=8;
 assert(!a04_wlan_station_event(&s,&h,&v,10000) && s.sta_active && !s.deadline_us);
 assert(h.tx[4]==0x1c && h.tx[10]==1); /* release join channel after activation */
 return 0;
}
''')

    def test_scan_observations_and_malformed_ap_ies(self):
        self.run_c(SETUP+r'''
int main(void){
 uint8_t beacon[100];memset(beacon,0,sizeof(beacon));beacon[0]=0x80;
 const uint8_t mac[6]={2,6,7,8,9,10};memcpy(beacon+10,mac,6);memcpy(beacon+16,mac,6);
 a04_wmt_put16(beacon+32,100);a04_wmt_put16(beacon+34,0x401);
 const uint8_t ies[]={0,4,'t','e','s','t',1,4,0x82,0x84,0x8b,0x96,3,1,6};memcpy(beacon+36,ies,sizeof(ies));
 unsigned n=36+sizeof(ies);struct a04_wlan_ap a;
 assert(!a04_wlan_ap_parse(beacon,n,6,&a) && a.ssid_length==4 && a.rate_count==4 && a.channel==6);
 for(unsigned i=0;i<36;i++)assert(a04_wlan_ap_parse(beacon,i,6,&a));
 assert(a04_wlan_ap_parse(beacon,n,11,&a)==A04_WLAN_FORMAT);
 beacon[n++]=0;beacon[n++]=0;assert(a04_wlan_ap_parse(beacon,n,6,&a)==A04_WLAN_FORMAT);n-=2;
 beacon[43]=10;assert(a04_wlan_ap_parse(beacon,n,6,&a)==A04_WLAN_FORMAT);
 unsigned len=0;const uint8_t *rsn=a04_wlan_rsn_selected(&len);assert(len==22 && !a04_wlan_rsn_psk_ccmp(rsn,len));
 uint8_t bad[22];memcpy(bad,rsn,len);bad[20]=0x80;assert(a04_wlan_rsn_psk_ccmp(bad,len)==A04_WLAN_UNSUPPORTED);
 return 0;
}
''')

    def test_scan_wire_observation_rcpi_and_explicit_channel_policy(self):
        self.run_c(SETUP+r'''
int main(void){
 struct a04_wlan_station s;memset(&s,0,sizeof(s));local_profile(&s.local);s.configured=1;s.scan.pending=1;
 uint8_t p[128];memset(p,0,sizeof(p));unsigned head=40;
 p[head]=0x80;memset(p+head+4,255,6);const uint8_t ap[6]={2,6,7,8,9,10};memcpy(p+head+10,ap,6);memcpy(p+head+16,ap,6);
 a04_wmt_put16(p+head+32,100);a04_wmt_put16(p+head+34,0x401);
 const uint8_t ies[]={0,4,'t','e','s','t',1,4,0x82,0x84,0x8b,0x96,3,1,6};memcpy(p+head+36,ies,sizeof(ies));
 unsigned n=head+36+sizeof(ies);a04_wmt_put16(p,n);a04_wmt_put16(p+2,0xe801);p[5]=6;p[6]=24;p[16+9]=100;
 struct a04_wlan_ap a;assert(!a04_wlan_scan_observe(&s,p,n,&a) && a.rcpi_valid && a.rcpi==100 && a.channel==6);
 p[25]=255;assert(!a04_wlan_scan_observe(&s,p,n,&a) && !a.rcpi_valid);
 s.local.permitted_channels&=~(1u<<6);assert(a04_wlan_scan_observe(&s,p,n,&a)==A04_WLAN_UNSUPPORTED);
 struct card c;struct a04_sdio_io io;struct a04_sdio_transport t;struct a04_wlan_host h;
 setup(&c,&io,&t);assert(!a04_wlan_host_open(&h,&t));h.ready=1;assert(!a04_wlan_runtime_begin(&h));
 memset(&s,0,sizeof(s));local_profile(&s.local);s.configured=1;s.local.active_channels=1u<<1;
 struct a04_wlan_scan req;memset(&req,0,sizeof(req));req.bss=s.local.bss;req.timeout=100;req.active=1;
 req.channel_type=4;const uint8_t channels[]={1,6};req.channels=channels;req.channel_count=1;
 assert(a04_wlan_station_scan(&s,&h,&req,1,1000)==A04_WLAN_ARGUMENT && !c.txs);
 req.active=0;assert(!a04_wlan_station_scan(&s,&h,&req,1,1000) && c.txs==1);
 return 0;
}
''')

    def test_shared_slot_reservation_exhaustion_and_no_partial_allocation(self):
        self.run_c(SETUP+r'''
int main(void){
 struct a04_wlan_slots slots;memset(&slots,0,sizeof(slots));
 slots.wtbl=(1u<<0)|(1u<<18)|(1u<<19); /* import externally reserved slots */
 const uint8_t mac[6]={2,1,2,3,4,5};struct a04_wlan_local l;
 for(unsigned i=0;i<4;i++){
  assert(!a04_wlan_slots_take(&slots,mac,0x0ffe,0x0ffe,&l));
  assert(l.bss==i && l.own==i && l.sta==i && l.wlan==i+1 && l.bmc==i+20 && a04_wlan_local_valid(&l));
 }
 struct a04_wlan_slots previous=slots;
 assert(a04_wlan_slots_take(&slots,mac,0x0ffe,0x0ffe,&l)==A04_WLAN_STATE && !memcmp(&slots,&previous,sizeof(slots)));
 assert(a04_wlan_slots_take(&slots,mac,1,2,&l)==A04_WLAN_ARGUMENT && !memcmp(&slots,&previous,sizeof(slots)));
 memset(&slots,0,sizeof(slots));slots.wtbl=0xffffffff;previous=slots;
 assert(a04_wlan_slots_take(&slots,mac,0x0ffe,0x0ffe,&l)==A04_WLAN_STATE && !memcmp(&slots,&previous,sizeof(slots)));
 return 0;
}
''')

if __name__=='__main__':unittest.main()
