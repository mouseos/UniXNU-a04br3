# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Actual strict592 Personal producer decoder; no firmware/key success mocks."""
from pathlib import Path
import unittest,subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1]
class PersonalAssociation(unittest.TestCase):
 def test_producer_rsn_key_extensions_and_rejections(self):
  code='#include <cassert>\n#include <cstring>\n#include "'+str(ROOT/'A04BR3NativePersonalAssociation.h')+'"\n'+r'''
static void make(uint8_t p[592]){memset(p,0,592);p[0]=1;p[4]=2;p[6]=1;p[8]=8;p[12]=4;memcpy(p+16,"test",4);
 p[56]=1;p[60]=8;p[64]=10;memcpy(p+72,"password",8);unsigned n;const uint8_t *rsn=a04_wlan_rsn_selected(&n);p[204]=(uint8_t)n;memcpy(p+206,rsn,n);}
int main(){uint8_t p[592];make(p);a04_native_personal_request request={};assert(!a04_native_personal_decode(0,p,592,&request));
 assert(request.peer.ssid_length==4&&request.key.kind==A04_NATIVE_KEY_PASSPHRASE&&request.key.length==8);
 p[504]=4;assert(!a04_native_personal_decode(0,p,592,&request));p[468]=32;memset(p+472,0x55,32);assert(!a04_native_personal_decode(0,p,592,&request)&&request.derived_bytes==32);
 p[468]=31;assert(a04_native_personal_decode(0,p,592,&request));assert(a04_psk_zero((const uint8_t *)&request,sizeof(request)));
 make(p);p[60]=32;p[64]=6;memset(p+72,0x55,32);assert(!a04_native_personal_decode(0,p,592,&request)&&request.key.kind==A04_NATIVE_KEY_PMK);
 p[468]=32;memset(p+472,0x55,32);assert(a04_native_personal_decode(0,p,592,&request));
 for(unsigned i=0;i<592;i++){make(p);if(p[i] || (i>=16&&i<20) || (i>=72&&i<80))continue;
  // These zero bytes belong to explicitly supported nonzero producer fields.
  if(i==48||i==49||i==50||i==51||i==52||i==53||i==463||i==468||i==472||i==504)continue;
  p[i]=1;assert(a04_native_personal_decode(0,p,592,&request));}
 make(p);p[463]=0x20;p[48]=2;p[53]=1;assert(!a04_native_personal_decode(0,p,592,&request)&&request.peer.force_bssid);
 for(unsigned n=0;n<592;n++)assert(a04_native_personal_decode(0,p,n,&request));
 make(p);p[224]=0x80;assert(a04_native_personal_decode(0,p,592,&request)); // PMF unsupported
 make(p);p[8]=16;assert(a04_native_personal_decode(0,p,592,&request)); // FT unsupported
 make(p);p[504]=8;assert(a04_native_personal_decode(0,p,592,&request));
 return 0;}
'''
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'case.cpp').write_text(code)
   subprocess.run(['c++','-std=c++14','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'case.cpp'),'-o',str(p/'case')],check=True)
   subprocess.run([str(p/'case')],check=True)
if __name__=='__main__':unittest.main()
