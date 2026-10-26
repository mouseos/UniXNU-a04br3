# SPDX-License-Identifier: BSD-2-Clause-Patent
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class NativeGET(unittest.TestCase):
 def test_observed_power_and_driver_version(self):
  c=r'''
#include "A04BR3NativeGET.h"
#include <assert.h>
#include <initializer_list>
int main(){a04_network_state s={};uint8_t b[260];uint32_t n=sizeof(b);
memset(b,0x55,sizeof(b));assert(a04_native_get(&s,19,0,b,&n)==ENETDOWN && b[0]==0x55);
s.present=s.mac_valid=s.firmware_running=s.control_ready=1;s.mac[0]=2;
for(unsigned cap=0;cap<24;cap++){memset(b,0,sizeof(b));b[0]=1;n=cap;assert(a04_native_get(&s,19,0,b,&n)==EMSGSIZE && n==cap && b[0]==1);}
memset(b,0x55,sizeof(b));b[0]=1;b[1]=b[2]=b[3]=0;s.radio_enabled=1;n=sizeof(b);
assert(a04_native_get(&s,19,0,b,&n)==0 && n==24 && b[0]==1 && b[4]==1 && b[8]==1 && b[24]==0x55);
s.radio_enabled=0;n=24;assert(a04_native_get(&s,19,0,b,&n)==0 && b[8]==0);
b[0]=2;n=24;assert(a04_native_get(&s,19,0,b,&n)==EINVAL && b[0]==2);
n=260;memset(b,0x55,sizeof(b));assert(a04_native_get(&s,12,0,b,&n)==0 && n==13 && b[13]==0x55);for(unsigned i=0;i<13;i++)assert(!b[i]);n=260;assert(a04_native_get(&s,19,1,b,&n)==EOPNOTSUPP);
memset(b,0x55,sizeof(b));n=260;assert(a04_native_get(&s,43,0,b,&n)==0 && n==256 && b[255]==0 && b[256]==0x55);
assert(!strcmp((char *)b,"A04BR3 MT6630 source SDIO runtime"));
// Source-defined station mode is independent of association and must not
// claim a running link. Interrupt mitigation is deliberately unsupported.
for(unsigned type: {15u}){
 memset(b,0xa5,sizeof(b));n=3;assert(a04_native_get(&s,type,0,b,&n)==EMSGSIZE && b[0]==0xa5);
 n=sizeof(b);assert(!a04_native_get(&s,type,0,b,&n) && n==4 && b[0]==1 && !b[1] && b[4]==0xa5 && !s.associated);
}
// State is an explicit source phase, never inferred merely from !associated.
for(unsigned phase=1;phase<=5;phase++){
 s.station_phase=phase;s.associated=phase==5;memset(b,0xa5,sizeof(b));n=sizeof(b);
 assert(!a04_native_get(&s,13,0,b,&n) && n==4 && b[0]==phase-1 && b[4]==0xa5);
}
s.associated=0;s.station_phase=A04_NET_PHASE_ASSOCIATED;n=sizeof(b);memset(b,0xa5,sizeof(b));
assert(a04_native_get(&s,13,0,b,&n)==EAGAIN && b[0]==0xa5);
s.station_phase=A04_NET_PHASE_IDLE;s.associated=1;
assert(a04_native_get(&s,13,0,b,&n)==EAGAIN && b[0]==0xa5);
s.station_phase=A04_NET_PHASE_UNKNOWN;s.associated=0;
// Current network is actual association, NEVER the completed scan records.
uint8_t association[1165];
for(unsigned type: {1u,9u,103u,16u,17u,8u}){
 memset(association,0xa5,sizeof(association));n=sizeof(association);
 assert(a04_native_get(&s,type,0,association,&n)==ENOTCONN && n==sizeof(association));
 for(unsigned i=0;i<sizeof(association);i++)assert(association[i]==0xa5);
 s.associated=1;assert(a04_native_get(&s,type,0,association,&n)==EOPNOTSUPP);s.associated=0;
 n=0;assert(a04_native_get(&s,type,0,association,&n)==EMSGSIZE);
}
// Native27/254 share the original iOS channel-list carrier, with a real
// policy mask; no hardcoded full-band support and actual programmed20MHz restriction; no wider/ACTIVE guesses.
uint8_t channels[4825];
for(unsigned type:{27u,254u}){
 memset(channels,0xa5,sizeof(channels));n=sizeof(channels);
 assert(a04_native_get(&s,type,0,channels,&n)==EOPNOTSUPP && channels[0]==0xa5);
 s.permitted_channels=(1u<<1)|(1u<<6)|(1u<<11);n=4823;
 assert(a04_native_get(&s,type,0,channels,&n)==EMSGSIZE && channels[0]==0xa5);
 n=sizeof(channels);assert(!a04_native_get(&s,type,0,channels,&n) && n==4824 && channels[0]==1 && channels[4]==3);
 unsigned expected[]={1,6,11};for(unsigned i=0;i<3;i++){
  unsigned offset=8+12*i;assert(channels[offset]==1 && channels[offset+4]==expected[i] && channels[offset+8]==0xa);
 }
 for(unsigned i=44;i<4824;i++){assert(!channels[i]);}
 assert(channels[4824]==0xa5);
 s.permitted_channels=1u<<14;n=sizeof(channels);memset(channels,0xa5,sizeof(channels));
 assert(a04_native_get(&s,type,0,channels,&n)==EOPNOTSUPP && channels[0]==0xa5);s.permitted_channels=0;
}
// Unknown firmware power-save/role/phase cannot be inferred from control-ready.
for(unsigned type: {5u,13u,18u,57u,96u}){
 memset(b,0xa5,sizeof(b));n=sizeof(b);assert(a04_native_get(&s,type,0,b,&n)==EOPNOTSUPP && b[0]==0xa5);
}
// Class conformance must not turn unsupported scan/association into success.
for(unsigned type: {10u,11u,20u,0xffffffffu}){
 memset(b,0x55,sizeof(b));n=sizeof(b);
 assert(a04_native_get(&s,type,0,b,&n)==EOPNOTSUPP && n==sizeof(b));
 for(unsigned i=0;i<sizeof(b);i++)assert(b[i]==0x55);
}
return 0;}
'''
  with tempfile.TemporaryDirectory() as tmp:
   d=Path(tmp);(d/'test.cpp').write_text(c)
   subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(ROOT),str(d/'test.cpp'),'-o',str(d/'test')],check=True)
   subprocess.run([str(d/'test')],check=True)
if __name__=='__main__':unittest.main()
