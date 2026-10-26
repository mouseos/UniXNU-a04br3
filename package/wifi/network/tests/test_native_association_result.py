# SPDX-License-Identifier: BSD-2-Clause-Patent
"""GET21 scalar wire through real MT6630 open-join state machine with fake I/O.
No actual radio, AP, supplicant, association request20 or key installation.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
KERNEL=(ROOT.parent/'host-fixture')
sys.path.insert(0,str(KERNEL/'build-support/tests'))
from test_wlan_backend import SETUP

CASE=r'''
#include "A04BR3StationPhase.h"
#include "A04BR3NativeGET.h"
#include "A04BR3Apple80211IOCTL.h"
static unsigned userCopies;
static int no_in(void *,uint64_t,void *,unsigned){userCopies++;return EFAULT;}
static int no_out(void *,const void *,uint64_t,unsigned){userCopies++;return EFAULT;}
static a04_network_state snapshot(const a04_wlan_station *s){
 a04_network_state v={};v.present=v.mac_valid=v.firmware_running=1;v.mac[0]=2;
 v.control_ready=s->configured && !s->failed;
 v.station_phase=a04_network_phase_from_station(s);
 v.join_state=a04_network_join_from_station(s);
 v.associated=s->sta_active && s->join.phase==4 && !s->failed;
 return v;
}
static int get(void *p,uint32_t type,uint32_t value,void *out,uint32_t *bytes){
 auto *s=(a04_wlan_station *)p;auto v=snapshot(s);return a04_native_get(&v,type,value,out,bytes);
}
static void expect(a04_wlan_station *s,int error,unsigned result){
 auto state=snapshot(s);a04_network_backend b={};b.context=s;b.native_get=get;
 a04_network_copy copies={nullptr,no_in,no_out};a04_apple80211_req64 req={};
 memcpy(req.name,"en0",4);req.type=21;uint8_t scratch[5];memset(scratch,0xa5,5);
 assert(a04_network_get(&b,&state,&req,"en0",scratch,4,&copies)==error);
 assert(req.value==(error?0:result) && !req.length && !req.user_data && !userCopies && scratch[4]==0xa5);
 if(error)for(unsigned i=0;i<4;i++)assert(scratch[i]==0xa5);
}
int main(){
 struct card c;struct a04_sdio_io io;struct a04_sdio_transport t;struct a04_wlan_host h;
 setup(&c,&io,&t);assert(!a04_wlan_host_open(&h,&t));h.ready=1;assert(!a04_wlan_runtime_begin(&h));
 a04_wlan_station s={};a04_wlan_local l;local_profile(&l);a04_wlan_ap ap;ap_profile(&ap);
 expect(&s,ENETDOWN,0);assert(!a04_wlan_station_setup(&s,&h,&l));
 expect(&s,ENOTCONN,0);s.scan.complete=1;
 assert(!a04_wlan_station_select(&s,&h,&ap,0,44,500,3000,900000));
 expect(&s,0,0); // actual accepted channel request, not association success
 uint8_t payload[16]={};payload[0]=1;payload[1]=44;payload[3]=6;payload[5]=1;a04_wmt_put32(payload+12,500);
 a04_wlan_event event={};event.id=0x10;event.length=16;event.payload=payload;
 assert(!a04_wlan_station_event(&s,&h,&event,5000));expect(&s,0,0);
 assert(!a04_wlan_station_auth(&s,&h,6000));expect(&s,0,0);
 uint8_t frame[30];response(frame,&s,0xb0);a04_wmt_put16(frame+26,2);
 assert(!a04_wlan_station_auth_rx(&s,&h,frame,30,7000));expect(&s,0,0);
 assert(!a04_wlan_station_assoc(&s,&h,0x401,10,nullptr,0,8000));expect(&s,0,0);
 response(frame,&s,0x10);a04_wmt_put16(frame+28,0xc02a);
 assert(!a04_wlan_station_assoc_rx(&s,&h,frame,30,0,9000));
 assert(s.join.phase==4 && s.sta_pending && !s.sta_active);expect(&s,0,0);
 memset(payload,0,16);memcpy(payload,ap.mac,6);payload[6]=2;payload[7]=1;
 event.id=0x0c;event.sequence=s.sta_sequence;event.length=8;
 assert(!a04_wlan_station_event(&s,&h,&event,10000));expect(&s,0,1);
 // AP response alone, malformed/inconsistent activation and secure states
 // cannot be promoted to success; do not imply authorized/IP connectivity.
 s.sta_pending=1;expect(&s,EOPNOTSUPP,0);s.sta_pending=0;
 s.join.aid=0;expect(&s,EOPNOTSUPP,0);s.join.aid=42;
 s.secured=1;expect(&s,EOPNOTSUPP,0);s.secured=0;
 s.join.failed=A04_WLAN_STATUS;expect(&s,EIO,0);s.join.failed=0;
 s.failed=A04_WLAN_TIMEOUT;expect(&s,ENETDOWN,0);s.failed=0;
 auto state=snapshot(&s);uint8_t scratch[5];uint32_t n=4;
 memset(scratch,0xa5,5);state.associated=0;
 assert(a04_native_get(&state,21,0,scratch,&n)==EAGAIN && scratch[0]==0xa5);
 state.associated=1;state.station_phase=A04_NET_PHASE_ASSOC;
 assert(a04_native_get(&state,21,0,scratch,&n)==EAGAIN && scratch[0]==0xa5);
 state.station_phase=A04_NET_PHASE_ASSOCIATED;state.join_state=A04_NET_JOIN_PENDING;
 assert(a04_native_get(&state,21,0,scratch,&n)==EAGAIN && scratch[0]==0xa5);
 state.join_state=A04_NET_JOIN_OPEN_COMPLETE;n=3;
 assert(a04_native_get(&state,21,0,scratch,&n)==EMSGSIZE && n==3 && scratch[0]==0xa5);
 n=4;assert(a04_native_get(&state,21,1,scratch,&n)==EOPNOTSUPP && scratch[0]==0xa5);
 a04_network_backend backend={};backend.context=&s;backend.native_get=get;
 a04_network_copy copies={nullptr,no_in,no_out};a04_apple80211_req64 req={};memcpy(req.name,"en0",4);req.type=21;
 state=snapshot(&s);req.length=4;
 assert(a04_network_get(&backend,&state,&req,"en0",scratch,4,&copies)==EINVAL);
 req.length=0;req.user_data=0xdead;
 assert(a04_network_get(&backend,&state,&req,"en0",scratch,4,&copies)==EINVAL && !userCopies);
 req.user_data=0;assert(a04_network_get(&backend,&state,&req,"en0",scratch,3,&copies)==EMSGSIZE);
 // Real malformed/failed AP auth response propagates failure, never completion.
 a04_wlan_station failed={};failed.configured=failed.selected=1;failed.join.phase=1;
 failed.local=l;failed.ap=ap;failed.deadline_us=900000;
 memcpy(failed.join.client,l.mac,6);memcpy(failed.join.ap,ap.mac,6);
 response(frame,&failed,0xb0);a04_wmt_put16(frame+26,2);a04_wmt_put16(frame+28,1);
 assert(a04_wlan_station_auth_rx(&failed,&h,frame,30,10000)==A04_WLAN_STATUS);
 expect(&failed,ENETDOWN,0);
}
'''

class NativeAssociationResult(unittest.TestCase):
 def test_real_open_join_scalar_wire_and_negative_paths(self):
  prefix='#include <stdint.h>\n#include <assert.h>\n#include <string.h>\n#include "A04BR3WMTProtocol.h"\n'
  with tempfile.TemporaryDirectory() as directory:
   d=Path(directory);(d/'case.cpp').write_text(prefix+SETUP+CASE)
   subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
    '-fno-sanitize-recover=all','-I',str(ROOT),'-I',str(ROOT.parent/'sdio'),str(d/'case.cpp'),'-o',str(d/'case')],check=True)
   subprocess.run([str(d/'case')],check=True)

if __name__=='__main__':unittest.main()
