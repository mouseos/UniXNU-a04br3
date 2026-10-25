#!/usr/bin/env python3
"""実probe_vulkanをVulkan/loaderモックで実行し、試験順序と失敗時中断を検証。GPU試験ではない。"""
from pathlib import Path
import os
import runpy
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
src = (ROOT / 'package/powervr/user/powervrprobed_vulkan.c').read_text()


def function(signature):
    start = src.index(signature)
    b = src.index('{', start)
    end, depth = b + 1, 1
    while depth:
        depth += (src[end] == '{') - (src[end] == '}')
        end += 1
    return src[start:end]


macros = src[src.index('#define VK_FUNCTIONS('):src.index('#define DECLARE(')]
h = r'''
#define VK_NO_PROTOTYPES 1
#include <vulkan/vulkan.h>
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
enum{kWidth=64,kHeight=64};
static const uint8_t kClear[4]={0x11,0x5a,0xa5,0xee};
static _Alignas(16) uint8_t pixels[64*64*4];
static char events[128];static unsigned en,stage,memories,images,buffers,pools,fences,waits;
static const char *failure;
static void event(char c){assert(en<sizeof(events)-1);events[en++]=c;events[en]=0;}
static void probe_line(const char *fmt,...){(void)fmt;}
static int vkCheck(const char *name,int ok,const char *detail){(void)detail;if(!strcmp(name,"clear readback"))event('K');return ok;}
static int vkStep(const char *name,VkResult vr,const char *detail){(void)name;(void)detail;return vr==VK_SUCCESS;}
static uint32_t memoryType(const VkPhysicalDeviceMemoryProperties *m,uint32_t bits,VkMemoryPropertyFlags f){(void)m;(void)f;assert(bits);return 0;}
static int hostImportSupported(VkPhysicalDevice p){(void)p;return 1;}
static int hostImportProbe(VkPhysicalDevice p,VkDevice d,VkQueue q,uint32_t f,const VkPhysicalDeviceMemoryProperties *m,VkDeviceSize a){(void)p;(void)d;(void)q;(void)f;(void)m;assert(a==4096);event('H');return strcmp(failure,"host")!=0;}
static int linearTriangleProbe(VkDevice d,VkQueue q,VkCommandPool p,const VkPhysicalDeviceMemoryProperties *m,VkFormatFeatureFlags f,int clear){(void)d;(void)q;(void)p;(void)m;(void)f;event(clear?'L':'R');return !(clear&&!strcmp(failure,"linear"));}
static int triangleProbe(VkDevice d,VkQueue q,VkCommandPool p,VkImage i,VkBuffer b,VkDeviceMemory m,void *map,int c){(void)d;(void)q;(void)p;(void)i;(void)b;(void)m;(void)map;(void)c;event('O');return strcmp(failure,"triangle")!=0;}
static void imageBarrierLevels(VkCommandBuffer c,VkImage i,VkImageLayout a,VkImageLayout b,VkAccessFlags x,VkAccessFlags y,VkPipelineStageFlags s,VkPipelineStageFlags t,uint32_t levels){(void)c;(void)i;(void)a;(void)b;(void)x;(void)y;(void)s;(void)t;assert(levels==1);}
static void imageBarrier(VkCommandBuffer c,VkImage i,VkImageLayout a,VkImageLayout b,VkAccessFlags x,VkAccessFlags y,VkPipelineStageFlags s,VkPipelineStageFlags t){(void)c;(void)i;(void)a;(void)b;(void)x;(void)y;(void)s;(void)t;}
#define HANDLE(t) ((t)(uintptr_t)1)
static VkResult vkCreateInstance_(const VkInstanceCreateInfo *i,const VkAllocationCallbacks *a,VkInstance *v){(void)i;(void)a;*v=HANDLE(VkInstance);return VK_SUCCESS;}
static void vkDestroyInstance_(VkInstance v,const VkAllocationCallbacks *a){(void)v;(void)a;}
static VkResult vkEnumeratePhysicalDevices_(VkInstance i,uint32_t *n,VkPhysicalDevice *v){(void)i;*n=1;*v=HANDLE(VkPhysicalDevice);return VK_SUCCESS;}
static void vkGetPhysicalDeviceProperties_(VkPhysicalDevice d,VkPhysicalDeviceProperties *p){(void)d;memset(p,0,sizeof(*p));p->apiVersion=VK_API_VERSION_1_2;}
static void vkGetPhysicalDeviceProperties2_(VkPhysicalDevice d,VkPhysicalDeviceProperties2 *p){(void)d;if(p->pNext){VkBaseOutStructure *b=p->pNext;if(b->sType==VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT)((VkPhysicalDeviceExternalMemoryHostPropertiesEXT*)b)->minImportedHostPointerAlignment=4096;}}
static void vkGetPhysicalDeviceQueueFamilyProperties_(VkPhysicalDevice d,uint32_t *n,VkQueueFamilyProperties *p){(void)d;*n=1;memset(p,0,sizeof(*p));p->queueCount=1;p->queueFlags=VK_QUEUE_GRAPHICS_BIT;}
static void vkGetPhysicalDeviceFormatProperties_(VkPhysicalDevice d,VkFormat f,VkFormatProperties *p){(void)d;(void)f;memset(p,0,sizeof(*p));p->linearTilingFeatures=p->optimalTilingFeatures=VK_FORMAT_FEATURE_TRANSFER_SRC_BIT|VK_FORMAT_FEATURE_TRANSFER_DST_BIT|VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT;}
static void vkGetPhysicalDeviceMemoryProperties_(VkPhysicalDevice d,VkPhysicalDeviceMemoryProperties *p){(void)d;memset(p,0,sizeof(*p));}
static VkResult vkCreateDevice_(VkPhysicalDevice d,const VkDeviceCreateInfo *i,const VkAllocationCallbacks *a,VkDevice *v){(void)d;(void)i;(void)a;*v=HANDLE(VkDevice);return VK_SUCCESS;}
static void vkDestroyDevice_(VkDevice d,const VkAllocationCallbacks *a){(void)d;(void)a;}
static void vkGetDeviceQueue_(VkDevice d,uint32_t f,uint32_t i,VkQueue *q){(void)d;(void)f;(void)i;*q=HANDLE(VkQueue);}
static VkResult vkCreateImage_(VkDevice d,const VkImageCreateInfo *i,const VkAllocationCallbacks *a,VkImage *v){(void)d;(void)i;(void)a;++images;*v=HANDLE(VkImage);return VK_SUCCESS;}
static void vkDestroyImage_(VkDevice d,VkImage v,const VkAllocationCallbacks *a){(void)d;(void)v;(void)a;assert(images);--images;}
static void vkGetImageMemoryRequirements_(VkDevice d,VkImage i,VkMemoryRequirements *r){(void)d;(void)i;*r=(VkMemoryRequirements){.size=sizeof(pixels),.alignment=4096,.memoryTypeBits=1};}
static VkResult vkCreateBuffer_(VkDevice d,const VkBufferCreateInfo *i,const VkAllocationCallbacks *a,VkBuffer *v){(void)d;(void)i;(void)a;++buffers;*v=HANDLE(VkBuffer);return VK_SUCCESS;}
static void vkDestroyBuffer_(VkDevice d,VkBuffer v,const VkAllocationCallbacks *a){(void)d;(void)v;(void)a;assert(buffers);--buffers;}
static void vkGetBufferMemoryRequirements_(VkDevice d,VkBuffer i,VkMemoryRequirements *r){(void)d;(void)i;*r=(VkMemoryRequirements){.size=sizeof(pixels),.alignment=4096,.memoryTypeBits=1};}
static VkResult vkAllocateMemory_(VkDevice d,const VkMemoryAllocateInfo *i,const VkAllocationCallbacks *a,VkDeviceMemory *v){(void)d;(void)i;(void)a;++memories;*v=HANDLE(VkDeviceMemory);return VK_SUCCESS;}
static void vkFreeMemory_(VkDevice d,VkDeviceMemory m,const VkAllocationCallbacks *a){(void)d;(void)m;(void)a;assert(memories);--memories;}
static VkResult vkBindImageMemory_(VkDevice d,VkImage i,VkDeviceMemory m,VkDeviceSize o){(void)d;(void)i;(void)m;(void)o;return VK_SUCCESS;}
static VkResult vkBindBufferMemory_(VkDevice d,VkBuffer i,VkDeviceMemory m,VkDeviceSize o){(void)d;(void)i;(void)m;(void)o;return VK_SUCCESS;}
static VkResult vkMapMemory_(VkDevice d,VkDeviceMemory m,VkDeviceSize o,VkDeviceSize s,VkMemoryMapFlags f,void **p){(void)d;(void)m;(void)o;(void)s;(void)f;*p=pixels;return VK_SUCCESS;}
static void vkUnmapMemory_(VkDevice d,VkDeviceMemory m){(void)d;(void)m;}
static VkResult vkCreateCommandPool_(VkDevice d,const VkCommandPoolCreateInfo *i,const VkAllocationCallbacks *a,VkCommandPool *p){(void)d;(void)i;(void)a;++pools;*p=HANDLE(VkCommandPool);return VK_SUCCESS;}
static void vkDestroyCommandPool_(VkDevice d,VkCommandPool p,const VkAllocationCallbacks *a){(void)d;(void)p;(void)a;assert(pools);--pools;}
static VkResult vkAllocateCommandBuffers_(VkDevice d,const VkCommandBufferAllocateInfo *i,VkCommandBuffer *p){(void)d;(void)i;*p=HANDLE(VkCommandBuffer);return VK_SUCCESS;}
static VkResult vkBeginCommandBuffer_(VkCommandBuffer p,const VkCommandBufferBeginInfo *i){(void)p;(void)i;return VK_SUCCESS;}
static VkResult vkEndCommandBuffer_(VkCommandBuffer p){(void)p;return VK_SUCCESS;}
static void vkCmdClearColorImage_(VkCommandBuffer c,VkImage i,VkImageLayout l,const VkClearColorValue *v,uint32_t n,const VkImageSubresourceRange *r){(void)c;(void)i;(void)l;(void)v;(void)r;assert(n==1);stage=1;event('C');}
static void vkCmdCopyImageToBuffer_(VkCommandBuffer c,VkImage i,VkImageLayout l,VkBuffer b,uint32_t n,const VkBufferImageCopy *r){(void)c;(void)i;(void)l;(void)b;assert(n==1&&r->imageExtent.width==64);stage=2;event('B');}
static void vkCmdPipelineBarrier_(VkCommandBuffer c,VkPipelineStageFlags s,VkPipelineStageFlags d,VkDependencyFlags f,uint32_t n,const VkMemoryBarrier *m,uint32_t bn,const VkBufferMemoryBarrier *b,uint32_t in,const VkImageMemoryBarrier *i){(void)c;(void)s;(void)d;(void)f;(void)n;(void)m;(void)bn;(void)b;(void)in;(void)i;}
static VkResult vkCreateFence_(VkDevice d,const VkFenceCreateInfo *i,const VkAllocationCallbacks *a,VkFence *f){(void)d;(void)i;(void)a;++fences;*f=HANDLE(VkFence);return VK_SUCCESS;}
static void vkDestroyFence_(VkDevice d,VkFence f,const VkAllocationCallbacks *a){(void)d;(void)f;(void)a;assert(fences);--fences;}
static VkResult vkQueueSubmit_(VkQueue q,uint32_t n,const VkSubmitInfo *i,VkFence f){(void)q;(void)i;(void)f;assert(n==1);if(stage==1&&!strcmp(failure,"clear-submit"))return VK_ERROR_DEVICE_LOST;return VK_SUCCESS;}
static VkResult vkWaitForFences_(VkDevice d,uint32_t n,const VkFence *f,VkBool32 all,uint64_t t){(void)d;(void)f;(void)all;assert(n==1&&t==10000000000ull);++waits;
 if((stage==1&&!strcmp(failure,"clear-fence"))||(stage==2&&!strcmp(failure,"copy-fence")))return VK_ERROR_DEVICE_LOST;
 if(stage==2){for(unsigned i=0;i<sizeof(pixels);++i)pixels[i]=kClear[i%4];if(!strcmp(failure,"readback"))pixels[0]^=1;}return VK_SUCCESS;}
static VkResult vkInvalidateMappedMemoryRanges_(VkDevice d,uint32_t n,const VkMappedMemoryRange *r){(void)d;(void)n;(void)r;return VK_SUCCESS;}
static VkResult vkDeviceWaitIdle_(VkDevice d){(void)d;return VK_SUCCESS;}
static void dummy(void){}
static PFN_vkVoidFunction lookup(const char *name);
static PFN_vkVoidFunction vkGetDeviceProcAddr_(VkDevice d,const char *n){(void)d;return lookup(n);}
static PFN_vkVoidFunction gipa_(VkInstance d,const char *n){(void)d;return lookup(n);}
static void *fakeOpen(const char *p,int f){(void)p;(void)f;return (void*)1;}
static void *fakeSymbol(void *d,const char *n){(void)d;(void)n;return (void*)gipa_;}
static char *fakeError(void){return NULL;}
#define dlopen fakeOpen
#define dlsym fakeSymbol
#define dlerror fakeError
'''
# Map every actually invoked Vulkan operation to a signature-correct mock. Other
# function pointers only satisfy the probe's loader check and are never called.
import re
names = re.findall(r'static (?:VkResult|void|PFN_vkVoidFunction) (vk\w+)_\(', h)
h += 'static PFN_vkVoidFunction lookup(const char *name){\n'
for name in names:
    h += f' if(!strcmp(name,"{name}"))return (PFN_vkVoidFunction){name}_;\n'
