# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Original iOS raw GET1/9 byte codecs; no kernel or fixture helper dependency."""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
CASE=r'''
#include "A04BR3NativePeerIdentity.h"
#include <assert.h>
int main(void){
 struct a04_native_peer_identity peer;memset(&peer,0,sizeof(peer));peer.ssid_length=3;
 peer.bssid[0]=2;peer.bssid[5]=0x34;
 memset(peer.ssid,0xcc,sizeof(peer.ssid));peer.ssid[0]='a';peer.ssid[1]=0;peer.ssid[2]='b';
 uint8_t out[65];uint32_t bytes;
 for(unsigned type=1;type<=9;type+=8){
  const unsigned size=type==1?32:6,returned=type==1?3:6;
  memset(out,0xa5,sizeof(out));bytes=size;
  assert(!a04_native_peer_encode(&peer,type,0,out,&bytes) && bytes==returned && out[returned]==0xa5);
  if(type==1){assert(out[0]=='a' && !out[1] && out[2]=='b');for(unsigned i=3;i<65;i++)assert(out[i]==0xa5);}
  else assert(!memcmp(out,peer.bssid,6)); // NO version or length prefix
  for(unsigned n=0;n<size;n++){
   memset(out,0xa5,sizeof(out));bytes=n;
   assert(a04_native_peer_encode(&peer,type,0,out,&bytes)==EMSGSIZE && bytes==n && out[0]==0xa5);
  }
  memset(out,0xa5,sizeof(out));bytes=sizeof(out);
  assert(!a04_native_peer_encode(&peer,type,0,out,&bytes) && bytes==returned && out[returned]==0xa5);
  for(unsigned bad=0;bad<4;bad++){
   struct a04_native_peer_identity copy=peer;
   if(bad==0)memset(copy.bssid,0,6);
   if(bad==1)copy.bssid[0]=1;
   if(bad==2)copy.ssid_length=0;
   if(bad==3)copy.ssid_length=33;
   memset(out,0xa5,sizeof(out));bytes=size;
   assert(a04_native_peer_encode(&copy,type,0,out,&bytes)==ESTALE && out[0]==0xa5 && bytes==size);
  }
  assert(a04_native_peer_encode(&peer,type,1,out,&bytes)==EOPNOTSUPP);
 }
 peer.ssid_length=32;memset(peer.ssid,0x7e,32);bytes=32;
 assert(!a04_native_peer_encode(&peer,1,0,out,&bytes) && !memcmp(out,peer.ssid,32));
 assert(a04_native_peer_encode(&peer,103,0,out,&bytes)==EOPNOTSUPP);
 assert(a04_native_peer_encode(0,1,0,out,&bytes)==EINVAL);
 assert(a04_native_peer_encode(&peer,1,0,0,&bytes)==EINVAL);
 assert(a04_native_peer_encode(&peer,1,0,out,0)==EINVAL);
 return 0;
}
'''
class NativePeerIdentity(unittest.TestCase):
 def test_standalone_c_and_cpp_wire_exact_bounds_no_partial_output(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   for compiler,standard,extension in [('cc','c11','c'),('c++','c++14','cpp')]:
    source=p/('case.'+extension);source.write_text(CASE)
    subprocess.run([compiler,'-std='+standard,'-pedantic-errors','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(ROOT),str(source),'-o',str(p/'case')],check=True)
    subprocess.run([str(p/'case')],check=True)
if __name__=='__main__':unittest.main()
