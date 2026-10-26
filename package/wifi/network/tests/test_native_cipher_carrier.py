# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Execute strict native SET3 carrier; synthetic bytes, no cryptographic stubs."""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class CipherCarrier(unittest.TestCase):
 def test_exact_size_value_preservation_and_fail_closed_dispatch(self):
  source=r'''
#include <stdint.h>
#include <assert.h>
#include <string.h>
#include "A04BR3NativeCipherCarrier.h"
static unsigned calls;static int result=EOPNOTSUPP;static const uint8_t *original;
static int consume(void *context,const uint8_t *p,unsigned n){
 assert(context==&calls && n==148 && p!=original && !memcmp(p,original,148));calls++;return result;
}
static void put32(uint8_t *p,uint32_t n){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(n>>(8*i));}
int main(void){
 uint8_t p[160];for(unsigned i=0;i<sizeof(p);i++)p[i]=(uint8_t)i;original=p;
 struct a04_native_cipher_consumer consumer={&calls,consume};struct a04_native_cipher_view view;
 for(unsigned n=0;n<=160;n++){
  int e=a04_native_cipher_carrier(3,0,p,n,&view);
  if(n==148){assert(!e&&view.bytes==p&&view.length==148);}else assert(e==EINVAL&&!view.bytes&&!view.length);
 }
 assert(a04_native_cipher_carrier(3,0,0,148,&view)==EINVAL);
 assert(a04_native_cipher_carrier(3,1,p,148,&view)==EOPNOTSUPP);
 assert(a04_native_cipher_carrier(20,0,p,148,&view)==EOPNOTSUPP);
 assert(a04_native_cipher_carrier(3,0,(const void *)(UINTPTR_MAX-147),148,&view)==EINVAL);
 assert(a04_native_cipher_dispatch(3,0,p,148,0)==EOPNOTSUPP&&!calls);
 assert(a04_native_cipher_dispatch(3,0,p,147,&consumer)==EINVAL&&!calls);
 assert(a04_native_cipher_dispatch(3,1,p,148,&consumer)==EOPNOTSUPP&&!calls);
 assert(a04_native_cipher_dispatch(3,0,p,148,&consumer)==EOPNOTSUPP&&calls==1);
 result=EIO;assert(a04_native_cipher_dispatch(3,0,p,148,&consumer)==EIO&&calls==2);
 assert(!memcmp(original,p,148));
 uint8_t raw[148]={0};struct a04_native_key_fields key;
 put32(raw,1);put32(raw+4,5);put32(raw+8,1);raw[12]=9;raw[14]=2;
 for(unsigned i=0;i<5;i++)raw[16+i]=(uint8_t)(i+1);
 assert(!a04_native_key_fields_decode(raw,148,A04_NATIVE_KEY_FROM_SET3,&key));
 assert(key.kind==A04_NATIVE_KEY_WEP40&&key.length==5&&key.flags==9&&key.index==2&&!memcmp(key.secret,raw+16,5));
 raw[80]=1;assert(a04_native_key_fields_decode(raw,148,A04_NATIVE_KEY_FROM_SET3,&key)==EOPNOTSUPP&&!key.kind&&!key.secret[0]);raw[80]=0;
 memset(raw,0,148);put32(raw,1);put32(raw+4,64);put32(raw+8,9);
 for(unsigned i=0;i<64;i++)raw[16+i]=(uint8_t)i;
 assert(!a04_native_key_fields_decode(raw,148,A04_NATIVE_KEY_FROM_SET3,&key)&&key.kind==A04_NATIVE_KEY_MSK&&key.length==64);
 put32(raw+8,7);raw[92]=2;raw[97]=1;
 assert(!a04_native_key_fields_decode(raw,148,A04_NATIVE_KEY_FROM_SET3,&key)&&key.kind==A04_NATIVE_KEY_PMKSA&&key.peer[5]==1);
 raw[92]=3;assert(a04_native_key_fields_decode(raw,148,A04_NATIVE_KEY_FROM_SET3,&key)==EINVAL);raw[92]=2;
 memset(raw,0,148);put32(raw,1);put32(raw+8,9);
 assert(!a04_native_key_fields_decode(raw,148,A04_NATIVE_KEY_FROM_SET3,&key)&&key.kind==A04_NATIVE_KEY_MSK&&!key.length);
 uint8_t assoc[592]={0};put32(assoc,1);put32(assoc+56,1);put32(assoc+60,32);put32(assoc+64,6);
 for(unsigned i=0;i<32;i++)assoc[72+i]=(uint8_t)(i+1);
 assert(!a04_native_assoc_key_fields(assoc,592,&key)&&key.kind==A04_NATIVE_KEY_PMK&&key.length==32);
 assert(a04_native_key_fields_decode(assoc+56,148,A04_NATIVE_KEY_FROM_SET3,&key)==EOPNOTSUPP);
 assert(a04_native_assoc_key_fields(assoc,591,&key)==EINVAL);
 memset(assoc+72,0,64);put32(assoc+60,8);put32(assoc+64,10);memcpy(assoc+72,"testpass",8);
 assert(!a04_native_assoc_key_fields(assoc,592,&key)&&key.kind==A04_NATIVE_KEY_PASSPHRASE&&key.length==8);
 a04_native_key_clear(&key);for(unsigned i=0;i<sizeof(key);i++)assert(!((uint8_t *)&key)[i]);
 /* No producer evidence for a native CCMP/TK16 carrier: explicitly fail. */
 put32(assoc+60,16);put32(assoc+64,5);assert(a04_native_assoc_key_fields(assoc,592,&key)==EOPNOTSUPP);
 return 0;
}
'''
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'test.c').write_text(source)
   subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(ROOT),str(p/'test.c'),'-o',str(p/'test')],check=True)
   subprocess.run([str(p/'test')],check=True)
   subprocess.run(['clang++','-std=c++14','-Wall','-Wextra','-Werror','-I',str(ROOT),'-x','c++','-fsyntax-only',str(p/'test.c')],check=True)
if __name__=='__main__':unittest.main()
