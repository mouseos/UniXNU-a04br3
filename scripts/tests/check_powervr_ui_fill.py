#!/usr/bin/env python3
"""Actual probe_vulkan UI-fill mode, Vulkan mocks and owner constructor; no GPU."""
from pathlib import Path
import os
import re
import runpy
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[2]
base=(ROOT/'scripts/tests/check_powervr_tq_first.py').read_text()
ns={'__file__':str(ROOT/'scripts/tests/check_powervr_tq_first.py')}
exec(base.split('# Map every actually invoked Vulkan operation')[0],ns)
h=ns['h'].replace('pixels[64*64*4]','pixels[388*12*4]')
h=h.replace('static char events[128];','static unsigned ui,skips,cbAllocs,fenceIds,submits;static char events[128];')
def replace(name,body):
 global h
 m=re.search(r'static [^\n]*?\b'+name+r'\(',h);assert m,name
 a=m.start();b=h.index('{',a);e=b+1;d=1
 while d:d+=(h[e]=='{')-(h[e]=='}');e+=1
 h=h[:a]+body+h[e:]
replace('probe_line',r'''static void probe_line(const char *fmt,...){if(strstr(fmt,"UI-fill SKIP"))skips++;}''')
replace('vkCreateImage_',r'''static VkResult vkCreateImage_(VkDevice d,const VkImageCreateInfo *i,const VkAllocationCallbacks *a,VkImage *v){(void)d;(void)a;assert(i->extent.width==(ui?388:64)&&i->extent.height==(ui?12:64));assert(i->format==(ui?VK_FORMAT_B8G8R8A8_UNORM:VK_FORMAT_R8G8B8A8_UNORM));assert(i->tiling==VK_IMAGE_TILING_OPTIMAL);++images;*v=HANDLE(VkImage);return VK_SUCCESS;}''')
replace('vkAllocateCommandBuffers_',r'''static VkResult vkAllocateCommandBuffers_(VkDevice d,const VkCommandBufferAllocateInfo *i,VkCommandBuffer *p){(void)d;assert(i->commandBufferCount==1);*p=(VkCommandBuffer)(uintptr_t)++cbAllocs;return VK_SUCCESS;}''')
replace('vkCreateFence_',r'''static VkResult vkCreateFence_(VkDevice d,const VkFenceCreateInfo *i,const VkAllocationCallbacks *a,VkFence *f){(void)d;(void)i;(void)a;assert(!fences);++fences;*f=(VkFence)(uintptr_t)++fenceIds;return VK_SUCCESS;}''')
replace('vkQueueSubmit_',r'''static VkResult vkQueueSubmit_(VkQueue q,uint32_t n,const VkSubmitInfo *i,VkFence f){(void)q;assert(n==1&&i->commandBufferCount==1);++submits;assert((uintptr_t)*i->pCommandBuffers==submits&&(uintptr_t)f==submits);assert(stage==submits);if(stage==2)assert(waits==1);if(stage==1&&!strcmp(failure,"clear-submit"))return VK_ERROR_DEVICE_LOST;return VK_SUCCESS;}''')
replace('vkCmdCopyImageToBuffer_',r'''static void vkCmdCopyImageToBuffer_(VkCommandBuffer c,VkImage i,VkImageLayout l,VkBuffer b,uint32_t n,const VkBufferImageCopy *r){(void)c;(void)i;(void)l;(void)b;assert(n==1&&r->imageExtent.width==(ui?388:64)&&r->imageExtent.height==(ui?12:64));assert(waits==1);stage=2;event('B');}''')
h=h.replace('pixels[i]=kClear[i%4]','pixels[i]=kClear[ui?(i%4==0?2:i%4==2?0:i%4):i%4]')
names=re.findall(r'static (?:VkResult|void|PFN_vkVoidFunction) (vk\w+)_\(',h)
h+='static PFN_vkVoidFunction lookup(const char *name){\n'
for name in names:h+=f'if(!strcmp(name,"{name}"))return (PFN_vkVoidFunction){name}_;\n'
h+='return (PFN_vkVoidFunction)dummy;}\n'+ns['macros']
h+='#define DECLARE(name) static PFN_##name p_##name;\nVK_FUNCTIONS(DECLARE)\nVK_DEVICE_FUNCTIONS(DECLARE)\n#undef DECLARE\n'
h+=ns['function']('int probe_vulkan(')
h+=r'''
int main(int argc,char **argv){assert(argc==4);failure=argv[2];ui=!strcmp(argv[1],"1");
if(!strcmp(argv[1],"absent"))unsetenv("PVR_PROBE_UI_FILL");else setenv("PVR_PROBE_UI_FILL",argv[1],1);
unsetenv("PVR_PROBE_TQ_FIRST");int r=probe_vulkan("mock");assert(!strcmp(events,argv[3]));assert((r==0)==!strcmp(failure,"ok"));assert(skips==ui);assert(!memories&&!images&&!buffers&&!pools&&!fences);
assert(cbAllocs==(!strncmp(failure,"clear-",6)?1u:2u));assert(submits==cbAllocs&&fenceIds==submits);
printf("PASS actual UI-fill mode=%s failure=%s events=%s\n",argv[1],failure,events);}
'''
builder=runpy.run_path(str(ROOT/'scripts/build-a04br3-powervr-probe.py'))
with tempfile.TemporaryDirectory(prefix='pvr-ui-fill-') as t:
 p=Path(t);src=p/'t.c';exe=p/'test';src.write_text(h)
 cc=[os.environ.get('CC','cc'),'-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined','-fno-pie','-no-pie','-g']
 subprocess.run(cc+['-I'+str(ROOT/'.cache/mesa-pvr/include'),str(src),'-o',str(exe)],check=True)
 cases=[('1','ok','CBK'),('1','clear-submit','C'),('1','clear-fence','C'),('1','copy-fence','CB'),('1','readback','CBK')]
 cases += [(flag,'ok','LRCBKOH') for flag in ('absent','0','2','01','true')]
 for flag,fail,events in cases:subprocess.run([str(exe),flag,fail,events],check=True,timeout=30)
 ctor=builder['ui_fill_probe_source']()
 src.write_text(ctor+'\n#include <assert.h>\n#include <string.h>\nint main(void){assert(!strcmp(getenv("PVR_PROBE_UI_FILL"),"1"));assert(getenv("PVR_DARWIN_TQ_CLIP")==NULL);assert(getenv("PVR_PROBE_TQ_FIRST")==NULL);}\n')
 subprocess.run(cc+[str(src),'-o',str(exe)],check=True)
 env=dict(os.environ)
 for k in ('PVR_PROBE_UI_FILL','PVR_DARWIN_TQ_CLIP','PVR_PROBE_TQ_FIRST'):env.pop(k,None)
 subprocess.run([str(exe)],env=env,check=True)
 src.write_text(ctor.replace('setenv(', 'fail_setenv(').replace('#include <stdio.h>','#include <stdio.h>\nstatic int fail_setenv(const char*a,const char*b,int c){(void)a;(void)b;(void)c;return -1;}')+'\nint main(void){return 99;}\n')
 subprocess.run(cc+[str(src),'-o',str(exe)],check=True)
 r=subprocess.run([str(exe)],capture_output=True,text=True);assert r.returncode==1 and 'cannot enable UI-fill' in r.stderr