h += ' return (PFN_vkVoidFunction)dummy;}\n' + macros
h += '#define DECLARE(name) static PFN_##name p_##name;\nVK_FUNCTIONS(DECLARE)\nVK_DEVICE_FUNCTIONS(DECLARE)\n#undef DECLARE\n'
h += function('int probe_vulkan(')
h += r'''
int main(int argc,char **argv){assert(argc==4);failure=argv[2];
 if(!strcmp(argv[1],"absent"))assert(!unsetenv("PVR_PROBE_TQ_FIRST"));else assert(!setenv("PVR_PROBE_TQ_FIRST",argv[1],1));
 int r=probe_vulkan("mock");assert(!strcmp(events,argv[3]));
 assert((r==0)==!strcmp(failure,"ok"));assert(!memories&&!images&&!buffers&&!pools&&!fences);
 printf("PASS actual probe order flag=%s failure=%s events=%s result=%d\n",argv[1],failure,events,r);}
'''

builder = runpy.run_path(str(ROOT / 'scripts/build-a04br3-powervr-probe.py'))
ctor = builder['tq_first_probe_source']()
with tempfile.TemporaryDirectory(prefix='pvr-tq-first-') as tmp:
    tmp = Path(tmp)
    source, binary = tmp / 'probe.c', tmp / 'probe'
    source.write_text(h)
    cc = [os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
          '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-g']
    if sys.platform == 'linux':
        cc += ['-fno-pie', '-no-pie']
    subprocess.run(cc + ['-I'+str(ROOT / '.cache/mesa-pvr/include'), str(source), '-o', str(binary)], check=True)
    cases = [('absent','ok','LRCB KOH'.replace(' ','')),('0','ok','LRCBKOH'),('01','ok','LRCBKOH'),
             ('true','ok','LRCBKOH'),('1','ok','CBKLROH'),('1','clear-submit','C'),
             ('1','clear-fence','C'),('1','copy-fence','CB'),('1','readback','CBK'),
             ('1','linear','CBKLRO'),('1','triangle','CBKLRO'),('1','host','CBKLROH'),
             ('absent','readback','LRCBKO')]
    for flag, failure, events in cases:
        subprocess.run([str(binary), flag, failure, events], check=True, timeout=30)
    source.write_text(ctor + '\n#include <assert.h>\n#include <string.h>\nint main(void){assert(!strcmp(getenv("PVR_PROBE_TQ_FIRST"),"1"));assert(getenv("PVR_DARWIN_TQ_CLIP")==NULL);}\n')
    subprocess.run(cc + [str(source), '-o', str(binary)], check=True)
    env = dict(os.environ)
    env.pop('PVR_DARWIN_TQ_CLIP', None)
    subprocess.run([str(binary)], env=env, check=True)
    source.write_text(ctor.replace('setenv(', 'fail_setenv(').replace('#include <stdio.h>', '#include <stdio.h>\nstatic int fail_setenv(const char*a,const char*b,int c){(void)a;(void)b;(void)c;return -1;}')+'\nint main(void){return 99;}\n')
    subprocess.run(cc + [str(source), '-o', str(binary)], check=True)
    r = subprocess.run([str(binary)], capture_output=True, text=True)
    assert r.returncode == 1 and 'cannot enable TQ-first' in r.stderr
    r = subprocess.run([sys.executable, str(ROOT/'scripts/build-a04br3-powervr-probe.py'), '--check', '--tq-first'], capture_output=True, text=True)
    assert r.returncode == 2 and '--tq-first requires --vulkan' in r.stderr
print('PASS 13 actual-probe ASan/UBSan order/failure/cleanup cases + owner-only constructor, failed-init and CLI gate; no GPU test')
