# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Actual artifact registration-field vectors; no fabricated listener/socket."""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class EAPOLProtocol(unittest.TestCase):
 def test_ndrv_registration_records(self):
  source=r'''
#include <assert.h>
#include <string.h>
#include "A04BR3NativeEAPOLProtocol.h"
int main(void){
 uint8_t p[64];memset(p,0xa5,sizeof(p));
 p[0]=4;p[1]=0;p[2]=2;p[3]=0;p[4]=0x88;p[5]=0x8e;
 p[32]=4;p[33]=0;p[34]=2;p[35]=0;p[36]=0x88;p[37]=0xc7;
 struct a04_native_eapol_demux result;
 assert(!a04_native_eapol_demux_decode(1,0x8021ec,p,64,2,&result)&&result.eapol&&result.preauth);
 assert(!a04_native_eapol_demux_decode(1,0x8021ec,p,32,1,&result)&&result.eapol&&!result.preauth);
 assert(a04_native_eapol_demux_decode(1,27,p,64,2,&result)==EOPNOTSUPP&&!result.eapol);
 assert(a04_native_eapol_demux_decode(2,0x8021ec,p,64,2,&result)==EOPNOTSUPP);
 for(unsigned i=0;i<64;i++)assert(a04_native_eapol_demux_decode(1,0x8021ec,p,i,2,&result)==EINVAL);
 p[37]=0x8e;assert(a04_native_eapol_demux_decode(1,0x8021ec,p,64,2,&result)==EINVAL);p[37]=0xc7;
 p[2]=3;assert(a04_native_eapol_demux_decode(1,0x8021ec,p,64,2,&result)==EOPNOTSUPP);p[2]=2;
 p[4]=8;assert(a04_native_eapol_demux_decode(1,0x8021ec,p,64,2,&result)==EOPNOTSUPP);
 assert(A04_NATIVE_EAPOL_INTEREST_DONE==0xe0000130u);return 0;
}
'''
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'test.c').write_text(source)
   subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(ROOT),str(p/'test.c'),'-o',str(p/'test')],check=True)
   subprocess.run([str(p/'test')],check=True)
   subprocess.run(['clang++','-std=c++14','-Wall','-Wextra','-Werror','-I',str(ROOT),'-x','c++','-fsyntax-only',str(p/'test.c')],check=True)
if __name__=='__main__':unittest.main()
