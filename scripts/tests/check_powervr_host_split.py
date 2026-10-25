#!/usr/bin/env python3
"""Run actual hostImportProbe with Vulkan mocks; no GPU execution."""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
# Reuse signature-correct Vulkan mocks without running the unrelated suite.
base = (ROOT / 'scripts/tests/check_powervr_tq_first.py').read_text()
ns = {'__file__': str(ROOT / 'scripts/tests/check_powervr_tq_first.py')}
exec(base.split('# Map every actually invoked Vulkan operation')[0], ns)
h = ns['h']
def replace(name, body):
    global h
    match = re.search(r'static [^\n]*?\b' + name + r'\(', h)
    assert match, name
    start = match.start(); b = h.index('{', start); end = b + 1; depth = 1
    while depth:
        depth += (h[end] == '{') - (h[end] == '}'); end += 1
    h = h[:start] + body + h[end:]
replace('hostImportProbe', '')
h = h.replace('0x11,0x5a,0xa5,0xee', '0x39,0x72,0xb4,0xe1')
h = h.replace('static char events[128];', 'static void *hostPointer; static unsigned allocated,submitted,destroyed; static char events[128];')
replace('vkAllocateMemory_', '''static VkResult vkAllocateMemory_(VkDevice d,const VkMemoryAllocateInfo *i,const VkAllocationCallbacks *a,VkDeviceMemory *v){(void)d;(void)a;++memories;if(i->pNext){const VkImportMemoryHostPointerInfoEXT *p=i->pNext;assert(p->handleType==VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT);assert(i->allocationSize==16384);hostPointer=p->pHostPointer;*v=HANDLE(VkDeviceMemory);}else *v=(VkDeviceMemory)(uintptr_t)2;return VK_SUCCESS;}''')
replace('vkMapMemory_', '''static VkResult vkMapMemory_(VkDevice d,VkDeviceMemory m,VkDeviceSize o,VkDeviceSize s,VkMemoryMapFlags f,void **p){(void)d;(void)m;(void)o;(void)s;(void)f;*p=hostPointer;return VK_SUCCESS;}''')
replace('vkAllocateCommandBuffers_', '''static VkResult vkAllocateCommandBuffers_(VkDevice d,const VkCommandBufferAllocateInfo *i,VkCommandBuffer *p){(void)d;assert(i->commandBufferCount==1);*p=(VkCommandBuffer)(uintptr_t)++allocated;return VK_SUCCESS;}''')
replace('vkCreateFence_', '''static VkResult vkCreateFence_(VkDevice d,const VkFenceCreateInfo *i,const VkAllocationCallbacks *a,VkFence *f){(void)d;(void)i;(void)a;assert(!fences);++fences;*f=(VkFence)(uintptr_t)(destroyed+1);return VK_SUCCESS;}''')
replace('vkDestroyFence_', '''static void vkDestroyFence_(VkDevice d,VkFence f,const VkAllocationCallbacks *a){(void)d;(void)f;(void)a;assert(fences==1);--fences;++destroyed;}''')
replace('vkQueueSubmit_', '''static VkResult vkQueueSubmit_(VkQueue q,uint32_t n,const VkSubmitInfo *i,VkFence f){(void)q;(void)f;assert(n==1&&i->commandBufferCount==1);assert((uintptr_t)*i->pCommandBuffers==++submitted);assert(stage==submitted);if(stage==2)assert(waits==1&&destroyed==1);if(stage==1&&!strcmp(failure,"clear-submit"))return VK_ERROR_DEVICE_LOST;return VK_SUCCESS;}''')
h = h.replace('pixels[i]=kClear[i%4]', '((unsigned char*)hostPointer)[i]=kClear[i%4]').replace('pixels[0]^=1', '((unsigned char*)hostPointer)[0]^=1')
h += '''
static VkResult vkGetMemoryHostPointerPropertiesEXT_(VkDevice d,VkExternalMemoryHandleTypeFlagBits t,const void *p,VkMemoryHostPointerPropertiesEXT *v){(void)d;(void)t;assert(p);v->memoryTypeBits=1;return VK_SUCCESS;}
static void vkGetPhysicalDeviceExternalBufferProperties_(VkPhysicalDevice p,const VkPhysicalDeviceExternalBufferInfo *i,VkExternalBufferProperties *v){(void)p;(void)i;v->externalMemoryProperties.externalMemoryFeatures=VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT;v->externalMemoryProperties.compatibleHandleTypes=VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT;}
static VkResult vkFlushMappedMemoryRanges_(VkDevice d,uint32_t n,const VkMappedMemoryRange *r){(void)d;assert(n==1&&r->offset==0&&r->size==VK_WHOLE_SIZE);return VK_SUCCESS;}
'''
names = re.findall(r'static (?:VkResult|void|PFN_vkVoidFunction) (vk\w+)_\(', h)
h += 'static PFN_vkVoidFunction lookup(const char *name){\n'
for name in names:
    h += f'if(!strcmp(name,"{name}"))return (PFN_vkVoidFunction){name}_;\n'
h += 'return (PFN_vkVoidFunction)dummy;}\n' + ns['macros']
h += '#define DECLARE(name) static PFN_##name p_##name;\nVK_FUNCTIONS(DECLARE)\nVK_DEVICE_FUNCTIONS(DECLARE)\n#undef DECLARE\n'
h += ns['function']('static int hostImportProbe(')
h += '''
int main(int argc,char **argv){assert(argc==2);failure=argv[1];
#define INIT(name) p_##name=(PFN_##name)lookup(#name);
VK_FUNCTIONS(INIT) VK_DEVICE_FUNCTIONS(INIT)
#undef INIT
VkPhysicalDeviceMemoryProperties m={0};int r=hostImportProbe(HANDLE(VkPhysicalDevice),HANDLE(VkDevice),HANDLE(VkQueue),0,&m,4096);
int clearFail=!strncmp(failure,"clear-",6);assert(!strcmp(events,clearFail?"C":"CB"));assert(allocated==(clearFail?1u:2u));assert(submitted==allocated);assert(destroyed==allocated);assert((r!=0)==!strcmp(failure,"ok"));assert(!memories&&!images&&!buffers&&!pools&&!fences);printf("PASS actual hostImportProbe %s\\n",failure);}
'''
with tempfile.TemporaryDirectory(prefix='pvr-host-split-') as tmp:
    p=Path(tmp); (p/'fixture.c').write_text(h)
    subprocess.run([os.environ.get('CC','cc'),'-std=gnu11','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(ROOT/'.cache/mesa-pvr/include'),str(p/'fixture.c'),'-o',str(p/'fixture')],check=True)
    for case in ('ok','clear-submit','clear-fence','copy-fence','readback'):
        subprocess.run([str(p/'fixture'),case],check=True,timeout=30)
