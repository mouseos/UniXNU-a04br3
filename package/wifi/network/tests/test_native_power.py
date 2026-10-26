# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Observed native power SET codec, no synthetic backend state transitions."""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class NativePower(unittest.TestCase):
 def test_real_backend_dispatch_and_rejections(self):
  code=r'''
#include "A04BR3NativePower.h"
#include <assert.h>
#include <string.h>
static a04_network_state observed={1,1,1,1,0,0,{2,3,4,5,6,7},1,0,0,0,0,0,0,0,0,0,0};
static int calls,stateError,commandError;static unsigned desired;
static int state(void *,a04_network_state *s){*s=observed;return stateError;}
static int admin(void *,unsigned value){calls++;desired=value;return commandError;}
static void put(unsigned char *p,unsigned v){for(unsigned i=0;i<4;i++)p[i]=(unsigned char)(v>>(8*i));}
int main(){
 a04_network_backend b={};b.state=state;b.set_admin=admin;
 // Unaligned input must also be safe: decoder uses byte reads, never casts.
 unsigned char storage[26];auto *p=storage+1;memset(storage,0xa5,sizeof(storage));
 memset(p,0,24);put(p,1);put(p+4,1);put(p+8,1);
 for(unsigned n=0;n<26;n++)if(n!=24){int before=calls;assert(a04_native_power_set(&b,0,p,n)==EINVAL && calls==before);}
 assert(a04_native_power_set(nullptr,0,p,24)==EINVAL);
 assert(a04_native_power_set(&b,0,nullptr,24)==EINVAL);
 assert(a04_native_power_set(&b,1,p,24)==EOPNOTSUPP && !calls);
 for(unsigned count=1;count<=4;count++)for(unsigned value=0;value<2;value++){
  put(p+4,count);for(unsigned i=0;i<4;i++)put(p+8+i*4,value);
  int before=calls;auto prior=observed;
  assert(!a04_native_power_set(&b,0,p,24) && calls==before+1 && desired==value);
  assert(!memcmp(&observed,&prior,sizeof(prior))); // callback result cannot fabricate a state
 }
 int before=calls;
 put(p,2);assert(a04_native_power_set(&b,0,p,24)==EINVAL);put(p,1);
 put(p+4,0);assert(a04_native_power_set(&b,0,p,24)==EINVAL);
 put(p+4,5);assert(a04_native_power_set(&b,0,p,24)==EINVAL);
 put(p+4,1);put(p+8,2);assert(a04_native_power_set(&b,0,p,24)==EOPNOTSUPP);
 put(p+4,4);put(p+8,0);put(p+12,1);assert(a04_native_power_set(&b,0,p,24)==EOPNOTSUPP);
 assert(calls==before);put(p+4,1);put(p+8,1);
 stateError=EIO;assert(a04_native_power_set(&b,0,p,24)==EIO && calls==before);stateError=0;
 observed.control_ready=0;assert(a04_native_power_set(&b,0,p,24)==ENETDOWN && calls==before);observed.control_ready=1;
 auto saved=b.set_admin;b.set_admin=nullptr;assert(a04_native_power_set(&b,0,p,24)==EOPNOTSUPP);b.set_admin=saved;
 for(int error:{EOPNOTSUPP,EIO,EBUSY}){commandError=error;assert(a04_native_power_set(&b,0,p,24)==error && observed.radio_enabled==1);}
 assert(storage[0]==0xa5 && storage[25]==0xa5 && !observed.associated && !observed.datapath_ready);
}
'''.replace('#include <assert.h>','#include <assert.h>\n#include <initializer_list>')
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'test.cpp').write_text(code)
   subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(ROOT),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
   subprocess.run([str(p/'test')],check=True)
if __name__=='__main__':unittest.main()
