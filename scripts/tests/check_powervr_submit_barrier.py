#!/usr/bin/env python3
"""Shipped canonical helper/submit order; ARM64 compiler output, no disassembly."""
from pathlib import Path
import ast, os, runpy, subprocess, sys, tempfile
R=Path(__file__).resolve().parents[2]
patch=(R/'patches/mesa/pvr-darwin-winsys.patch').read_text()
added='\n'.join(x[1:] for x in patch.splitlines() if x.startswith('+') and not x.startswith('+++'))
def extract(s,sig):
 a=s.index(sig);e=s.index('{',a)+1;d=1
 while d:d+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[a:e]
helper=extract(added,'static void pvr_darwin_submit_barrier(')
assert '__asm__ volatile("dsb sy" ::: "memory")' in helper
# Reconstruct the shipped file from its pinned upstream and canonical patch.
# The submit function is wholly an added file in the patch.
f=extract(added,'VkResult pvr_darwin_submit_jobs(')
assert 'pvr_darwin_submit_barrier();\n   kr = pvr_darwin_call(fd,' in f
old=ast.parse((R/'scripts/tests/check_powervr_serial_transfer.py').read_text())
pre=next(ast.literal_eval(n.value) for n in old.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='pre' for t in n.targets))
pre=pre.replace('submitted++;struct NativePowerVRAbiSubmitReply', 'assert(barriers==expected);submitted++;struct NativePowerVRAbiSubmitReply')
pre=pre.replace('static int wc,mode,submitted,locked;', 'static int barriers,expected;static int wc,mode,submitted,locked;')
mock=helper.replace('__asm__ volatile("dsb sy" ::: "memory")','(barriers++)')
main=r'''
int main(void){struct drm_pvr_ioctl_submit_jobs_args a={{1,sizeof(struct drm_pvr_job)}};const char*flags[]={NULL,"","0","01","2","true","1"};unsetenv("PVR_DARWIN_SERIAL_TRANSFER");
for(unsigned i=0;i<7;i++){if(flags[i])setenv("PVR_DARWIN_SUBMIT_BARRIER",flags[i],1);else unsetenv("PVR_DARWIN_SUBMIT_BARRIER");barriers=submitted=locked=0;expected=(i==6);
#ifndef __aarch64__
expected=0;
#endif
assert(!pvr_darwin_submit_jobs(2,&a,0,0,NULL,0,"transfer"));assert(submitted==1&&barriers==expected);}
barriers=submitted=locked=0;a.jobs.count=0;assert(pvr_darwin_submit_jobs(2,&a,0,0,NULL,0,"transfer")!=0);assert(!barriers&&!submitted);
}
'''
with tempfile.TemporaryDirectory(prefix='pvr-submit-barrier-') as t:
 p=Path(t);(p/'test.c').write_text(pre+mock+f+main)
 for arm in (False,True):
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',*(['-D__aarch64__'] if arm else []),str(p/'test.c'),'-o',str(p/'test')],check=True)
  subprocess.run([str(p/'test')],check=True)
 (p/'arm.c').write_text('extern char *getenv(const char*);extern int strcmp(const char*,const char*);\n'+helper+'\nvoid entry(void){pvr_darwin_submit_barrier();}\n')
 subprocess.run(['clang','-target','arm64-apple-ios17.0','-ffreestanding','-O2','-S',str(p/'arm.c'),'-o',str(p/'arm.s')],check=True)
 assembly=(p/'arm.s').read_text();assert 'dsb\tsy' in assembly or 'dsb sy' in assembly
 assert assembly.count('dsb')==1
 # Actual owner constructor, independent of routing/dump/serialization.
 b=R/'scripts/build-a04br3-powervr-probe.py';ctor=runpy.run_path(str(b))['submit_barrier_probe_source']()
 (p/'ctor.c').write_text(ctor+'\n#include <assert.h>\n#include <string.h>\nint main(void){assert(!strcmp(getenv("PVR_DARWIN_SUBMIT_BARRIER"),"1"));assert(!getenv("PVR_DARWIN_TQ_CLIP")&&!getenv("PVR_DARWIN_SIPF1_DUMP")&&!getenv("PVR_DARWIN_SERIAL_TRANSFER"));}\n')
 env=dict(os.environ)
 for key in ('PVR_DARWIN_TQ_CLIP','PVR_DARWIN_SIPF1_DUMP','PVR_DARWIN_SERIAL_TRANSFER'):env.pop(key,None)
 subprocess.run(['cc','-D_POSIX_C_SOURCE=200809L',str(p/'ctor.c'),'-o',str(p/'ctor')],check=True);subprocess.run([str(p/'ctor')],env=env,check=True)
 failed=ctor.replace('setenv(', 'fail_setenv(').replace('#include <stdio.h>','#include <stdio.h>\nstatic int fail_setenv(const char*a,const char*b,int c){(void)a;(void)b;(void)c;return -1;}')
 (p/'ctor.c').write_text(failed+'\nint main(void){return 99;}\n');subprocess.run(['cc','-D_POSIX_C_SOURCE=200809L',str(p/'ctor.c'),'-o',str(p/'ctor')],check=True)
 r=subprocess.run([str(p/'ctor')],capture_output=True,text=True);assert r.returncode==1 and 'cannot enable submit barrier comparison' in r.stderr
 r=subprocess.run([sys.executable,str(b),'--check','--submit-barrier'],capture_output=True,text=True);assert r.returncode==2 and '--submit-barrier requires --vulkan' in r.stderr
print('PASS canonical actual helper+submit order: strict/default gates, non-ARM no-op, invalid-submit rejection, ARM64 -S dsb sy, independent ctor/failure/CLI; ASan/UBSan; no GPU execution')
