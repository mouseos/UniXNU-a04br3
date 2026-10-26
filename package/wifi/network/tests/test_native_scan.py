# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Execute real native scan policy/end/edge logic with bounded byte fixtures."""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class NativeScan(unittest.TestCase):
 def test_cached_policy_and_observed_completion(self):
  case=r'''
#include "A04BR3NativeScan.h"
#include <assert.h>
#include <string.h>
int main(){
 uint8_t policy[5457]={};policy[0]=1;policy[4]=3;policy[0x34]=3;policy[0x38]=1;
 assert(!a04_native_scan_policy(0,policy,5456));
 for(unsigned n=0;n<5456;n++)assert(a04_native_scan_policy(0,policy,n)==EINVAL);
 assert(a04_native_scan_policy(0,policy,5457)==EINVAL);
 assert(a04_native_scan_policy(1,policy,5456)==EOPNOTSUPP);
 policy[0x34]=1;assert(a04_native_scan_policy(0,policy,5456)==EOPNOTSUPP);policy[0x34]=3;
 // Every noncanonical option is rejected, including hidden filters at the end.
 for(unsigned i=4;i<5456;i++){policy[i]^=0x80;assert(a04_native_scan_policy(0,policy,5456)==EOPNOTSUPP);policy[i]^=0x80;}
 a04_native_scan_state s={};uint8_t out[1165]={};out[0]=1;out[0x8b]=4;
 uint8_t before[1165];memcpy(before,out,sizeof(out));uint32_t n=1164;
 assert(a04_native_scan_get(&s,0,out,&n,0,1)==EOPNOTSUPP);
 assert(a04_native_scan_cached(&s,0,0)==EAGAIN && !s.cached_empty);
 assert(a04_native_scan_cached(&s,3,1)==EOPNOTSUPP && !s.cached_empty);
 assert(!a04_native_scan_cached(&s,0,1));
 for(unsigned i=0;i<5;i++){assert(a04_native_scan_get(&s,0,out,&n,0,1)==EIO);assert(n==1164 && !memcmp(out,before,sizeof(out)));}
 assert(a04_native_scan_get(&s,0,out,&n,0,0)==EAGAIN);
 assert(a04_native_scan_get(&s,0,out,&n,1,1)==EOPNOTSUPP);
 assert(!a04_native_scan_completion(&s,0));assert(!s.cached_empty);
 assert(a04_native_scan_completion(&s,1)==1);
 for(unsigned i=0;i<20;i++)assert(!a04_native_scan_completion(&s,1));
 assert(!a04_native_scan_completion(&s,0));assert(a04_native_scan_completion(&s,1)==1);
 for(unsigned len=0;len<1164;len++){n=len;assert(a04_native_scan_get(&s,0,out,&n,0,1)==EINVAL);}
 n=1164;out[0]=2;assert(a04_native_scan_get(&s,0,out,&n,0,1)==EINVAL);
 out[0]=1;out[0x8b]=3;assert(a04_native_scan_get(&s,0,out,&n,0,1)==EINVAL);
 assert(!a04_native_scan_cached(&s,0,1));assert(a04_native_scan_cached(&s,1,1)==EOPNOTSUPP && !s.cached_empty);
}
'''
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'case.cpp').write_text(case)
   subprocess.run(['c++','-std=c++14','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(ROOT),str(p/'case.cpp'),'-o',str(p/'case')],check=True)
   subprocess.run([str(p/'case')],check=True)
if __name__=='__main__':unittest.main()
