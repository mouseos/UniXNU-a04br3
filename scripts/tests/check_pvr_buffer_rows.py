#!/usr/bin/env python3
"""Compile the production optional coalescer; compare its emitted rectangle to byte rows."""
from pathlib import Path
import os
import subprocess
import tempfile
root=Path(__file__).resolve().parents[2]
patch=(root/'patches/mesa/pvr-darwin-winsys.patch').read_text()
added='\n'.join(l[1:] for l in patch.splitlines() if l.startswith('+') and not l.startswith('+++'))
a=added.index('static bool\npvr_darwin_copy_buffer_rows(')
b=added.index('\n#endif',a)
helper=added[a:b]
pre=r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <algorithm>
#define PVR_MAX_TRANSFER_SIZE_IN_TEXELS 2048U
#define VK_FORMAT_R32G32B32A32_UINT 1
#define VK_SUCCESS 0
using VkResult=int;
using VkDeviceSize=uint64_t;
struct pvr_dev_addr_t { uint64_t addr; };
struct VkBufferCopy2 { uint64_t srcOffset,dstOffset,size; };
struct VkCopyBufferInfo2 { unsigned regionCount; const VkBufferCopy2 *pRegions; };
struct pvr_buffer { struct {uint64_t size;} vk; pvr_dev_addr_t dev_addr; };
struct Rect {unsigned width,height;};
struct Surface {uint64_t base; unsigned width,height,stride;};
struct Mapping {Rect src_rect,dst_rect;};
struct pvr_transfer_cmd {struct {Surface surface;Mapping mappings[1];unsigned mapping_count;} sources[1];Surface dst;Rect scissor;unsigned source_count;};
struct Pool {int alloc;};
struct pvr_cmd_buffer {struct {Pool *pool;int error;} vk;};
static pvr_transfer_cmd emitted;
static unsigned allocations,adds,frees;
static bool allocFail,addFail;
static pvr_transfer_cmd* pvr_transfer_cmd_alloc(pvr_cmd_buffer*c){++allocations;if(allocFail){c->vk.error=-1;return nullptr;}return new pvr_transfer_cmd{};}
static void pvr_setup_buffer_surface(Surface*s,Rect*r,pvr_dev_addr_t a,uint64_t o,unsigned f,unsigned image,unsigned w,unsigned h,unsigned stride){assert(f==1&&image==1);*s={a.addr+o,w,h,stride};*r={w,h};}
static VkResult pvr_arch_cmd_buffer_add_transfer_cmd(pvr_cmd_buffer*,pvr_transfer_cmd*c){++adds;if(addFail)return -2;emitted=*c;delete c;return 0;}
static void vk_free(int*,pvr_transfer_cmd*c){++frees;delete c;}
static void vk_command_buffer_set_error(decltype(pvr_cmd_buffer::vk)*c,int e){c->error=e;}
'''
post=r'''
int main(){
 Pool pool{};pvr_cmd_buffer c{{&pool,0}};
 pvr_buffer src{{6000000},{0x10000000}},dst{{6000000},{0x20000000}};
 std::vector<VkBufferCopy2> rows(1280);
 auto layout=[&](unsigned count,uint64_t size,uint64_t sp,uint64_t dp){rows.resize(count);for(unsigned i=0;i<count;i++)rows[i]={16+i*sp,32+i*dp,size};};
 auto run=[&]{VkCopyBufferInfo2 info{unsigned(rows.size()),rows.data()};return pvr_darwin_copy_buffer_rows(&c,&src,&dst,&info);};
 layout(1280,3200,3328,3200);
 unsetenv("PVR_DARWIN_BUFFER_ROWS");assert(!run()&&!adds&&!allocations);
 for(const char*v:{"0","", "01","true"}){setenv("PVR_DARWIN_BUFFER_ROWS",v,1);assert(!run());}
 setenv("PVR_DARWIN_BUFFER_ROWS","1",1);
 for(unsigned count:{2,17,1280,2048})for(unsigned width:{16,64,3200}){
  const unsigned sp=width+128,dp=width+64;layout(count,width,sp,dp);
  src.vk.size=rows.back().srcOffset+width;dst.vk.size=rows.back().dstOffset+width;
  unsigned before=adds;assert(run()&&adds==before+1);
  assert(emitted.source_count==1&&emitted.sources[0].mapping_count==1);
  assert(emitted.sources[0].surface.stride==sp/16&&emitted.dst.stride==dp/16);
  std::vector<unsigned char> input(src.vk.size),expected(dst.vk.size,0xa5),actual=expected;
  for(size_t i=0;i<input.size();i++)input[i]=(i*71+i/257)&255;
  for(auto row:rows)memcpy(expected.data()+row.dstOffset,input.data()+row.srcOffset,row.size);
  const auto&s=emitted.sources[0].surface;const auto&d=emitted.dst;
  for(unsigned y=0;y<d.height;y++)memcpy(actual.data()+d.base-dst.dev_addr.addr+y*d.stride*16,
     input.data()+s.base-src.dev_addr.addr+y*s.stride*16,d.width*16);
  assert(actual==expected); // includes all destination padding and sentinels
 }
 layout(1280,3200,3328,3200);src.vk.size=dst.vk.size=6000000;
 auto no=[&]{auto before=allocations;assert(!run()&&allocations==before);};
 rows[2].size--;no();rows[2].size++;
 rows[2].srcOffset++;no();rows[2].srcOffset--;
 src.vk.size=1;no();src.vk.size=6000000;
 dst.vk.size=1;no();dst.vk.size=6000000;
 src.dev_addr.addr++;no();src.dev_addr.addr--;
 dst.dev_addr.addr=src.dev_addr.addr;no();dst.dev_addr.addr=0x20000000;
 src.dev_addr.addr=UINT64_MAX-15;no();src.dev_addr.addr=0x10000000;
 layout(2049,16,16,16);no();layout(1,16,16,16);no();
 layout(2,15,16,16);no();layout(2,32,16,16);no();
 layout(2,16,32784,16);no();layout(2,16,16,17);no();
 layout(2,16,16,16);auto before=adds;allocFail=true;assert(run()&&c.vk.error==-1&&adds==before);allocFail=false;
 addFail=true;assert(run()&&c.vk.error==-2&&frees==1);addFail=false;
 VkCopyBufferInfo2 same{unsigned(rows.size()),rows.data()};assert(!pvr_darwin_copy_buffer_rows(&c,&src,&src,&same));
}
'''
with tempfile.TemporaryDirectory() as directory:
    tmp=Path(directory);(tmp/'test.cpp').write_text(pre+helper+post)
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(tmp/'test.cpp'),'-o',str(tmp/'test')],check=True)
    subprocess.run([str(tmp/'test')],check=True)
print('PASS production coalescer: default-off, exact padded bytes, 1280->1 command, bounds/alignment/alias/overflow fallback, allocation/add failure')
