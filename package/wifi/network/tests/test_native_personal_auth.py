# SPDX-License-Identifier: BSD-2-Clause-Patent
"""REA-backed native Personal AUTH fields, not association or port success."""
import subprocess,tempfile,unittest
from pathlib import Path
HEADER=Path(__file__).resolve().parents[1]/'A04BR3NativePersonalAuth.h'
class PersonalAuth(unittest.TestCase):
 def test_actual_producer_offsets_and_unsupported_auth(self):
  source='#include <assert.h>\n#include <string.h>\n#include "'+str(HEADER)+'"\n'+r'''
int main(void){uint8_t storage[594]={0},*p=storage+1;p[0]=1;p[4]=2;p[6]=1;p[8]=8;
 struct a04_native_personal_auth out={77,88};
 assert(!a04_native_personal_auth_decode(0,p,592,&out)&&out.upper==8&&!out.producer_flags);
 p[504]=4;assert(!a04_native_personal_auth_decode(0,p,592,&out)&&out.producer_flags==4);
 for(unsigned i=0;i<16;i++){if(i==3)continue;p[8]=(uint8_t)(1u<<i);p[9]=(uint8_t)((1u<<i)>>8);assert(a04_native_personal_auth_decode(0,p,592,&out)==EOPNOTSUPP);}
 p[8]=8;p[9]=0;
 for(unsigned i=0;i<32;i++){if(i==2)continue;memset(p+504,0,4);p[504+i/8]=(uint8_t)(1u<<(i%8));assert(a04_native_personal_auth_decode(0,p,592,&out)==EOPNOTSUPP);}
 memset(p+504,0,4);p[6]=2;assert(a04_native_personal_auth_decode(0,p,592,&out)==EOPNOTSUPP);p[6]=1;
 p[4]=1;assert(a04_native_personal_auth_decode(0,p,592,&out)==EOPNOTSUPP);p[4]=2;
 for(unsigned n=0;n<592;n++)assert(a04_native_personal_auth_decode(0,p,n,&out)==EINVAL);
 assert(a04_native_personal_auth_decode(1,p,592,&out)==EOPNOTSUPP);
 assert(a04_native_personal_auth_decode(0,0,592,&out)==EINVAL);return 0;}
'''
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'case.c').write_text(source)
   subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'case.c'),'-o',str(p/'case')],check=True)
   subprocess.run([str(p/'case')],check=True)
   subprocess.run(['c++','-std=c++14','-Wall','-Wextra','-Werror','-x','c++','-fsyntax-only',str(p/'case.c')],check=True)
if __name__=='__main__':unittest.main()
