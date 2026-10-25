#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""正本パッチを一時ツリーへ適用し、実ISP/PDS/size関数をASan/UBSanで検証する。

--mesa は固定版Mesaのgitオブジェクトを持つcheckout。作業ツリーや既存生成物は
読み込まない。初回は build-pvr-darwin.py の入力取得で固定版Mesaを用意する。
GPUの実行・vendor binaryの解析は行わない。
"""
import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--mesa', type=Path, default=ROOT / '.cache/mesa-pvr')
a = p.parse_args()
pin = json.loads((ROOT / 'scripts/data/pvr-mesa-inputs.json').read_text())['mesa']['commit']

def definition(s, sig):
    start = s.index(sig)
    b = s.index('{', start)
    depth, end = 1, b + 1
    while depth:
        depth += (s[end] == '{') - (s[end] == '}')
        end += 1
    return s[start:end]

with tempfile.TemporaryDirectory(prefix='pvr-sipf1-') as tmp:
    out = Path(tmp)
    archive = subprocess.check_output(['git', '-C', str(a.mesa), 'archive', pin,
        'src/imagination/csbgen', 'src/imagination/vulkan/pvr_arch_job_transfer.c',
        'src/imagination/vulkan/pvr_csb.h'])
    with tarfile.open(fileobj=io.BytesIO(archive)) as t:
        t.extractall(out, filter='data')
    subprocess.run(['git', 'apply', '--unsafe-paths',
        '--include=src/imagination/vulkan/pvr_arch_job_transfer.c',
        '--include=src/imagination/vulkan/pvr_darwin_sipf1.h',
        str(ROOT / 'patches/mesa/pvr-darwin-winsys.patch')], cwd=out, check=True)
    img = out / 'src/imagination'
    for kind in ('ppp', 'ipf', 'cr'):
        with (out / (kind + '.h')).open('w') as f:
            subprocess.run([sys.executable, str(img / 'csbgen/gen_pack_header.py'),
                str(img / ('csbgen/rogue/' + kind + '.xml'))], stdout=f, check=True)
    src = (img / 'vulkan/pvr_arch_job_transfer.c').read_text()
    csb = (img / 'vulkan/pvr_csb.h').read_text()
    functions = '\n'.join(definition(src, s) for s in (
        'static bool pvr_darwin_sipf1_dump_begin(',
        'static void pvr_darwin_sipf1_dump_bytes(',
        'static bool pvr_darwin_tq_sipf1(',
        'static void pvr_isp_prim_block_pds_state(',
        'static void pvr_isp_prim_block_isp_state(',
        'static uint32_t\npvr_isp_primitive_block_size(',
        'static inline uint32_t\npvr_transfer_prim_blocks_per_alloc('))
    ctrl = definition(src, 'static VkResult pvr_isp_ctrl_stream(')
    link_branch = definition(ctrl, 'if (free_ctrl_stream_words < min_free_ctrl_stream_words)')
    end_record = definition(ctrl, 'if (sipf1) {\n      uint64_t last;')
    min_free = re.search(r'const uint32_t min_free_ctrl_stream_words =.*?;', ctrl, re.S)[0]
    final_assert = re.search(r'if \(PVR_HAS_FEATURE\(dev_info, ipf_creq_pf\)\)\s*assert\([^;]+;', ctrl)[0]
    h = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
static inline void pvr_darwin_sipf1_log(const char *text) {(void)text;}
#include <stdlib.h>
#include <string.h>
#define STATIC_ASSERT(x) _Static_assert(x,#x)
#define UNUSED __attribute__((unused))
#define ALIGN_POT(x,a) (((x)+(a)-1)&~((a)-1))
#define MIN2(x,y) ((x)<(y)?(x):(y))
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define PVR_DW_TO_BYTES(x) ((x)*4)
#define PVR_TRANSFER_NUM_LAYERS 1
#define PVR_MEMLAYOUT_3DTWIDDLED 3
#define PVR_HAS_FEATURE(d,f) ((d)->f)
#define PVR_GET_FEATURE_VALUE(d,f,z) ((d)->f)
#define PVR_HAS_ENHANCEMENT(d,n) ((d)->ern42307)
typedef struct {uint64_t addr;} pvr_dev_addr_t;
#define PVR_DEV_ADDR(x) ((pvr_dev_addr_t){(x)})
#define __pvr_address_type pvr_dev_addr_t
#define __pvr_get_address(x) ((x).addr)
#define __pvr_make_address(x) PVR_DEV_ADDR(x)
#include "ppp.h"
#include "ipf.h"
#include "cr.h"
#include "vulkan/pvr_darwin_sipf1.h"
struct pvr_device_info {struct {unsigned b,v,n,c;} ident;bool tile_size_16x16,simple_internal_parameter_format,simple_internal_parameter_format_v2,ipf_creq_pf,ern42307;unsigned simple_parameter_format_version;};
struct pvr_transfer_ctx {void *device;};
struct pvr_transfer_3d_state {uint32_t pds_shader_task_offset,uni_tex_code_offset,uniform_data_size,tex_state_data_size,coeff_data_size,usc_coeff_regs,pds_temps,pds_coeff_task_offset,tex_state_data_offset,common_ptr;};
struct pvr_transfer_cmd_source {struct {unsigned mem_layout;} surface;};
'''
    for name in ('length', 'header', 'pack'):
        h += re.search(r'^#define pvr_cmd_' + name + r'\(x\).*$', csb, re.M)[0] + '\n'
    start = csb.index('#define pvr_csb_pack(')
    h += csb[start:csb.index('\n\n', start)] + '\n' + functions
    h += r'''
#define VK_ERROR_FORMAT_NOT_SUPPORTED -11
static int vk_error(void *device,int error) {(void)device;return error;}
struct pvr_suballoc_bo {void *map;};
static void *pvr_bo_suballoc_get_map_addr(struct pvr_suballoc_bo *bo) {return bo->map;}
'''
    h += definition(src, 'static inline uint8_t *pvr_isp_ctrl_stream_sipf_write_aligned(')
    h += r'''
/* Actual link/cursor/termination source blocks; shader generation is outside
 * this fixture. The oracle independently walks every encoded region. */
static int region_fixture(const struct pvr_device_info *dev_info,unsigned blocks,unsigned forced_regions) {
 const bool sipf1=true;
 const bool dump=false; unsigned dump_links=0;
 const unsigned capacity=pvr_transfer_prim_blocks_per_alloc(dev_info);
 unsigned num_region_arrays=forced_regions?forced_regions:DIV_ROUND_UP(blocks,capacity);
 const unsigned reserved=num_region_arrays;
 const unsigned bytes=reserved*128;
 const unsigned total_stream_size=bytes;
 uint32_t *buffer=malloc(bytes+128);assert(buffer);
 for(unsigned i=0;i<(bytes+128)/4;i++)buffer[i]=0xaabbccdd;
 struct pvr_suballoc_bo bo={buffer},*pvr_cs_bo=&bo;
 struct pvr_transfer_ctx context={0},*ctx=&context;
 pvr_dev_addr_t stream_base_vaddr=PVR_DEV_ADDR(0x1000);
 uint32_t *cs_ptr=buffer;
 unsigned sipf1_region_index=0,free_ctrl_stream_words=32;
 bool was_linked=false;
'''
    rgn_start = ctrl.index('if (sipf1 && PVR_HAS_FEATURE(dev_info, ipf_creq_pf))')
    rgn_end = ctrl.index('\n\n   return VK_SUCCESS;', rgn_start)
    rgn_block = ctrl[rgn_start:rgn_end]
    h += '\nstatic uint32_t rgn_fixture(const struct pvr_device_info *dev_info,bool sipf1,unsigned num_prim_blks,unsigned sipf1_region_index,bool was_linked) {\nstruct {uint32_t isp_rgn;} storage={0},*regs=&storage;\n'
    h += rgn_block + '\nreturn regs->isp_rgn; }\n'
    # region_fixture's declaration was already emitted; move this definition before it.
    mark=h.index('/* Actual link/cursor/termination source blocks;')
    split=h.index('static uint32_t rgn_fixture(',mark)
    h=h[:mark]+h[split:]+h[mark:split]
    # Keep the error return in the extracted block, but free the fixture's allocation.
    h += 'for(unsigned n=0;n<blocks;n++) {\n' + min_free + '\n'
    h += link_branch.replace('return vk_error(ctx->device, VK_ERROR_FORMAT_NOT_SUPPORTED);',
        '{ free(buffer); return vk_error(ctx->device, VK_ERROR_FORMAT_NOT_SUPPORTED); }')
    h += r'''
 uint64_t word;
 assert(pvr_sipf1_primitive(0x8000+n*88,1,false,&word));
 memcpy(cs_ptr,&word,8);cs_ptr+=2;free_ctrl_stream_words-=2;
 }
'''
    h += final_assert + '\n' + end_record
    h += r'''
 assert(num_region_arrays==reserved);
 uint32_t rgn=rgn_fixture(dev_info,true,blocks,sipf1_region_index,was_linked);
 assert(rgn==(dev_info->ipf_creq_pf?((blocks>15?31u:blocks)<<24):0));
 assert(sipf1_region_index==(blocks-1)/15);
 for(unsigned r=0;r<reserved;r++) {
  unsigned first=r*15,records=blocks-first;if(records>15)records=15;
  for(unsigned j=0;j<records;j++) {
   uint64_t got,expected;
   memcpy(&got,buffer+r*32+j*2,8);
   assert(pvr_sipf1_primitive(0x8000+(first+j)*88,1,false,&expected));
   if(first+j+1==blocks)expected|=2;
   assert(got==expected);
  }
  if(r+1<reserved){
   uint64_t got;memcpy(&got,buffer+r*32+30,8);
   uint64_t target=0x1000+(r+1)*128;
   assert(got==((target>>3)<<33|1));
  }
 }
 for(unsigned i=bytes/4;i<(bytes+128)/4;i++)assert(buffer[i]==0xaabbccdd);
 free(buffer);return 0;
}
'''
    h += r'''
int main(void) {
 struct pvr_device_info d={.ident={22,40,54,30},.tile_size_16x16=true,.simple_internal_parameter_format=true,.simple_parameter_format_version=1};
 uint32_t header;uint64_t word;
 assert(pvr_sipf1_vertex_header(4,0,&header)&&header==0x60);
 assert(pvr_sipf1_vertex_header(4,8,&header)&&header==0x2060);
 const uint64_t words[]={0x2100000308ULL,0x2100000f08ULL,0x2100003f28ULL,0x210000ff28ULL};
 for(unsigned n=1;n<=4;n++){assert(pvr_sipf1_primitive(0x80,n,false,&word)&&word==words[n-1]);}
 assert(pvr_sipf1_primitive(0x80,1,true,&word)&&word==0x2100000328ULL);
 assert(pvr_sipf1_link(0x80,&word)&&word==0x2000000001ULL);
 assert(!pvr_sipf1_primitive(7,1,false,&word));
 assert(!pvr_sipf1_link(1ULL<<34,&word));
 assert(pvr_sipf1_link((1ULL<<34)-8,&word));
 assert(!pvr_sipf1_primitive(0x80,2,true,&word));
 assert(!pvr_sipf1_vertex_header(0,0,&header));
 assert(!pvr_sipf1_vertex_header(4,512,&header));
 assert(unsetenv("PVR_DARWIN_SIPF1")==0);
 assert(unsetenv("PVR_DARWIN_TQ_CLIP")==0);
 assert(!pvr_darwin_tq_sipf1(&d));
 assert(setenv("PVR_DARWIN_TQ_CLIP","1",1)==0);
#ifdef PVR_WINSYS_DARWIN
 assert(pvr_darwin_tq_sipf1(&d));
#else
 assert(!pvr_darwin_tq_sipf1(&d));
#endif

 /* Naturally selected Clip can use v1 encoding without forced routing. */
 assert(unsetenv("PVR_DARWIN_TQ_CLIP")==0);
 assert(setenv("PVR_DARWIN_SIPF1","1",1)==0);
 {
  struct pvr_device_info policy=d;policy.ern42307=true;
  struct pvr_transfer_3d_state state={0};uint32_t words[16]={0};uint32_t *cur=words;
  pvr_isp_prim_block_pds_state(&policy,NULL,&state,&cur);
#ifdef PVR_WINSYS_DARWIN
  assert(words[6]==0x4000&&pvr_transfer_prim_blocks_per_alloc(&policy)==15);
  cur=words;pvr_isp_prim_block_isp_state(&policy,0,0,4,false,&cur);
  assert(cur==words+4&&words[0]==0x60&&words[2]==0x2e0000);
#else
  assert(words[6]==0&&pvr_transfer_prim_blocks_per_alloc(&policy)==8);
#endif
 }
 assert(unsetenv("PVR_DARWIN_SIPF1")==0);
 assert(setenv("PVR_DARWIN_TQ_CLIP","1",1)==0);
 /* Actual generated seven-DW packing: vendor bit14 parity and unchanged
  * ERN42307 policy outside the exact opt-in core. Shared-size bits survive. */
 for(unsigned ern=0;ern<2;ern++)for(unsigned core=0;core<2;core++)for(unsigned gate=0;gate<2;gate++){
  struct pvr_device_info policy=d;policy.ern42307=ern;policy.ident.c+=!core;
  assert(setenv("PVR_DARWIN_TQ_CLIP",gate?"1":"0",1)==0);
  struct pvr_transfer_3d_state state={.common_ptr=17};uint32_t words[8];
  for(unsigned i=0;i<8;i++)words[i]=0xbadface;
  uint32_t *cur=words;pvr_isp_prim_block_pds_state(&policy,NULL,&state,&cur);
  bool disable=!ern;
#ifdef PVR_WINSYS_DARWIN
  disable|=core&&gate;
#endif
  assert(words[6]==((2u<<23)|(disable?0x4000u:0u)));
  assert(words[0]==0&&words[1]==0&&words[2]==0&&words[3]==0&&words[4]==0&&words[5]==0);
  assert(cur==words+8&&words[7]==0xbadface);
 }
 assert(setenv("PVR_DARWIN_TQ_CLIP","1",1)==0);

 {
  struct pvr_device_info policy=d;policy.ern42307=true;
  struct pvr_transfer_3d_state state={.pds_shader_task_offset=0x1000,.pds_temps=4};
  uint32_t words[8]={0};uint32_t *cur=words;pvr_isp_prim_block_pds_state(&policy,NULL,&state,&cur);
  assert(words[0]==0x1000&&words[1]==0&&words[2]==1&&words[3]==0&&words[4]==0&&words[5]==0);
#ifdef PVR_WINSYS_DARWIN
  assert(words[6]==0x4000); /* Captured fill vector: only DW6 bit14 changes. */
#else
  assert(words[6]==0);
#endif
 }
 puts("actual PDS bit14: 8 ERN/core/opt-in cases passed; shared size and other DWs unchanged");
 unsigned fixtures=0;
#ifdef PVR_WINSYS_DARWIN
 for(unsigned kind=0;kind<3;kind++) {
  uint32_t storage[64];for(unsigned i=0;i<64;i++)storage[i]=0xbadface;
  uint32_t *cur=storage;unsigned varying=kind==0?0:kind==1?8:16;
  pvr_isp_prim_block_isp_state(&d,0,varying*4,4,false,&cur);
  assert(cur-storage==4&&storage[0]==(0x60|(varying<<10)));
  assert(storage[1]==0&&storage[2]==0x2e0000&&storage[3]==0);
  assert(storage[4]==0xbadface);
  /* One quad: two index dwords plus four 8-byte XYZ records. */
  cur+=2+8;assert(!((uintptr_t)cur&7));
  struct pvr_transfer_3d_state state={0};
  uint32_t *pds=cur;
  pvr_isp_prim_block_pds_state(&d,NULL,&state,&cur);
  assert(cur-pds==8&&!((uintptr_t)cur&7));
  assert(pds[7]==0xbadface); /* padding is reserved, not initialized by this function */
  cur+=varying;
  struct pvr_transfer_cmd_source source={.surface={kind==2?PVR_MEMLAYOUT_3DTWIDDLED:0}};
  assert(pvr_isp_primitive_block_size(&d,kind?&source:NULL,1)==(unsigned)(cur-storage)*4);
  assert(pvr_isp_primitive_block_size(&d,kind?&source:NULL,1)==(kind==0?88:kind==1?120:152));
  assert(*cur==0xbadface);fixtures++;
 }
 d.ident.c++;assert(!pvr_darwin_tq_sipf1(&d));d.ident.c--;
 assert(setenv("PVR_DARWIN_TQ_CLIP","01",1)==0);assert(!pvr_darwin_tq_sipf1(&d));
 assert(setenv("PVR_DARWIN_TQ_CLIP","1",1)==0);
 {
  uint32_t words[8];for(unsigned i=0;i<8;i++)words[i]=0xbadface;
  uint32_t *cur=words;
  struct pvr_transfer_3d_state state={.pds_shader_task_offset=0x120,.uni_tex_code_offset=0x230,
   .uniform_data_size=20,.tex_state_data_size=28,.coeff_data_size=36,.usc_coeff_regs=17,
   .pds_temps=5,.pds_coeff_task_offset=0x340,.tex_state_data_offset=0x450,.common_ptr=17};
  pvr_isp_prim_block_pds_state(&d,NULL,&state,&cur);
  /* Independent vendor seven-DW field formulas (units/rounding included). */
  const uint32_t expected[]={0x120,0x230,(5u<<23)|(7u<<16)|(9u<<10)|(2u<<4)|2u,0x340,0x450,0,(2u<<23)|0x4000};
  for(unsigned i=0;i<7;i++)assert(words[i]==expected[i]);
  assert(cur==words+8&&words[7]==0xbadface);
  puts("actual PDS seven-DW nonzero vendor-formula vector passed; reserved DW untouched");
 }
 unsigned capacity=pvr_transfer_prim_blocks_per_alloc(&d);
 assert(capacity==15); /* 15 records plus a link in a 128-byte region. */
 const unsigned blocks[]={1,8,9,15,16,29,30,31,60,120};
 for(unsigned pf=0;pf<2;pf++){
  d.ipf_creq_pf=pf;
  for(unsigned i=0;i<sizeof(blocks)/sizeof(blocks[0]);i++)assert(region_fixture(&d,blocks[i],0)==0);
  assert(region_fixture(&d,16,1)==VK_ERROR_FORMAT_NOT_SUPPORTED);
 }
 for(unsigned n=1;n<=40;n++){
  assert(rgn_fixture(&d,true,n,0,false)==((n<31?n:31u)<<24));
  assert(rgn_fixture(&d,true,n,1,true)==0x1f000000);
 }
 assert(rgn_fixture(&d,false,1,0,false)==0); /* legacy */
 d.simple_internal_parameter_format_v2=true;
 assert(rgn_fixture(&d,false,1,0,false)==0);
 assert(rgn_fixture(&d,false,16,0,true)==0x01000000); /* existing v2 link bit */
 d.simple_internal_parameter_format_v2=false;
 puts("actual RGN block: 80 count/saturation/link cases + legacy/v2 controls passed");
 puts("actual link/cursor/end blocks: 20 boundary cases + 2 allocation-limit rejections passed");
 printf("actual ISP/PDS/size fixtures=%u; v1 region capacity=%u\n",fixtures,capacity);
#else
 assert(pvr_transfer_prim_blocks_per_alloc(&d)==8);
 puts("generic policy remains disabled");
#endif
 return 0;
}
'''
    (out / 'fixture.c').write_text(h)
    for darwin in (False, True):
        exe = out / ('darwin' if darwin else 'generic')
        cmd = [os.environ.get('CC', 'cc'), '-std=gnu11', '-g', '-O1',
            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
            '-I' + str(out), '-I' + str(img)]
        if sys.platform.startswith('linux'):
            cmd += ['-fno-pie', '-no-pie']
        if darwin:
            cmd += ['-DPVR_WINSYS_DARWIN']
        subprocess.run(cmd + [str(out / 'fixture.c'), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
    print('正本パッチ＋固定版Mesaから生成・実行完了（GPU実行は未検証）')
