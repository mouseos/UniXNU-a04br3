#!/usr/bin/env python3
"""Compile actual submit function with mocked transport, clock, waits and locks."""
from pathlib import Path
import subprocess, tempfile, runpy, sys
R=Path(__file__).resolve().parents[2]
s=(R/'.cache/mesa-pvr/src/imagination/vulkan/winsys/darwin/pvr_darwin.c').read_text()
f=s[s.index('VkResult pvr_darwin_submit_jobs('):s.index('/* The signal resolves',s.index('VkResult pvr_darwin_submit_jobs('))]
pre=r'''#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <stdio.h>
typedef int VkResult; typedef int kern_return_t;
#define VK_SUCCESS 0
#define VK_ERROR_DEVICE_LOST -4
#define VK_ERROR_OUT_OF_DEVICE_MEMORY -2
#define KERN_SUCCESS 0
#define VK_SYNC_IS_TIMELINE 1
#define NativePowerVRMethodSubmitJobs 1
struct drm_pvr_job {int x;};
struct drm_pvr_ioctl_submit_jobs_args {struct {uint32_t count,stride;} jobs;};
struct NativePowerVRAbiSubmitReply {uint64_t fence[4];};
struct vk_sync {int flags;};
struct pvr_darwin_fence {int fd;uint64_t value;};
struct pvr_darwin_sync {struct vk_sync base;struct pvr_darwin_fence fences[4];int count,resolved;};
struct pvr_darwin_signal {struct vk_sync *sync;uint32_t job;};
static struct {uint32_t max_jobs;int lock,resolved;} pvr_dw={4,0,0};
static int wc,mode,submitted,locked;static uint64_t now=100,dl;
static struct pvr_darwin_sync *to_pvr_darwin_sync(struct vk_sync*s){return (void*)s;}
#define vk_errorf(...) VK_ERROR_DEVICE_LOST
static int pvr_darwin_sync_wait_one(struct pvr_darwin_sync*s,uint64_t d){(void)s;(void)d;return 0;}
static uint64_t os_time_get_nano(void){return now;}
static int pvr_darwin_call(int fd,int op,const void*a,size_t as,void*r,size_t rs){(void)fd;(void)op;(void)a;(void)as;(void)rs;submitted++;struct NativePowerVRAbiSubmitReply*p=r;for(int i=0;i<4;i++)p->fence[i]=(mode==3&&i==1)?0:i+1;return 0;}
static int pvr_darwin_wait_fence(const struct pvr_darwin_fence*f,uint64_t d){assert(submitted&&!locked);assert(f->value==(uint64_t)wc+1);if(wc)assert(dl==d);dl=d;wc++;if(mode==1)return -4;if(mode==2)return 2;if(mode==4)now=d;return 0;}
static void pthread_mutex_lock(int*l){assert(!*l);*l=1;locked++;}
static void pthread_mutex_unlock(int*l){assert(*l);*l=0;}
static void pthread_cond_broadcast(int*c){(void)c;}
'''
main=r'''
int main(void){struct drm_pvr_ioctl_submit_jobs_args a={{4,sizeof(struct drm_pvr_job)}};struct pvr_darwin_sync sync={0};struct pvr_darwin_signal sig={&sync.base,3};
for(int m=0;m<8;m++){mode=m;wc=submitted=locked=0;now=m==7?UINT64_MAX-5:100;sync.resolved=0;setenv("PVR_DARWIN_SERIAL_TRANSFER",m==5?"01":"1",1);int r=pvr_darwin_submit_jobs(2,&a,0,0,&sig,1,m==6?"geometry":"transfer");if(m>=1&&m<=4){assert(r==-4&&!sync.resolved&&!locked);}else{assert(!r&&sync.resolved&&locked==1);assert(wc==((m==5||m==6)?0:4));}if(m==0)assert(dl==10000000100ull);if(m==7)assert(dl==UINT64_MAX-1);}
unsetenv("PVR_DARWIN_SERIAL_TRANSFER");wc=locked=0;assert(!pvr_darwin_submit_jobs(2,&a,0,0,&sig,1,"transfer")&&!wc);puts("PASS actual submit: all fences, shared deadline, failure/timeout/zero, overflow, strict/default gates, geometry skip, waits before lock");}
'''
with tempfile.TemporaryDirectory() as t:
 p=Path(t);(p/'t.c').write_text(pre+f+main);subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(p/'t.c'),'-o',str(p/'t')],check=True);subprocess.run([str(p/'t')],check=True)
b=R/'scripts/build-a04br3-powervr-probe.py'
ctor=runpy.run_path(str(b))['serial_transfer_probe_source']()
assert 'PVR_DARWIN_SERIAL_TRANSFER' in ctor and 'PVR_DARWIN_TQ_CLIP' not in ctor
r=subprocess.run([sys.executable,str(b),'--check','--serial-transfer'],capture_output=True,text=True)
assert r.returncode==2 and '--serial-transfer requires --vulkan' in r.stderr

with tempfile.TemporaryDirectory() as t:
 p=Path(t)
 (p/'ctor.c').write_text(ctor+'\n#include <assert.h>\n#include <string.h>\nint main(void){assert(!strcmp(getenv("PVR_DARWIN_SERIAL_TRANSFER"),"1"));return 0;}\n')
 subprocess.run(['cc','-D_POSIX_C_SOURCE=200809L',str(p/'ctor.c'),'-o',str(p/'ctor')],check=True)
 subprocess.run([str(p/'ctor')],check=True)
 failed=ctor.replace('setenv(', 'fail_setenv(').replace('#include <stdio.h>', '#include <stdio.h>\nstatic int fail_setenv(const char*a,const char*b,int c){(void)a;(void)b;(void)c;return -1;}')
 (p/'ctor.c').write_text(failed+'\nint main(void){return 99;}\n')
 subprocess.run(['cc','-D_POSIX_C_SOURCE=200809L',str(p/'ctor.c'),'-o',str(p/'ctor')],check=True)
 r=subprocess.run([str(p/'ctor')],capture_output=True,text=True)
 assert r.returncode==1 and 'cannot enable serial transfer comparison' in r.stderr
print('PASS actual serial constructor, failed-setenv exit, independent gate and Vulkan CLI requirement')
