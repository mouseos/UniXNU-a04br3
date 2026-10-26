#!/usr/bin/env python3
"""Compile shipped probe; explicit CPU ICD execution, never implicit host GPU use."""
import argparse
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[2]
def run(args, **kw):
    subprocess.run([str(x) for x in args], check=True, **kw)
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--lavapipe', type=Path, help='explicit CPU ICD shared library')
    parser.add_argument('--vulkan-include', type=Path, default=ROOT/'.cache/mesa-pvr/include',
                        help='pinned Vulkan headers (also supports isolated worktrees)')
    args=parser.parse_args()
    source=ROOT/'package/powervr/user/powervrprobed_vulkan.c'
    include=args.vulkan_include.resolve()
    if not (include/'vulkan/vulkan.h').is_file():
        parser.error('pinned Vulkan headers missing; pass --vulkan-include')
    run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsyntax-only','-I',include,source])
    builder=ROOT/'scripts/build-a04br3-powervr-probe.py'
    with tempfile.TemporaryDirectory() as temporary:
        tmp=Path(temporary)
        constructor=tmp/'constructor.c'
        constructor.write_text(runpy.run_path(str(builder))['buffer_rows_probe_source']()+'''\n#include <string.h>
int main(void){return !getenv("PVR_PROBE_BUFFER_ROWS") || strcmp(getenv("PVR_PROBE_BUFFER_ROWS"),"1") ||
 !getenv("PVR_DARWIN_BUFFER_ROWS") || strcmp(getenv("PVR_DARWIN_BUFFER_ROWS"),"1");}
''')
        run(['cc','-Wall','-Wextra','-Werror',constructor,'-o',tmp/'constructor'])
        run([tmp/'constructor'])
        for flags in (['--buffer-rows'],['--vulkan','--buffer-rows','--ui-batch'],['--vulkan','--buffer-rows','--serial-transfer']):
            result=subprocess.run(['python3',str(builder),'--check',*flags],capture_output=True)
            assert result.returncode!=0,flags
        validator=tmp/'validator.c'
        validator.write_text(r'''#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdio.h>
void probe_line(const char *f,...){(void)f;}
void probe_pass(void){}
void probe_fail(void){}
#include "'''+str(source)+r'''"
int main(void){
 assert(brElapsedUs(2,7)==5);assert(brElapsedUs(7,2)==0);
 assert(brElapsedUs(UINT64_MAX,7)==0);assert(brElapsedUs(2,UINT64_MAX)==0);
 assert(brClockUs()!=UINT64_MAX);
 size_t sn=brPrefix+brSrcPitch*brRows+8192, dn=brPrefix+brDstPitch*brRows+8192;
 uint8_t *s=malloc(sn),*d=malloc(dn);assert(s&&d);
 for(size_t n=0;n<sn;n++)s[n]=brPattern(n);
 for(size_t n=0;n<dn;n++){size_t src=0;d[n]=brPayload(n,&src)?brPattern(src):0xa5;}
 assert(brValidate(s,sn,d,dn));
 const size_t bad[]={0,brPrefix,brPrefix+brWidth,brPrefix+(brRows-1)*brDstPitch+brWidth,dn-1};
 for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);i++){
   d[bad[i]]^=1;assert(!brValidate(s,sn,d,dn));d[bad[i]]^=1;
 }
 s[sn-1]^=1;assert(!brValidate(s,sn,d,dn));s[sn-1]^=1;
 s[brPrefix]^=1;assert(!brValidate(s,sn,d,dn));free(s);free(d);return 0;
}
''')
        run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
             '-fno-omit-frame-pointer','-I',include,validator,'-ldl','-o',tmp/'validator'])
        run([tmp/'validator'])
        # Run the actual helper with a CPU ICD but replace only completion verdict.
        # A real wait first makes process exit safe; synthetic TIMEOUT must not free resources.
        lifecycle=tmp/'lifecycle.c'
        lifecycle.write_text(r'''#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
void probe_line(const char *f,...){(void)f;}
void probe_pass(void){}
void probe_fail(void){}
#include "'''+str(source)+r'''"
static PFN_vkWaitForFences realWait;
static unsigned destroyed;
static VkResult timeoutWait(VkDevice d,uint32_t n,const VkFence *f,VkBool32 all,uint64_t timeout){
 assert(realWait(d,n,f,all,timeout)==VK_SUCCESS);return VK_TIMEOUT;
}
static void noDestroyFence(VkDevice d,VkFence f,const VkAllocationCallbacks *a){(void)d;(void)f;(void)a;destroyed++;}
static void noDestroyPool(VkDevice d,VkCommandPool p,const VkAllocationCallbacks *a){(void)d;(void)p;(void)a;destroyed++;}
static void noDestroyBuffer(VkDevice d,VkBuffer b,const VkAllocationCallbacks *a){(void)d;(void)b;(void)a;destroyed++;}
static void noFree(VkDevice d,VkDeviceMemory m,const VkAllocationCallbacks *a){(void)d;(void)m;(void)a;destroyed++;}
static void noUnmap(VkDevice d,VkDeviceMemory m){(void)d;(void)m;destroyed++;}
int main(int argc,char **argv){
 assert(argc==2);void *lib=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);assert(lib);
 PFN_vkGetInstanceProcAddr gipa=(PFN_vkGetInstanceProcAddr)dlsym(lib,"vkGetInstanceProcAddr");
 if(!gipa){gipa=(PFN_vkGetInstanceProcAddr)dlsym(lib,"vk_icdGetInstanceProcAddr");}
 assert(gipa);
 PFN_vkCreateInstance create=(PFN_vkCreateInstance)gipa(NULL,"vkCreateInstance");
 VkApplicationInfo app={.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,.apiVersion=VK_API_VERSION_1_1};
 VkInstanceCreateInfo ii={.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,.pApplicationInfo=&app};
 VkInstance instance;assert(create(&ii,NULL,&instance)==VK_SUCCESS);
#define LOAD(name) p_##name=(PFN_##name)gipa(instance,#name);
 VK_FUNCTIONS(LOAD)
#undef LOAD
 VkPhysicalDevice physical;uint32_t count=1;assert(p_vkEnumeratePhysicalDevices(instance,&count,&physical)==VK_SUCCESS);
 VkPhysicalDeviceMemoryProperties memory;p_vkGetPhysicalDeviceMemoryProperties(physical,&memory);
 float priority=1;VkDeviceQueueCreateInfo qi={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,.queueFamilyIndex=0,.queueCount=1,.pQueuePriorities=&priority};
 VkDeviceCreateInfo di={.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,.queueCreateInfoCount=1,.pQueueCreateInfos=&qi};
 VkDevice device;assert(p_vkCreateDevice(physical,&di,NULL,&device)==VK_SUCCESS);
#define LOAD(name) p_##name=(PFN_##name)p_vkGetDeviceProcAddr(device,#name);
 VK_DEVICE_FUNCTIONS(LOAD)
#undef LOAD
 VkQueue queue;p_vkGetDeviceQueue(device,0,0,&queue);
 realWait=p_vkWaitForFences;p_vkWaitForFences=timeoutWait;
 p_vkDestroyFence=noDestroyFence;p_vkDestroyCommandPool=noDestroyPool;p_vkDestroyBuffer=noDestroyBuffer;
 p_vkFreeMemory=noFree;p_vkUnmapMemory=noUnmap;
 assert(bufferRowsProbe(device,queue,0,&memory)==-1);assert(destroyed==0);
 /* Deliberately retain submitted resources until process exit, matching the owner daemon. */
 return 0;
}
''')
        run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-I',include,lifecycle,'-ldl','-o',tmp/'lifecycle'])

        harness=tmp/'harness.c'
        harness.write_text('''#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
static unsigned failures;
void probe_line(const char *fmt,...){va_list ap;va_start(ap,fmt);vprintf(fmt,ap);puts("");va_end(ap);}
void probe_pass(void){}
void probe_fail(void){failures++;}
#include "'''+str(source)+'''"
int main(int argc,char **argv){
 if(argc!=2)return 2;
 setenv("PVR_PROBE_BUFFER_ROWS","1",1);
 int result=probe_vulkan(argv[1]);return result!=0 || failures!=0;
}
''')
        run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-I',include,harness,'-ldl','-o',tmp/'probe'])
        if args.lavapipe:
            env=dict(os.environ, VK_DRIVER_FILES='/usr/share/vulkan/icd.d/lvp_icd.json')
            executed=subprocess.run([str(tmp/'probe'),str(args.lavapipe.resolve())],env=env,
                                    timeout=90,capture_output=True,text=True)
            print(executed.stdout,end='')
            if executed.returncode:
                print(executed.stderr,end='')
                raise SystemExit(executed.returncode)
            for marker in ('vk buffer-rows single-shot host-us: record=', 'clock=1',
                           'vk buffer-rows single-shot cache-us: flush=', ' invalidate='):
                if marker not in executed.stdout:
                    raise SystemExit('missing timing marker: '+marker)
            run([tmp/'lifecycle',args.lavapipe.resolve()],env=env,timeout=90)
    print('PASS buffer-rows source, opt-in constructor, exclusive CLI'+(', CPU lavapipe real Vulkan copy' if args.lavapipe else ''))
if __name__=='__main__':main()
