#!/usr/bin/env python3
"""Exercise the production SecureDT boundary with XNU's success=1 ABI."""
from pathlib import Path
import os, subprocess, tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'package/A04BR3Charger.cpp').read_text()
s=s.split('// BEGIN firmware DT lookup:',1)[1].split('// END firmware DT lookup',1)[0]
s=s[s.index('static bool firmwareHandoff'):]
pre=r'''
#include <cassert>
#include <cstring>
using DTEntry=void*;
// XNU pexpert/device_tree.h: kError=-1, kIterationDone=0, kSuccess=1.
enum { kError=-1, kIterationDone=0, kSuccess=1 };
static int entryStatus,propertyStatus,propertyCalls,chosenStorage;
static unsigned char stored[128];
static int SecureDTLookupEntry(DTEntry parent,const char*path,DTEntry*out){
 assert(parent==nullptr && !strcmp(path,"/chosen"));
 if(entryStatus==kSuccess)*out=&chosenStorage;
 return entryStatus;
}
static int SecureDTGetProperty(DTEntry entry,const char*key,const void**out,unsigned*length){
 ++propertyCalls;assert(entry==&chosenStorage && !strcmp(key,"a04br3-charger-owner"));
 if(propertyStatus==kSuccess){*out=stored;*length=sizeof stored;}
 return propertyStatus;
}
'''
post=r'''
int main(){
 for(int a:{kError,kIterationDone,kSuccess})for(int b:{kError,kIterationDone,kSuccess}){
  entryStatus=a;propertyStatus=b;propertyCalls=0;const void*p=nullptr;unsigned n=0;
  bool ok=firmwareHandoff(&p,&n);
  assert(ok==(a==kSuccess && b==kSuccess));
  assert(propertyCalls==(a==kSuccess?1:0));
  if(ok){assert(p==stored && n==128);}else{assert(p==nullptr && n==0);}
 }
}
'''
with tempfile.TemporaryDirectory() as name:
 d=Path(name);(d/'boundary.cpp').write_text('#include <initializer_list>\n'+pre+s+post)
 subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(d/'boundary.cpp'),'-o',str(d/'boundary')],check=True)
 subprocess.run([str(d/'boundary')],check=True)
print('PASS actual SecureDT adapter: success1, iterationDone0, error-1; property ordering/output contract')