r=subprocess.run([sys.executable,str(ROOT/'scripts/build-a04br3-powervr-probe.py'),'--check','--ui-fill'],capture_output=True,text=True)
assert r.returncode==2 and '--ui-fill requires --vulkan' in r.stderr
with tempfile.TemporaryDirectory(prefix='pvr-probe-dump-') as t:
 p=Path(t);src=p/'t.c';exe=p/'test'
 ctor=builder['sipf1_dump_probe_source']()
 src.write_text(ctor+'\n#include <assert.h>\n#include <string.h>\nint main(void){assert(!strcmp(getenv("PVR_DARWIN_SIPF1_DUMP"),"1"));assert(getenv("PVR_PROBE_UI_FILL")==NULL);assert(getenv("PVR_DARWIN_TQ_CLIP")==NULL);assert(getenv("PVR_PROBE_TQ_FIRST")==NULL);}\n')
 subprocess.run(cc+[str(src),'-o',str(exe)],check=True)
 env=dict(os.environ)
 for k in ('PVR_DARWIN_SIPF1_DUMP','PVR_PROBE_UI_FILL','PVR_DARWIN_TQ_CLIP','PVR_PROBE_TQ_FIRST'):env.pop(k,None)
 subprocess.run([str(exe)],env=env,check=True)
 src.write_text(ctor.replace('setenv(', 'fail_setenv(').replace('#include <stdio.h>','#include <stdio.h>\nstatic int fail_setenv(const char*a,const char*b,int c){(void)a;(void)b;(void)c;return -1;}')+'\nint main(void){return 99;}\n')
 subprocess.run(cc+[str(src),'-o',str(exe)],check=True)
 r=subprocess.run([str(exe)],capture_output=True,text=True);assert r.returncode==1 and 'cannot enable SIPF1 dump' in r.stderr
r=subprocess.run([sys.executable,str(ROOT/'scripts/build-a04br3-powervr-probe.py'),'--check','--sipf1-dump'],capture_output=True,text=True)
assert r.returncode==2 and '--sipf1-dump requires --vulkan' in r.stderr
print('PASS independent SIPF1-dump constructor, failed-init exit and Vulkan CLI gate')
print('PASS 10 actual-probe ASan/UBSan mode/order/failure/BGRA/cleanup cases + constructor/failure/CLI gate; no GPU test')
