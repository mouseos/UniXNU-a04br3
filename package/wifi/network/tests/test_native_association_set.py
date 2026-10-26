# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Strict native592 decode -> generation snapshot -> actual locked join method.
Runs real source station commands against simulated FIFO, never a physical AP.
"""
from pathlib import Path
import subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
KERNEL=(ROOT.parent/'host-fixture')
sys.path.insert(0,str(KERNEL/'build-support/tests'))
from test_wlan_backend import SETUP

CASE=r'''
#include "A04BR3NativeAssociation.h"
#include "A04BR3StationPhase.h"
#include "A04BR3WLANJoinAdmission.h"
#include "A04BR3WLANCurrentNetwork.h"
#include "A04BR3NativeCurrentNetwork.h"
#include "A04BR3NativePeerIdentity.h"
#include <strings.h>
static bool locked;static unsigned scheduled;
static void IOLockLock(void *){assert(!locked);locked=true;}
static void IOLockUnlock(void *){assert(locked);locked=false;}
class A04BR3SDIO {
public:
 void *transportLock=nullptr;bool liveStopping=false,networkInitialized=true;
 a04_wlan_host *wlanHost=nullptr;
 struct Live{a04_wlan_runtime runtime;};Live storage={};Live *live=&storage;
 static uint64_t liveNow(){assert(locked);return 2000000;}
 void scheduleLive(){assert(locked);scheduled++;}
PRODUCTION
};
static unsigned race,joinCalls,infoCalls;
static int info(void *p,a04_wlan_scan_info *out){infoCalls++;return ((A04BR3SDIO *)p)->scanSnapshot(out);}
static int record(void *p,unsigned g,unsigned i,a04_wlan_scan_record *out){return ((A04BR3SDIO *)p)->scanCopyRecord(g,i,out);}
static int join(void *p,const a04_wlan_join_target *target){
 assert(!locked);joinCalls++;auto *device=(A04BR3SDIO *)p;auto &r=device->live->runtime;
 if(race==1)r.scan_generation++;
 if(race==2)r.scan[target->index].mac[5]^=1;
 if(race==3)r.scan[target->index].ssid[0]^=1;
 if(race==4)r.scan_extra[target->index].observed_us=3000000;
 if(race==5)r.station.scan.pending=1;
 if(race==6)r.scan[target->index].capability|=16;
 if(race==7)r.scan[target->index].channel=11;
 return device->requestJoinTarget(target);
}
static void raw_request(uint8_t *p){
 memset(p,0,592);p[0]=1;p[4]=2;p[6]=1;p[12]=4;memcpy(p+16,"test",4);
}
static void prepare(A04BR3SDIO *device,card *card,a04_sdio_io *io,a04_sdio_transport *t,a04_wlan_host *h){
 assert(!locked);setup(card,io,t);assert(!a04_wlan_host_open(h,t));h->ready=1;assert(!a04_wlan_runtime_begin(h));
 device->storage.runtime={};device->live=&device->storage;device->wlanHost=h;
 device->liveStopping=false;device->networkInitialized=true;
 auto &r=device->live->runtime;r.configured=r.admin=1;
 a04_wlan_local local;local_profile(&local);assert(!a04_wlan_station_setup(&r.station,h,&local));
 r.station.scan.complete=1;r.scan_generation=7;r.scan_count=1;ap_profile(&r.scan[0]);
 r.scan_extra[0].generation=7;r.scan_extra[0].observed_us=1000000;
 r.scan[0].rcpi_valid=1;r.scan[0].rcpi=100;
 const uint8_t ies[]={0,4,'t','e','s','t',1,4,0x82,0x84,0x8b,0x96,3,1,6};
 r.scan_extra[0].ie_bytes=sizeof(ies);memcpy(r.scan_extra[0].ies,ies,sizeof(ies));
 scheduled=joinCalls=infoCalls=race=0;
}
int main(){
 auto *device=new A04BR3SDIO;auto *work=new a04_wlan_scan_record;
 card card;struct a04_sdio_io io;struct a04_sdio_transport t;struct a04_wlan_host h;
 a04_association_backend backend={device,info,record,join};uint8_t bytes[592];raw_request(bytes);
 a04_native_association decoded={};assert(!a04_native_association_decode(0,bytes,592,&decoded));
 for(unsigned n=0;n<592;n++)assert(a04_native_association_decode(0,bytes,n,&decoded)==EINVAL);
 assert(a04_native_association_decode(0,bytes,593,&decoded)==EINVAL);
 assert(a04_native_association_decode(0,nullptr,592,&decoded)==EINVAL);
 assert(a04_native_association_decode(1,bytes,592,&decoded)==EOPNOTSUPP);
 // Every byte outside proven fields/defaults is rejected (keys, private MAC,
 // RSN/WPA IEs, per-client extensions and all unused SSID bytes included).
 for(unsigned i=0;i<592;i++){
  if(i<10 || (i>=12 && i<20) || (i>=48 && i<54) || i==463)continue;
  bytes[i]=1;assert(a04_native_association_decode(0,bytes,592,&decoded)==EOPNOTSUPP);bytes[i]=0;
 }
 for(unsigned offset:{4u,6u,8u,463u}){
  raw_request(bytes);bytes[offset]=offset==463?8:3;
  assert(a04_native_association_decode(0,bytes,592,&decoded)==EOPNOTSUPP);
 }
 raw_request(bytes);bytes[12]=33;assert(a04_native_association_decode(0,bytes,592,&decoded)==EINVAL);
 raw_request(bytes);bytes[463]=0x20;assert(a04_native_association_decode(0,bytes,592,&decoded)==EINVAL);
 bytes[48]=1;assert(a04_native_association_decode(0,bytes,592,&decoded)==EINVAL);
 raw_request(bytes);bytes[48]=2;assert(a04_native_association_decode(0,bytes,592,&decoded)==EINVAL);
 raw_request(bytes);prepare(device,&card,&io,&t,&h);unsigned tx=card.txs;
 assert(!a04_native_association_set(&backend,0,bytes,592,work));
 auto &r=device->live->runtime;
 assert(card.txs==tx+3 && scheduled==1 && joinCalls==1 && r.station.selected && r.station.channel_pending);
 assert(!r.station.sta_active && !r.station.join.phase && a04_network_join_from_station(&r.station)==A04_NET_JOIN_PENDING);
 tx=card.txs;assert(a04_native_association_set(&backend,0,bytes,592,work)==EBUSY && card.txs==tx && scheduled==1);
 // Advance the ACTUAL admitted station, using peer frames and firmware
 // activation events. GET103 remains absent before association, never scan0.
 auto *current=new a04_current_network_work;uint8_t output[1165];uint32_t length=1164;
 memset(output,0xa5,sizeof(output));assert(device->currentRecord(&current->raw)==ENOTCONN);
 uint8_t payload[16]={};payload[0]=1;payload[1]=1;payload[3]=6;payload[5]=1;a04_wmt_put32(payload+12,1000);
 a04_wlan_event event={};event.id=0x10;event.length=16;event.payload=payload;
 assert(!a04_wlan_station_event(&r.station,&h,&event,2001000));
 assert(!a04_wlan_station_auth(&r.station,&h,2002000));
 uint8_t frame[30];response(frame,&r.station,0xb0);a04_wmt_put16(frame+26,2);
 assert(!a04_wlan_station_auth_rx(&r.station,&h,frame,30,2003000));
 assert(!a04_wlan_station_assoc(&r.station,&h,0x401,10,nullptr,0,2004000));
 response(frame,&r.station,0x10);a04_wmt_put16(frame+28,0xc02a);
 assert(!a04_wlan_station_assoc_rx(&r.station,&h,frame,30,0,2005000));
 assert(device->currentRecord(&current->raw)==ENOTCONN);
 memset(payload,0,16);memcpy(payload,r.scan[0].mac,6);payload[6]=r.station.local.sta;payload[7]=r.station.local.bss;
 event.id=0x0c;event.length=8;event.sequence=r.station.sta_sequence;
 assert(!a04_wlan_station_event(&r.station,&h,&event,2006000));
 assert(a04_network_join_from_station(&r.station)==A04_NET_JOIN_OPEN_COMPLETE);
 assert(!device->currentRecord(&current->raw));
 // Identity uses the actual associated station snapshot just obtained after
 // real auth/assoc + matching ACTIVATE_STA_REC; scan-only was ENOTCONN above.
 a04_native_peer_identity identity={};identity.ssid_length=current->raw.ap.ssid_length;
 memcpy(identity.ssid,current->raw.ap.ssid,identity.ssid_length);memcpy(identity.bssid,current->raw.ap.mac,6);
 uint8_t peerWire[33];uint32_t peerLength=32;memset(peerWire,0xa5,sizeof(peerWire));
 assert(!a04_native_peer_encode(&identity,1,0,peerWire,&peerLength) && peerLength==4 && peerWire[4]==0xa5 && !memcmp(peerWire,"test",4));
 for(unsigned i=4;i<32;i++)assert(peerWire[i]==0xa5);
 peerLength=6;assert(!a04_native_peer_encode(&identity,9,0,peerWire,&peerLength) && !memcmp(peerWire,r.station.ap.mac,6));
 memset(output,0,1164);output[0x8b]=4; // observed GET103 input version0
 assert(!a04_native_current_encode(current,2007000,output,&length));
 assert(output[0]==1 && output[0x60]==4 && !memcmp(output+0x61,"test",4) && output[1164]==0xa5);
 assert(!memcmp(output+0x1c,r.station.ap.mac,6) && a04_scan_result_u32(output+0x84)==1007);
 assert(a04_scan_result_u32(output+4)==1 && output[0x8a]==15);
 auto preserved=current->raw;memset(output,0,1164);output[0x8b]=4;
 current->raw.observation.ie_bytes=1025;assert(a04_native_current_encode(current,2007000,output,&length)==EMSGSIZE && !output[0]);
 current->raw=preserved;current->raw.ap.rcpi_valid=0;
 assert(a04_native_current_encode(current,2007000,output,&length)==EOPNOTSUPP && !output[0]);
 current->raw=preserved;current->raw.observation.observed_us=3000000;
 assert(a04_native_current_encode(current,2007000,output,&length)==EINVAL && !output[0]);
 r.station.sta_active=0;assert(device->currentRecord(&current->raw)==ENOTCONN);r.station.sta_active=1;
 r.station.secured=1;assert(device->currentRecord(&current->raw)==EOPNOTSUPP);r.station.secured=0;
 r.scan[0].mac[5]^=1;assert(device->currentRecord(&current->raw)==ESTALE);r.scan[0].mac[5]^=1;
 delete current;
 // Exact forced BSSID is supported; other BSSIDs never fall back by SSID.
 prepare(device,&card,&io,&t,&h);raw_request(bytes);bytes[463]=0x20;memcpy(bytes+48,r.scan[0].mac,6);
 assert(!a04_native_association_set(&backend,0,bytes,592,work));
 prepare(device,&card,&io,&t,&h);bytes[53]^=0x40;tx=card.txs;
 assert(a04_native_association_set(&backend,0,bytes,592,work)==ENOENT && card.txs==tx);
 // Interleavings after resolution and BEFORE the actual lock/admission.
 for(unsigned mode=1;mode<=7;mode++){
  prepare(device,&card,&io,&t,&h);raw_request(bytes);race=mode;tx=card.txs;
  int expected=mode==4?EAGAIN:(mode==5?EBUSY:(mode==6?EOPNOTSUPP:ESTALE));
  assert(a04_native_association_set(&backend,0,bytes,592,work)==expected);
  assert(card.txs==tx && !scheduled && !r.station.selected && !locked);
 }
 prepare(device,&card,&io,&t,&h);raw_request(bytes);r.scan_count=2;r.scan[1]=r.scan[0];r.scan[1].mac[5]^=1;r.scan_extra[1]=r.scan_extra[0];
 tx=card.txs;assert(a04_native_association_set(&backend,0,bytes,592,work)==EEXIST && card.txs==tx && !joinCalls);
 prepare(device,&card,&io,&t,&h);r.scan[0].capability|=16;tx=card.txs;
 assert(a04_native_association_set(&backend,0,bytes,592,work)==EOPNOTSUPP && !joinCalls && card.txs==tx);
 prepare(device,&card,&io,&t,&h);r.scan[0].rsn_length=2;assert(a04_native_association_set(&backend,0,bytes,592,work)==EOPNOTSUPP && !joinCalls);
 prepare(device,&card,&io,&t,&h);r.scan_count=17;assert(a04_native_association_set(&backend,0,bytes,592,work)==EOVERFLOW && !joinCalls);
 prepare(device,&card,&io,&t,&h);r.station.scan.complete=0;assert(a04_native_association_set(&backend,0,bytes,592,work)==EAGAIN && !joinCalls);
 prepare(device,&card,&io,&t,&h);card.fail_tx=card.txs+1; // injected FIFO failure is not successful admission
 assert(a04_native_association_set(&backend,0,bytes,592,work)!=0 && !scheduled && !r.station.sta_active);
 delete work;delete device;
}
'''

class NativeAssociationSet(unittest.TestCase):
 def test_pure_codec_and_headers_standalone_without_fixtures(self):
  codec=r'''
#include "A04BR3NativeAssociationCodec.h"
int main(void){
 uint8_t input[592]={0};struct a04_native_association output;
 input[0]=1;input[4]=2;input[6]=1;input[12]=1;input[16]='x';
 if(a04_native_association_decode(0,input,sizeof(input),&output))return 1;
 input[463]=0x20;input[48]=2;input[53]=9;
 if(a04_native_association_decode(0,input,sizeof(input),&output))return 2;
 input[48]=1;
 if(a04_native_association_decode(0,input,sizeof(input),&output)!=EINVAL)return 3;
 if(a04_assoc_unicast_address((const uint8_t *)0))return 4;
 return 0;
}
'''
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory)
   for compiler,standard,extension in [('cc','c11','c'),('c++','c++14','cpp')]:
    source=p/('codec.'+extension);source.write_text(codec)
    subprocess.run([compiler,'-std='+standard,'-pedantic-errors','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(ROOT),str(source),'-o',str(p/'codec')],check=True)
    subprocess.run([str(p/'codec')],check=True)
   for header in ['A04BR3NativeAssociation.h','A04BR3NativeCurrentNetwork.h']:
    source=p/'alone.cpp';source.write_text('#include "'+header+'"\nint main(){return 0;}\n')
    subprocess.run(['c++','-std=c++14','-Wall','-Wextra','-Werror','-I',str(ROOT),'-c',str(source),'-o',str(p/'alone.o')],check=True)
 def test_strict_decode_actual_locked_admission_and_races(self):
  s=(ROOT.parent/'sdio/A04BR3SDIO.cpp').read_text()
  a=s.index(' int scanSnapshot(');b=s.index(' int requestJoin(',a)
  methods=s[a:b]
  a=s.index(' int currentRecord(');b=s.index(' int sendEAPOL(',a)
  methods+=s[a:b]
  code='#include <stdint.h>\n#include <assert.h>\n#include <string.h>\n#include <initializer_list>\n#include "A04BR3WMTProtocol.h"\n'+SETUP+CASE.replace('PRODUCTION',methods)
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory);(p/'case.cpp').write_text(code)
   subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(ROOT),'-I',str(ROOT.parent/'sdio'),str(p/'case.cpp'),'-o',str(p/'case')],check=True)
   subprocess.run([str(p/'case')],check=True)

if __name__=='__main__':unittest.main()
