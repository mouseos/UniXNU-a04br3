#!/usr/bin/env python3
"""Actual four-mip probe main and barrier helper with Vulkan mocks; no GPU."""
from pathlib import Path
import os
import runpy
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[2]
# Reuse signature-correct mocks, without executing the UI-fill suite.
foundation=(ROOT/'scripts/tests/check_powervr_ui_fill.py').read_text()
ns={'__file__':str(ROOT/'scripts/tests/check_powervr_ui_fill.py')}
exec(foundation.split("builder=runpy.run_path")[0],ns)
h=ns['h'];end=h.index('int main(int argc,char **argv)');h=h[:end]
# Use its replacement helper against this generated fixture.
ns['h']=h
replace=ns['replace']
def change(name,body):
 global h
 ns['h']=h;replace(name,body);h=ns['h']
h=h.replace('pixels[388*12*4]','pixels[6159*4]').replace('static unsigned ui,skips,','static unsigned barrierCount,idleCalls,ui,skips,')
change('probe_line',r'''static void probe_line(const char *fmt,...){if(strstr(fmt,"SKIP: original"))skips++;}''')
# Replace the no-op mock with the actual production helper after function pointers exist.
change('imageBarrierLevels','')
actual=ns['ns']['function']('static void imageBarrierLevels(')
pos=h.index('int probe_vulkan(');h=h[:pos]+actual+'\n'+h[pos:]
change('vkCreateImage_',r'''static VkResult vkCreateImage_(VkDevice d,const VkImageCreateInfo *i,const VkAllocationCallbacks *a,VkImage *v){(void)d;(void)a;assert(i->extent.width==(ui?388:64)&&i->extent.height==(ui?12:64)&&i->mipLevels==(ui?4:1));assert(i->format==(ui?VK_FORMAT_B8G8R8A8_UNORM:VK_FORMAT_R8G8B8A8_UNORM)&&i->tiling==VK_IMAGE_TILING_OPTIMAL);++images;*v=HANDLE(VkImage);return VK_SUCCESS;}''')
change('vkCreateBuffer_',r'''static VkResult vkCreateBuffer_(VkDevice d,const VkBufferCreateInfo *i,const VkAllocationCallbacks *a,VkBuffer *v){(void)d;(void)a;assert(i->size==(ui?6159u:4096u)*4);++buffers;*v=HANDLE(VkBuffer);return VK_SUCCESS;}''')
change('vkCmdPipelineBarrier_',r'''static void vkCmdPipelineBarrier_(VkCommandBuffer c,VkPipelineStageFlags s,VkPipelineStageFlags d,VkDependencyFlags f,uint32_t n,const VkMemoryBarrier *m,uint32_t bn,const VkBufferMemoryBarrier *b,uint32_t in,const VkImageMemoryBarrier *i){(void)c;(void)s;(void)d;(void)f;(void)n;(void)m;(void)bn;(void)b;if(in){assert(in==1&&i->subresourceRange.baseMipLevel==0&&i->subresourceRange.levelCount==(ui?4:1));assert(i->subresourceRange.baseArrayLayer==0&&i->subresourceRange.layerCount==1);if(!barrierCount)assert(i->oldLayout==VK_IMAGE_LAYOUT_UNDEFINED&&i->newLayout==VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL&&!waits);else assert(barrierCount==1&&waits==1&&i->oldLayout==VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL&&i->newLayout==VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);barrierCount++;}}''')
change('vkCmdClearColorImage_',r'''static void vkCmdClearColorImage_(VkCommandBuffer c,VkImage i,VkImageLayout l,const VkClearColorValue *v,uint32_t n,const VkImageSubresourceRange *r){(void)c;(void)i;(void)l;(void)v;assert(!stage&&!waits&&n==1&&r->baseMipLevel==0&&r->levelCount==(ui?4:1));assert(images==1&&buffers==1&&memories==2&&pools==1);stage=1;event('C');}''')
change('vkCmdCopyImageToBuffer_',r'''static void vkCmdCopyImageToBuffer_(VkCommandBuffer c,VkImage i,VkImageLayout l,VkBuffer b,uint32_t n,const VkBufferImageCopy *r){(void)c;(void)i;(void)l;(void)b;assert(waits==1&&n==(ui?4:1));const unsigned widths[]={388,194,97,48},heights[]={12,6,3,1};uint64_t offset=0;for(unsigned j=0;j<n;j++){assert(r[j].bufferOffset==offset&&!r[j].bufferRowLength&&!r[j].bufferImageHeight);assert(r[j].imageSubresource.mipLevel==j&&r[j].imageSubresource.aspectMask==VK_IMAGE_ASPECT_COLOR_BIT&&r[j].imageSubresource.baseArrayLayer==0&&r[j].imageSubresource.layerCount==1);assert(r[j].imageExtent.width==(ui?widths[j]:64)&&r[j].imageExtent.height==(ui?heights[j]:64)&&r[j].imageExtent.depth==1);assert(!r[j].imageOffset.x&&!r[j].imageOffset.y&&!r[j].imageOffset.z);offset+=(uint64_t)r[j].imageExtent.width*r[j].imageExtent.height*4;}assert(offset==(ui?6159u:4096u)*4);stage=2;event('B');}''')
change('vkWaitForFences_',r'''static VkResult vkWaitForFences_(VkDevice d,uint32_t n,const VkFence *f,VkBool32 all,uint64_t t){(void)d;(void)f;assert(all&&n==1&&t==10000000000ull);++waits;assert(images==1&&buffers==1&&memories==2&&pools==1);if((stage==1&&!strcmp(failure,"clear-fence"))||(stage==2&&!strcmp(failure,"copy-fence")))return VK_ERROR_DEVICE_LOST;if(stage==2){unsigned size=(ui?6159:4096)*4;for(unsigned j=0;j<size;j++)pixels[j]=kClear[ui?(j%4==0?2:j%4==2?0:j%4):j%4];if(!strncmp(failure,"mip",3)){unsigned mip=failure[3]-'0';const unsigned ends[]={4656*4-1,5820*4-1,6111*4-1,6159*4-1};assert(ui&&mip<4);pixels[ends[mip]]^=1;}}return VK_SUCCESS;}''')
change('vkDeviceWaitIdle_',r'''static VkResult vkDeviceWaitIdle_(VkDevice d){(void)d;idleCalls++;return VK_SUCCESS;}''')
change('vkDestroyImage_',r'''static void vkDestroyImage_(VkDevice d,VkImage v,const VkAllocationCallbacks *a){(void)d;(void)v;(void)a;assert(images&&idleCalls);--images;}''')
change('vkFreeMemory_',r'''static void vkFreeMemory_(VkDevice d,VkDeviceMemory m,const VkAllocationCallbacks *a){(void)d;(void)m;(void)a;assert(memories&&idleCalls);--memories;}''')
h+=r'''
int main(int argc,char **argv){assert(argc==4);failure=argv[2];ui=!strcmp(argv[1],"1");if(!strcmp(argv[1],"absent"))unsetenv("PVR_PROBE_UI_BATCH");else setenv("PVR_PROBE_UI_BATCH",argv[1],1);unsetenv("PVR_PROBE_UI_FILL");unsetenv("PVR_PROBE_TQ_FIRST");int r=probe_vulkan("mock");assert(!strcmp(events,argv[3]));assert((r==0)==!strcmp(failure,"ok"));assert(skips==ui);assert(!memories&&!images&&!buffers&&!pools&&!fences);unsigned clearFail=!strncmp(failure,"clear-",6);assert(cbAllocs==(clearFail?1u:2u)&&submits==cbAllocs&&fenceIds==submits);assert(barrierCount==(clearFail?1u:2u));printf("PASS actual UI-batch flag=%s failure=%s events=%s\n",argv[1],failure,events);}
'''
builder=runpy.run_path(str(ROOT/'scripts/build-a04br3-powervr-probe.py'))
with tempfile.TemporaryDirectory(prefix='pvr-ui-batch-') as t:
 p=Path(t);src=p/'t.c';exe=p/'test';src.write_text(h)
 cc=[os.environ.get('CC','cc'),'-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined','-fno-pie','-no-pie','-g']
 subprocess.run(cc+['-I'+str(ROOT/'.cache/mesa-pvr/include'),str(src),'-o',str(exe)],check=True)
 cases=[('1','ok','CBK'),('1','clear-submit','C'),('1','clear-fence','C'),('1','copy-fence','CB')]+[('1','mip'+str(i),'CBK') for i in range(4)]
 cases += [(flag,'ok','LRCBKOH') for flag in ('absent','0','2','01','true')]
 for flag,fail,events in cases:subprocess.run([str(exe),flag,fail,events],check=True,timeout=30)
 ctor=builder['ui_batch_probe_source']()
 src.write_text(ctor+'\n#include <assert.h>\n#include <string.h>\nint main(void){assert(!strcmp(getenv("PVR_PROBE_UI_BATCH"),"1"));assert(!getenv("PVR_PROBE_UI_FILL")&&!getenv("PVR_DARWIN_TQ_CLIP")&&!getenv("PVR_DARWIN_SIPF1_DUMP"));}\n')
 subprocess.run(cc+[str(src),'-o',str(exe)],check=True)
 env=dict(os.environ)
 for k in ('PVR_PROBE_UI_BATCH','PVR_PROBE_UI_FILL','PVR_DARWIN_TQ_CLIP','PVR_DARWIN_SIPF1_DUMP'):env.pop(k,None)
 subprocess.run([str(exe)],env=env,check=True)
 src.write_text(ctor.replace('setenv(', 'fail_setenv(').replace('#include <stdio.h>','#include <stdio.h>\nstatic int fail_setenv(const char*a,const char*b,int c){(void)a;(void)b;(void)c;return -1;}')+'\nint main(void){return 99;}\n')
 subprocess.run(cc+[str(src),'-o',str(exe)],check=True);r=subprocess.run([str(exe)],capture_output=True,text=True);assert r.returncode==1 and 'cannot enable UI-batch' in r.stderr
r=subprocess.run([sys.executable,str(ROOT/'scripts/build-a04br3-powervr-probe.py'),'--check','--ui-batch'],capture_output=True,text=True);assert r.returncode==2 and '--ui-batch requires --vulkan' in r.stderr
print('PASS 13 actual-main ASan/UBSan cases: four mip shapes/offsets/barriers, every mip readback, separate CB/fences, lifetimes, exact gate + constructor/CLI; no GPU execution')
