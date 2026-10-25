#!/usr/bin/env python3
"""Actual shipped route + owner ctor: strict stderr-only bypass before lock/IOKit."""
from pathlib import Path
import ast, os, runpy, subprocess, sys, tempfile
R=Path(__file__).resolve().parents[2]
s='\n'.join(l[1:] for l in (R/'patches/mesa/pvr-darwin-winsys.patch').read_text().splitlines() if l.startswith('+') and not l.startswith('+++'))
a=s.index('void pvr_darwin_sipf1_log(const char *text)\n{');e=s.index('{',a)+1;depth=1
while depth:depth+=(s[e]=='{')-(s[e]=='}');e+=1
actual=s[a:e]
assert actual.index('stderr_only')<actual.index('pthread_mutex_lock')<actual.index('IOServiceMatching')
# Reuse the existing full marker transport mock against the actual canonical body.
tree=ast.parse((R/'scripts/check-pvr-sipf1-route.py').read_text())
node=next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='h' for t in n.targets))
h=eval(compile(ast.Expression(node),'<route fixture>','eval'),{'actual':actual})
wrap=r'''
static unsigned locks;
static int counted_lock(pthread_mutex_t*p){locks++;return pthread_mutex_lock(p);}
#define pthread_mutex_lock counted_lock
'''
h=h.replace(actual,wrap+actual,1)
h=h.replace('int main(void){','int main(void){\nunsetenv("PVR_DARWIN_SIPF1_STDERR_ONLY");',1)
needle='mode=1;pvr_darwin_sipf1_log("a");'
addition=r'''
setenv("PVR_DARWIN_SIPF1_STDERR_ONLY","1",1);clear();unsigned before=locks;
pvr_darwin_sipf1_log("stderr already emitted by caller");
assert(locks==before&&!matchingCalls&&!lookups&&!opens&&!releases&&!closes);
const char *not_one[]={"","0","01","2","true"};
for(unsigned i=0;i<5;i++){clear();setenv("PVR_DARWIN_SIPF1_STDERR_ONLY",not_one[i],1);pvr_darwin_sipf1_log("strict");assert(matchingCalls==1&&lookups==1&&opens>0&&lines==1);}
unsetenv("PVR_DARWIN_SIPF1_STDERR_ONLY");clear();
'''
assert needle in h;h=h.replace(needle,addition+needle,1)
with tempfile.TemporaryDirectory(prefix='pvr-stderr-only-') as t:
 p=Path(t);(p/'route.c').write_text(h)
 subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-pthread','-fsanitize=address,undefined','-fno-pie','-no-pie',str(p/'route.c'),'-o',str(p/'route')],check=True)
 subprocess.run([str(p/'route')],check=True)
 b=R/'scripts/build-a04br3-powervr-probe.py';ctor=runpy.run_path(str(b))['sipf1_stderr_only_probe_source']()
 (p/'ctor.c').write_text(ctor+'\n#include <assert.h>\n#include <string.h>\nint main(void){assert(!strcmp(getenv("PVR_DARWIN_SIPF1_STDERR_ONLY"),"1"));assert(!getenv("PVR_DARWIN_SIPF1_DUMP")&&!getenv("PVR_DARWIN_SIPF1_TOUCH")&&!getenv("PVR_DARWIN_SUBMIT_BARRIER"));}\n')
 env=dict(os.environ)
 for key in ('PVR_DARWIN_SIPF1_DUMP','PVR_DARWIN_SIPF1_TOUCH','PVR_DARWIN_SUBMIT_BARRIER'):env.pop(key,None)
 subprocess.run(['cc','-D_POSIX_C_SOURCE=200809L',str(p/'ctor.c'),'-o',str(p/'ctor')],check=True);subprocess.run([str(p/'ctor')],check=True,env=env)
 failed=ctor.replace('setenv(', 'fail_setenv(').replace('#include <stdio.h>','#include <stdio.h>\nstatic int fail_setenv(const char*a,const char*b,int c){(void)a;(void)b;(void)c;return -1;}')
 (p/'ctor.c').write_text(failed+'\nint main(void){return 99;}\n');subprocess.run(['cc','-D_POSIX_C_SOURCE=200809L',str(p/'ctor.c'),'-o',str(p/'ctor')],check=True)
 r=subprocess.run([str(p/'ctor')],capture_output=True,text=True);assert r.returncode==1 and 'cannot enable SIPF1 stderr-only comparison' in r.stderr
 for extra,message in (([], '--vulkan'),(['--sipf1-dump'],'--vulkan'),(['--vulkan',str(p/'dummy')],'--sipf1-dump')):
  r=subprocess.run([sys.executable,str(b),'--check','--sipf1-stderr-only',*extra],capture_output=True,text=True);assert r.returncode==2 and '--sipf1-stderr-only requires '+message in r.stderr
print('PASS stderr-only exact gate: no lock/IOKit calls, invalid/default flags retain full marker flow, independent ctor/failure and both CLI prerequisites; ASan/UBSan; no GPU execution')
