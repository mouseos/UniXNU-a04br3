#!/usr/bin/env python3
"""Compile actual Mesa guard functions with bounded allocator/ioctl mocks.
Requires the pinned patched .cache/mesa-pvr tree and cc with ASan/UBSan.
No SDK, GPU, external checkout or caller host-memory access is used.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / '.cache/mesa-pvr/src/imagination/vulkan'


def definition(text, signature):
    start = text.index(signature)
    brace = text.index('{', start)
    end, depth = brace + 1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


bo = (SRC / 'winsys/powervr/pvr_drm_bo.c').read_text()
device = (SRC / 'pvr_device.c').read_text()
ws = (SRC / 'winsys/pvr_winsys.h').read_text()
boh = (SRC / 'winsys/powervr/pvr_drm_bo.h').read_text()
h = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int VkResult;
typedef uint64_t VkDeviceSize;
typedef struct { uint64_t addr; } pvr_dev_addr_t;
#define PVR_DEV_ADDR_OFFSET(a,b) ((pvr_dev_addr_t){(a).addr+(b)})
#define ALIGN_POT(a,b) (((a)+(b)-1)&~((uint64_t)(b)-1))
#define align64 ALIGN_POT
#define VK_SUCCESS 0
#define VK_ERROR_UNKNOWN (-3)
#define VK_ERROR_MEMORY_MAP_FAILED (-1)
#define VK_ERROR_OUT_OF_DEVICE_MEMORY (-2)
#define DRM_IOCTL_PVR_VM_MAP 1
#define DRM_IOCTL_PVR_VM_UNMAP 2
#define PVR_WINSYS_BO_TYPE_GPU 0
#define PVR_BUFFER_MEMORY_PADDING_SIZE 4
#define PVR_BVNC_PACK(b,v,n,c) (((uint64_t)(b)<<48)|((uint64_t)(v)<<32)|((uint64_t)(n)<<16)|(c))
#define vk_error(d,e) (e)
#define to_pvr_drm_winsys(ws) ((struct pvr_drm_winsys *)(ws))
#define to_pvr_drm_winsys_bo(bo) ((struct pvr_drm_winsys_bo *)(bo))
#define to_pvr_drm_winsys_vma(vma) ((struct pvr_drm_winsys_vma *)(vma))
struct pvr_winsys;
struct pvr_winsys_heap {struct pvr_winsys *ws; uint64_t page_size;};
struct pvr_winsys_bo {struct pvr_winsys *ws; uint64_t size;};
'''
h += definition(ws, 'struct pvr_winsys_vma {') + ';\n'
h += definition(boh, 'struct pvr_drm_winsys_vma {') + ';\n'
h += r'''
struct pvr_drm_winsys_bo {struct pvr_winsys_bo base; unsigned handle, refs;};
struct ops {
 VkResult (*heap_alloc)(struct pvr_winsys_heap *,uint64_t,uint64_t,struct pvr_winsys_vma **);
 void (*heap_free)(struct pvr_winsys_vma *);
 VkResult (*vma_map)(struct pvr_winsys_vma *,struct pvr_winsys_bo *,uint64_t,uint64_t,pvr_dev_addr_t *);
 VkResult (*vma_map_guarded)(struct pvr_winsys_vma *,struct pvr_winsys_bo *,uint64_t,uint64_t,pvr_dev_addr_t *);
 void (*vma_unmap)(struct pvr_winsys_vma *);
};
struct pvr_winsys {struct ops *ops; uint64_t page_size; struct {bool buffer_guard;} features;int render_fd;};
struct pvr_drm_winsys {struct pvr_winsys base; unsigned vm_context;};
struct pvr_device {struct pvr_winsys *ws; struct {struct pvr_winsys_heap *general_heap;} heaps;};
struct pvr_device_memory {struct pvr_winsys_bo *bo;};
struct drm_pvr_ioctl_vm_map_args {uint64_t device_addr,flags,handle,offset,size,vm_context_handle;};
struct drm_pvr_ioctl_vm_unmap_args {uint64_t vm_context_handle,device_addr,size;};
static struct pvr_drm_winsys_vma area;
static struct pvr_drm_winsys_bo guard;
static unsigned maps,unmaps,allocs,frees,created,destroyed,fail,fixtures;
static uint64_t reserved;
static struct drm_pvr_ioctl_vm_map_args live[2],history[2];
static VkResult pvr_ioctl(int fd,unsigned op,const struct drm_pvr_ioctl_vm_map_args *a,VkResult err) {
 (void)fd;(void)err;assert(op==DRM_IOCTL_PVR_VM_MAP&&a->flags==0);
 ++maps;if(fail==maps)return VK_ERROR_MEMORY_MAP_FAILED;
 unsigned i=live[0].size?1:0;assert(!live[i].size);live[i]=*a;history[i]=*a;return VK_SUCCESS;
}
static void pvr_ioctlf(int fd,unsigned op,const struct drm_pvr_ioctl_vm_unmap_args *a,VkResult err,const char *msg) {
 (void)fd;(void)err;(void)msg;assert(op==DRM_IOCTL_PVR_VM_UNMAP);
 unsigned i=0;while(i<2 && (!live[i].size||live[i].device_addr!=a->device_addr))++i;
 assert(i<2&&live[i].size==a->size);live[i].size=0;++unmaps;
}
static void pvr_drm_buffer_acquire(struct pvr_drm_winsys_bo *b){assert(b->refs);++b->refs;}
static void pvr_drm_buffer_release(struct pvr_drm_winsys_bo *b){assert(b->refs);if(!--b->refs){assert(b==&guard);++destroyed;}}
static VkResult pvr_drm_winsys_buffer_create(struct pvr_winsys *w,uint64_t size,uint64_t alignment,int type,unsigned flags,struct pvr_winsys_bo **out) {
 assert(size==4096&&alignment==4096&&type==PVR_WINSYS_BO_TYPE_GPU&&!flags);++created;
 if(fail==3)return VK_ERROR_OUT_OF_DEVICE_MEMORY;
 assert(!guard.refs);guard=(struct pvr_drm_winsys_bo){.base={w,size},.handle=2,.refs=1};*out=&guard.base;return VK_SUCCESS;
}
static void pvr_drm_winsys_buffer_destroy(struct pvr_winsys_bo *b){pvr_drm_buffer_release(to_pvr_drm_winsys_bo(b));}
static VkResult heap_alloc(struct pvr_winsys_heap *h,uint64_t size,uint64_t alignment,struct pvr_winsys_vma **out){
 assert(alignment==4096);++allocs;if(fail==4)return VK_ERROR_OUT_OF_DEVICE_MEMORY;
 memset(&area,0,sizeof area);reserved=ALIGN_POT(size,alignment);area.base=(struct pvr_winsys_vma){.heap=h,.size=reserved,.dev_addr={0x100000}};*out=&area.base;return VK_SUCCESS;
}
static void heap_free(struct pvr_winsys_vma *a){assert(a==&area.base&&!a->bo&&!area.guard.bo&&!live[0].size&&!live[1].size);++frees;}
'''
for signature in ('VkResult pvr_drm_winsys_vma_map(',
                  'static void pvr_drm_vma_unmap_one(',
                  'VkResult pvr_drm_winsys_vma_map_guarded(',
                  'void pvr_drm_winsys_vma_unmap('):
    h += definition(bo, signature) + '\n'
h += definition((SRC / 'pvr_buffer.h').read_text(), 'static inline bool pvr_buffer_needs_guard(') + '\n'
for signature in ('static VkResult pvr_bind_memory_internal(',
                  'VkResult pvr_bind_memory(', 'void pvr_unbind_memory('):
    h += definition(device, signature) + '\n'
# Compile the real requirements function, including its capability condition.
h += r'''
struct pvr_buffer {struct {uint64_t size;} vk;uint64_t alignment;struct pvr_winsys_vma *vma;pvr_dev_addr_t dev_addr;};
typedef struct pvr_device *VkDevice;
typedef struct {struct pvr_device_memory *memory;struct pvr_buffer *buffer;uint64_t memoryOffset;} VkBindBufferMemoryInfo;
typedef struct {struct pvr_buffer *buffer;} VkBufferMemoryRequirementsInfo2;
typedef struct {struct {uint64_t size,alignment,memoryTypeBits;} memoryRequirements;void *pNext;} VkMemoryRequirements2;
typedef struct {bool requiresDedicatedAllocation,prefersDedicatedAllocation;} VkMemoryDedicatedRequirements;
#define VK_FROM_HANDLE(t,n,h) struct t *n=(h)
#define VK_STRUCTURE_TYPE_MEMORY_DEDICATED_REQUIREMENTS 0
#define vk_foreach_struct(s,e,p) for(int s=0,once=0;once;once=0) for(void *e=(p);once;once=0)
#define vk_debug_ignored_stype(s) ((void)(s))
'''
req = definition(device, 'void pvr_GetBufferMemoryRequirements2(')
# Only the unrelated physical-device memory-type count is supplied by a mock.
req = req.replace('device->pdevice->memory.memoryTypeCount', '1')
h += req + '\n'
h += definition(device, 'VkResult pvr_BindBufferMemory2(') + '\n'
h += definition((SRC / 'winsys/powervr/pvr_drm.c').read_text(), 'static bool pvr_drm_buffer_guard_enabled(') + '\n'
h += r'''
static void reset(void){assert(!live[0].size&&!live[1].size&&!guard.refs);maps=unmaps=allocs=frees=created=destroyed=fail=0;memset(history,0,sizeof history);}
int main(void) {
 struct ops ops={heap_alloc,heap_free,pvr_drm_winsys_vma_map,pvr_drm_winsys_vma_map_guarded,pvr_drm_winsys_vma_unmap};
 struct pvr_drm_winsys w={.base={.ops=&ops,.page_size=4096,.features={true}},.vm_context=3};
 struct pvr_winsys_heap heap={&w.base,4096};struct pvr_device d={&w.base,{&heap}};
 const uint64_t sizes[]={1,16,4092,4093,4095,4096,4097,16384,4096000};
 for(unsigned enabled=0;enabled<2;++enabled)for(unsigned i=0;i<sizeof sizes/sizeof *sizes;++i)for(unsigned off=0;off<=8192;off+=4096){
  reset();w.base.features.buffer_guard=enabled;
  uint64_t size=sizes[i],body=ALIGN_POT(size,4096);bool needs=size%4096==0||size%4096>4092;
  struct pvr_buffer buffer={.vk={size},.alignment=4096};VkBufferMemoryRequirementsInfo2 info={&buffer};VkMemoryRequirements2 req={0};
  pvr_GetBufferMemoryRequirements2(&d,&info,&req);
  assert(req.memoryRequirements.size==body+(!enabled&&needs?4096:0));
  struct pvr_drm_winsys_bo payload={.base={&w.base,off+req.memoryRequirements.size},.handle=1,.refs=1};
  struct pvr_device_memory mem={&payload.base};struct pvr_winsys_vma *vma=NULL;pvr_dev_addr_t addr={0};
  VkBindBufferMemoryInfo bind={&mem,&buffer,off};
  assert(pvr_BindBufferMemory2(&d,1,&bind)==VK_SUCCESS);vma=buffer.vma;addr=buffer.dev_addr;
  assert(addr.addr==0x100000&&reserved==body+(enabled&&needs?4096:0));
  assert(payload.base.size==off+req.memoryRequirements.size&&payload.refs==2);
  assert(history[0].offset==off&&history[0].size==body&&history[0].handle==1);
  assert(maps==(enabled&&needs?2u:1u));
  if(enabled&&needs){assert(guard.refs==1&&history[1].handle==2&&history[1].offset==0&&history[1].size==4096&&history[1].device_addr==addr.addr+body);}
  pvr_unbind_memory(&d,vma);assert(payload.refs==1&&unmaps==maps&&frees==1&&destroyed==created);++fixtures;
 }
 w.base.features.buffer_guard=true;
 for(unsigned error=1;error<=4;++error){
  reset();fail=error;struct pvr_drm_winsys_bo payload={.base={&w.base,4096},.handle=1,.refs=1};struct pvr_device_memory mem={&payload.base};struct pvr_winsys_vma *vma=NULL;pvr_dev_addr_t addr={99};
  assert(pvr_bind_memory_internal(&d,&mem,0,4096,4096,&vma,&addr,true)!=VK_SUCCESS);
  assert(!vma&&addr.addr==99&&payload.refs==1&&!guard.refs&&!live[0].size&&!live[1].size);
  assert(frees==(error==4?0u:1u));assert(unmaps==(error==2?1u:0u));assert(destroyed==(error<=2?1u:0u));++fixtures;
 }
 // Generic/image bindings continue to use the unguarded entry point.
 reset();struct pvr_drm_winsys_bo payload={.base={&w.base,4096},.handle=1,.refs=1};struct pvr_device_memory mem={&payload.base};struct pvr_winsys_vma *vma; pvr_dev_addr_t addr;
 assert(pvr_bind_memory(&d,&mem,0,4096,4096,&vma,&addr)==0&&maps==1&&!created&&reserved==4096);pvr_unbind_memory(&d,vma);++fixtures;
 // Overflow and short reservations must fail before guard allocation or map.
 reset();area.base=(struct pvr_winsys_vma){.heap=&heap,.size=4096,.dev_addr={0x100000}};
 assert(pvr_drm_winsys_vma_map_guarded(&area.base,&payload.base,0,4096,NULL)!=0&&!maps&&!created);++fixtures;
 assert(pvr_drm_winsys_vma_map_guarded(&area.base,&payload.base,0,UINT64_MAX,NULL)!=0&&!maps&&!created);++fixtures;
 area.base.size=8192;assert(pvr_drm_winsys_vma_map_guarded(&area.base,&payload.base,1,4096,NULL)!=0&&!maps&&!created);++fixtures;
 // Addition, rounding and guard-reservation overflow must precede VA allocation.
 for(unsigned guarded=0;guarded<2;++guarded){
  reset();payload.base.size=UINT64_MAX;
  assert(pvr_bind_memory_internal(&d,&mem,64,UINT64_MAX,64,&vma,&addr,guarded)!=0&&!allocs&&!maps&&!created);++fixtures;
 }
 reset();assert(pvr_bind_memory_internal(&d,&mem,0,UINT64_MAX,4096,&vma,&addr,true)!=0&&!allocs);++fixtures;
 reset();assert(pvr_bind_memory_internal(&d,&mem,0,UINT64_MAX-4095,4096,&vma,&addr,true)!=0&&!allocs);++fixtures;
 const char *envs[]={NULL,"","0","01","true","1"};
 for(unsigned i=0;i<6;++i)for(unsigned host=0;host<2;++host)for(unsigned board=0;board<2;++board)for(unsigned page=4096;page<=8192;page+=4096){
  if(envs[i])assert(!setenv("PVR_DARWIN_BUFFER_GUARD",envs[i],1));else assert(!unsetenv("PVR_DARWIN_BUFFER_GUARD"));
  bool expected=false;
#ifdef PVR_WINSYS_DARWIN
  expected=i==5&&host&&board&&page==4096;
#endif
  assert(pvr_drm_buffer_guard_enabled(board?PVR_BVNC_PACK(22,40,54,30):PVR_BVNC_PACK(22,67,54,30),host,page)==expected);++fixtures;
 }
 printf("PASS %u actual-function fixtures: requirements, sizes/offsets, private maps, rollback, exact unmaps, refs, generic/default-off policy\n",fixtures);
}
'''
create_h = r'''
#include <assert.h>
#include <stdint.h>
#include <limits.h>
#include <stdlib.h>
typedef int VkResult;
#define VK_SUCCESS 0
#define VK_ERROR_OUT_OF_DEVICE_MEMORY -1
#define VK_ERROR_OUT_OF_HOST_MEMORY -2
#define VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO 12
#define VK_FROM_HANDLE(t,n,h) struct t *n=(h)
#define vk_error(d,e) (e)
struct pvr_winsys {uint32_t page_size;};
struct physical {struct pvr_winsys *ws;};
struct pvr_device {struct physical *pdevice;int vk;};
struct pvr_buffer {struct {unsigned usage;} vk;uint32_t alignment;};
typedef struct pvr_device *VkDevice;
typedef struct pvr_buffer *VkBuffer;
typedef void VkAllocationCallbacks;
typedef struct {int sType;uint64_t size;} VkBufferCreateInfo;
static struct pvr_buffer buffer;
static void *vk_buffer_create(int *d,const VkBufferCreateInfo *i,const void *a,size_t size){(void)d;(void)i;(void)a;assert(size==sizeof buffer);buffer.vk.usage=1;return &buffer;}
#define pvr_buffer_to_handle(b) (b)
''' + definition(device, 'VkResult pvr_CreateBuffer(') + r'''
int main(void){struct pvr_winsys ws={4096};struct physical p={&ws};struct pvr_device d={&p,0};VkBufferCreateInfo i={12,4096};VkBuffer b=NULL;assert(!pvr_CreateBuffer(&d,&i,NULL,&b)&&b->alignment==4096);i.size=ULONG_MAX-4096;assert(pvr_CreateBuffer(&d,&i,NULL,&b)==VK_ERROR_OUT_OF_DEVICE_MEMORY);}
'''
with tempfile.TemporaryDirectory(prefix='pvr-buffer-guard-') as tmp:
    path = Path(tmp)
    (path / 'fixture.c').write_text(h)
    for darwin in (False, True):
        binary = path / ('darwin' if darwin else 'generic')
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-D_POSIX_C_SOURCE=200809L',
                        '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-g',
                        *(['-DPVR_WINSYS_DARWIN'] if darwin else []),
                        str(path / 'fixture.c'), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)

    (path / 'create.c').write_text(create_h)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-g', str(path / 'create.c'),
                    '-o', str(path / 'create')], check=True)
    subprocess.run([str(path / 'create')], check=True)
    print('PASS actual CreateBuffer: page-size alignment and upper size bound')
