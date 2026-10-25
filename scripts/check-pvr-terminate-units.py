#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Test the actual terminal PPP function and generated Mesa packers after a Mesa build.

Device allocation/upload/debug are stubbed; this is not a GPU execution test.
Pass --ppp-header for builds outside the usual output directories.
"""
from pathlib import Path
import argparse, re, hashlib, subprocess, tempfile
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--mesa',type=Path,default=root/'.cache/mesa-pvr')
p.add_argument('--ppp-header',type=Path)
args=p.parse_args()
mesa=args.mesa
if args.ppp_header is None:
 candidates=[root/'.cache/build-pvr-hostcheck/src/imagination/csbgen/rogue/ppp.h',
             root/'build/mesa-pvr-ios/src/imagination/csbgen/rogue/ppp.h',
             root/'build/mesa-pvr-host/src/imagination/csbgen/rogue/ppp.h']
 args.ppp_header=next((x for x in candidates if x.is_file()),None)
if args.ppp_header is None or not args.ppp_header.is_file():
 p.error('Build Mesa first, or pass --ppp-header PATH to its generated rogue/ppp.h')
work=tempfile.TemporaryDirectory(prefix='pvr-terminate-test-')
out=Path(work.name)
def definition(s,sig):
 start=s.index(sig); b=s.index('{',start); depth=1;e=b+1
 while depth:
  depth+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[start:e]
source=(mesa/'src/imagination/vulkan/pvr_arch_framebuffer.c').read_text()
fn=definition(source,'VkResult PVR_PER_ARCH(ppp_state_create)(')
(out/'actual-function.c').write_text(fn+'\n')
ppp=args.ppp_header.read_text()
csb=(mesa/'src/imagination/vulkan/pvr_csb.h').read_text()
helpers=(mesa/'src/imagination/csbgen/pvr_packet_helpers.h').read_text()
h=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#define __pvr_validate_value(x)
#define NDEBUG_UNUSED
#define STATIC_ASSERT(x) _Static_assert(x,#x)
#define PVR_DW_TO_BYTES(x) ((x)*4)
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define PVR_PER_ARCH(x) x
#define PVR_HAS_FEATURE(d,f) ((d)->f)
#define PVR_GET_FEATURE_VALUE(d,f,fallback) ((d)->has_version ? (d)->f : (fallback))
#define VK_SUCCESS 0
typedef int VkResult;
struct pvr_device_info {struct {unsigned b,v,n,c;} ident; bool tile_size_16x16,has_version; unsigned simple_parameter_format_version;};
struct pvr_physical_device {struct pvr_device_info dev_info;};
struct pvr_device {struct pvr_physical_device *pdevice;struct {void *general_heap;} heaps;};
struct pvr_suballoc_bo {unsigned unused;};
static uint32_t captured[3];
static unsigned calls,fixtures;
static int upload_error;
static struct pvr_suballoc_bo fake_bo;
static uint32_t pvr_get_slc_cache_line_size(const struct pvr_device_info *d) {(void)d;return 64;}
#ifdef PVR_WINSYS_DARWIN
static void pvr_darwin_debug(const char *fmt,...) {(void)fmt;}
#endif
static VkResult pvr_gpu_upload(struct pvr_device *d,void *heap,const void *data,size_t size,uint32_t alignment,struct pvr_suballoc_bo **out)
{ assert(d && heap==NULL && size==12 && alignment==64);++calls;memcpy(captured,data,12);if(upload_error)return upload_error;*out=&fake_bo;return VK_SUCCESS; }
'''
h+='static inline uint64_t\n'+definition(helpers,'__pvr_uint(uint64_t v,')+'\n'
for name in ('TA_STATE_HEADER','TA_STATE_TERMINATE0','TA_STATE_TERMINATE1'):
 h+=definition(ppp,'struct ROGUE_'+name+' {')+';\n'
 h+='static inline void\n'+definition(ppp,'ROGUE_'+name+'_pack(')+'\n'
 h+=f'#define ROGUE_{name}_header\n#define ROGUE_{name}_length 1\n'
for name in ('RIGHT','BOTTOM'):
 m=re.search(r'^#define ROGUE_TA_STATE_TERMINATE0_CLIP_'+name+r'_BLOCK_SIZE_IN_PIXELS\s+\d+',ppp,re.M)
 h+=m.group(0)+'\n'
for name in ('length','header','pack'):
 h+=re.search(r'^#define pvr_cmd_'+name+r'\(x\).*$',csb,re.M).group(0)+'\n'
start=csb.index('#define pvr_csb_pack(');end=csb.index('\n\n',start)
h+=csb[start:end]+'\n'+fn+'\n'
h+=r'''
static void check(struct pvr_device_info info,const char *env,unsigned w,unsigned ht,unsigned unit)
{
 if(env)assert(setenv("PVR_DARWIN_TERMINATE_TILE_UNITS",env,1)==0);else assert(unsetenv("PVR_DARWIN_TERMINATE_TILE_UNITS")==0);
 struct pvr_physical_device p={info};struct pvr_device d={.pdevice=&p};
 struct pvr_suballoc_bo *bo=NULL;size_t size=999;
 unsigned before=calls;assert(ppp_state_create(&d,w,ht,&bo,&size)==0);
 assert(calls==before+1 && bo==&fake_bo && size==3);
 unsigned right=(w-1)/unit,bottom=(ht-1)/unit;
 assert(captured[0]==0x02000000u && captured[2]==0);
 assert(captured[1]==((right<<18)|bottom));
 assert(((captured[1]>>18)&511)==right && (captured[1]&511)==bottom);
 assert(((captured[1]>>9)&511)==0);
 ++fixtures;
}
int main(void)
{
 struct pvr_device_info target={.ident={22,40,54,30},.tile_size_16x16=true,.has_version=true,.simple_parameter_format_version=1};
 const unsigned dims[]={16,32,63,64,65,128};
 const char *envs[]={NULL,"","0","1","01","true","1 ","2"};
 for(unsigned i=0;i<6;++i)for(unsigned j=0;j<6;++j)for(unsigned e=0;e<8;++e){
 unsigned unit=32;
#ifdef PVR_WINSYS_DARWIN
 if(envs[e] && strcmp(envs[e],"1")==0)unit=16;
#endif
 check(target,envs[e],dims[i],dims[j],unit);
 }
 for(unsigned variant=0;variant<8;++variant){
 struct pvr_device_info other=target;
 switch(variant){case 0:other.ident.b=23;break;case 1:other.ident.v=67;break;case 2:other.ident.n=55;break;case 3:other.ident.c=38;break;case 4:other.tile_size_16x16=false;break;case 5:other.simple_parameter_format_version=2;break;case 6:other.has_version=false;break;case 7:other.simple_parameter_format_version=0;break;}
 for(unsigned i=0;i<6;++i)for(unsigned j=0;j<6;++j)check(other,"1",dims[i],dims[j],32);
 }
 for(unsigned i=0;i<6;++i){
 unsigned v=dims[i];unsigned unit=32;
#ifdef PVR_WINSYS_DARWIN
 unit=16;
#endif
 check(target,"1",v,v,unit);printf("%ux%u enabled: %08x/%08x/%08x right=%u bottom=%u\n",v,v,captured[0],captured[1],captured[2],(captured[1]>>18)&511,captured[1]&511);
 check(target,NULL,v,v,32);printf("%ux%u default: %08x/%08x/%08x right=%u bottom=%u\n",v,v,captured[0],captured[1],captured[2],(captured[1]>>18)&511,captured[1]&511);
 }
 struct pvr_physical_device p={target};struct pvr_device d={.pdevice=&p};
 struct pvr_suballoc_bo *bo=NULL;size_t size=999;upload_error=-7;
 assert(ppp_state_create(&d,64,64,&bo,&size)==-7 && bo==NULL && size==999);++fixtures;
 printf("PASS %u actual-function fixtures; real generated structs/packers and actual pvr_csb_pack; upload/device/debug stubs\n",fixtures);
}
'''
(out/'harness.c').write_text(h)
print('function_sha256',hashlib.sha256(fn.encode()).hexdigest(),flush=True)
for mode in ('darwin','non-darwin'):
 cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined']
 if mode=='darwin':cmd+=['-DPVR_WINSYS_DARWIN']
 cmd += [str(out/'harness.c'),'-o',str(out/mode)]
 subprocess.run(cmd,check=True)
 print(mode,flush=True)
 subprocess.run([str(out/mode)],check=True)
