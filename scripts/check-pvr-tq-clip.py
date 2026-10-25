#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""実MesaのTQ wrapper/reroute関数をモックcoreで検証する。GPU動作試験ではない。"""
from pathlib import Path
import argparse
import os
import re
import runpy
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--mesa', type=Path, default=ROOT / '.cache/mesa-pvr')
args = p.parse_args()
src = (args.mesa / 'src/imagination/vulkan/pvr_arch_job_transfer.c').read_text()


def function(signature):
    start = src.index(signature)
    b = src.index('{', start)
    depth, end = 1, b + 1
    while depth:
        depth += (src[end] == '{') - (src[end] == '}')
        end += 1
    return src[start:end]


wrapper = function('static VkResult pvr_3d_copy_blit(')
reroute = function('static VkResult pvr_reroute_to_clip(')
policy = function('static bool pvr_darwin_tq_clip_enabled(')
# Full custom-mapping branch returns before the opt-in. Clip's background setup
# goes directly to core; it cannot recursively enter the instrumented wrapper.
assert wrapper.index('return result;') < wrapper.index('pvr_darwin_tq_clip_enabled(')
clip = function('static VkResult pvr_3d_clip_blit(')
assert 'pvr_3d_copy_blit_core(ctx, &bg_cmd' in clip and 'pvr_3d_copy_blit(' not in clip
ctrl = function('static VkResult pvr_isp_ctrl_stream(')
assert 'num_sources = fill_blit ? 1U' in ctrl and 'pvr_isp_primitive_block_size(dev_info,' in ctrl
assert 'ROGUE_CR_ISP_RENDER_MODE_TYPE_FAST_2D' in ctrl

h = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
typedef int VkResult;
#define VK_SUCCESS 0
#define VK_ERROR_FORMAT_NOT_SUPPORTED -11
#define PVR_FILTER_POINT 0
#define PVR_TRANSFER_CMD_FLAGS_DSMERGE 1u
#define PVR_TRANSFER_CMD_FLAGS_FILL 2u
#define PVR_TRANSFER_CMD_FLAGS_FAST2D 4u
#define FALLTHROUGH __attribute__((fallthrough))
#define MAX2(a,b) ((a)>(b)?(a):(b))
#define PVR_HAS_FEATURE(d,f) ((d)->f)
#define PVR_GET_FEATURE_VALUE(d,f,z) ((d)->f)
#define PVR_HAS_ENHANCEMENT(d,n) ((d)->enh42064)
struct pvr_device_info {struct{unsigned b,v,n,c;}ident;bool tile_size_16x16;unsigned simple_parameter_format_version;bool enh42064;};
struct pvr_physical_device {struct pvr_device_info dev_info;};
struct pvr_device {struct pvr_physical_device *pdevice;};
struct pvr_transfer_ctx {struct pvr_device *device;};
typedef struct VkRect2D {struct{int x,y;}offset;struct{unsigned width,height;}extent;} VkRect2D;
struct pvr_rect_mapping {VkRect2D src_rect,dst_rect;};
struct surface {int vk_format;unsigned sample_count;};
struct pvr_transfer_cmd_source {struct surface surface;struct pvr_rect_mapping mappings[4];};
struct pvr_transfer_cmd {unsigned flags,source_count;struct surface dst;struct pvr_transfer_cmd_source sources[3];VkRect2D scissor;unsigned clear[4];};
struct pvr_transfer_pass {struct{unsigned mapping_count;struct pvr_rect_mapping mappings[4];}sources[3];unsigned source_count;};
struct mapping {unsigned pass_count,texel_extend_src,texel_extend_dst;bool double_stride;struct pvr_transfer_pass passes[4];};
struct pvr_transfer_3d_state {bool dont_force_pbe,custom_filter;unsigned pass_idx;int filter[3];struct mapping custom_mapping;};
struct pvr_transfer_prep_data {struct pvr_transfer_3d_state state;};
static struct mapping prepared;
static struct pvr_transfer_cmd last;
static unsigned coreCalls,clipCalls,modified,fixtures;
static int injectedError;
static void pvr_transfer_set_filter(struct pvr_transfer_cmd *c,struct pvr_transfer_3d_state *s){(void)c;s->filter[0]=PVR_FILTER_POINT;}
static unsigned vk_format_get_blocksizebits(int f){(void)f;return 32;}
static void pvr_get_custom_mapping(const struct pvr_device_info *d,struct pvr_transfer_cmd *c,unsigned n,struct mapping *m){(void)d;(void)c;assert(n==3);*m=prepared;}
static void pvr_modify_command(struct mapping *m,unsigned pass,struct pvr_transfer_cmd *c){assert(pass<m->pass_count);++modified;c->clear[0]=0x1234;}
static VkResult vk_error(struct pvr_device *d,VkResult e){(void)d;return e;}
static VkResult pvr_3d_copy_blit_core(struct pvr_transfer_ctx *c,struct pvr_transfer_cmd *t,struct pvr_transfer_prep_data *p,unsigned pass,bool *done){(void)c;(void)p;(void)pass;++coreCalls;last=*t;*done=true;return injectedError?injectedError:10;}
static VkResult pvr_3d_clip_blit(struct pvr_transfer_ctx *c,struct pvr_transfer_cmd *t,struct pvr_transfer_prep_data *p,unsigned pass,bool *done){(void)c;(void)p;(void)pass;++clipCalls;last=*t;*done=true;return injectedError?injectedError:20;}
'''
formats = sorted(set(re.findall(r'\bVK_FORMAT_\w+', wrapper)))
h += 'enum {' + ','.join(formats) + '};\n' + reroute + '\n'
h += '#ifdef PVR_WINSYS_DARWIN\n' + policy + '\n#endif\n' + wrapper + '\n'
h += r'''
static void check(struct pvr_transfer_ctx *ctx,struct pvr_transfer_cmd *cmd,bool wantClip){
 struct pvr_transfer_cmd original=*cmd;struct pvr_transfer_prep_data prep={0};bool done=false;
 coreCalls=clipCalls=modified=0;injectedError=0;
 VkResult r=pvr_3d_copy_blit(ctx,cmd,&prep,0,&done);
 assert(r==(wantClip?20:10)&&done&&coreCalls==!wantClip&&clipCalls==wantClip);
 assert(!memcmp(&original,cmd,sizeof(*cmd))); // caller's original command is untouched
 if(wantClip){assert(last.flags&PVR_TRANSFER_CMD_FLAGS_FAST2D);assert(!memcmp(&last.scissor,&cmd->scissor,sizeof(cmd->scissor)));}
 ++fixtures;
}
int main(void){
 struct pvr_physical_device pd={.dev_info={.ident={22,40,54,30},.tile_size_16x16=true,.simple_parameter_format_version=1}};
 struct pvr_device dev={&pd};struct pvr_transfer_ctx ctx={&dev};
 struct pvr_transfer_cmd cmd={.flags=PVR_TRANSFER_CMD_FLAGS_FILL,.source_count=0,.dst={1000,1},.scissor={{3,5},{388,12}},.clear={0xaabbccdd,2,3,4}};
 const char *values[]={NULL,"","0","true","01","1x","1"};
 for(unsigned i=0;i<sizeof(values)/sizeof(*values);++i){if(values[i])assert(!setenv("PVR_DARWIN_TQ_CLIP",values[i],1));else assert(!unsetenv("PVR_DARWIN_TQ_CLIP"));
#ifdef PVR_WINSYS_DARWIN
 check(&ctx,&cmd,values[i]&&!strcmp(values[i],"1"));
#else
 check(&ctx,&cmd,false);
#endif
 }
 assert(!setenv("PVR_DARWIN_TQ_CLIP","1",1));
#ifdef PVR_WINSYS_DARWIN
 assert(last.source_count==0&&last.clear[0]==0xaabbccdd&&(last.flags&PVR_TRANSFER_CMD_FLAGS_FILL));
 // Every nonmatching identity/feature falls through to original core.
 unsigned *ids[]={&pd.dev_info.ident.b,&pd.dev_info.ident.v,&pd.dev_info.ident.n,&pd.dev_info.ident.c};
 for(unsigned i=0;i<4;++i){++*ids[i];check(&ctx,&cmd,false);--*ids[i];}
 pd.dev_info.tile_size_16x16=false;check(&ctx,&cmd,false);pd.dev_info.tile_size_16x16=true;
 pd.dev_info.simple_parameter_format_version=2;check(&ctx,&cmd,false);pd.dev_info.simple_parameter_format_version=1;
#endif
 // One source uses the existing RAW format transformation (active_cmd), not the original.
 cmd.flags=0;cmd.source_count=1;cmd.sources[0].surface=cmd.dst;
#ifdef PVR_WINSYS_DARWIN
 check(&ctx,&cmd,true);
#else
 check(&ctx,&cmd,false);
#endif
 assert(last.dst.vk_format==VK_FORMAT_R32_UINT);
 cmd.source_count=2;check(&ctx,&cmd,false);
 // Custom mapping single-source/core and double-stride/clip paths remain unchanged.
 cmd.source_count=1;prepared.pass_count=2;prepared.texel_extend_src=prepared.texel_extend_dst=1;
 for(unsigned i=0;i<2;++i){prepared.passes[i].source_count=1;prepared.passes[i].sources[0].mapping_count=1;prepared.passes[i].sources[0].mappings[0].dst_rect=cmd.scissor;}
 check(&ctx,&cmd,false);assert(modified==1);
 struct pvr_transfer_prep_data prep={0};bool done=false;
 prep.state.custom_mapping=prepared;clipCalls=coreCalls=0;
 assert(pvr_3d_copy_blit(&ctx,&cmd,&prep,1,&done)==10&&coreCalls==1&&!clipCalls);
 prepared.double_stride=true;prep=(struct pvr_transfer_prep_data){0};coreCalls=clipCalls=0;
 assert(pvr_3d_copy_blit(&ctx,&cmd,&prep,0,&done)==20&&clipCalls==1&&!coreCalls);
 // Native DS-merge reroute is independent of the opt-in and remains intact.
 prepared=(struct mapping){0};assert(!unsetenv("PVR_DARWIN_TQ_CLIP"));cmd.flags=PVR_TRANSFER_CMD_FLAGS_DSMERGE;
 check(&ctx,&cmd,true);
 // Error/finished semantics of existing reroute are propagated unchanged.
 injectedError=-7;prep=(struct pvr_transfer_prep_data){0};done=false;
 assert(pvr_reroute_to_clip(&ctx,&cmd,&cmd.scissor,&prep,0,&done)==-7&&done);
 cmd.source_count=2;unsigned old=clipCalls;assert(pvr_reroute_to_clip(&ctx,&cmd,NULL,&prep,0,&done)==VK_ERROR_FORMAT_NOT_SUPPORTED&&clipCalls==old);
 printf("PASS %u policy/fallback cases + actual FILL/reroute, active command, custom multipass, DSmerge/error paths (%s)\n",fixtures,
#ifdef PVR_WINSYS_DARWIN
 "Darwin"
#else
 "generic"
#endif
 );
}
'''

builder = runpy.run_path(str(ROOT / 'scripts/build-a04br3-powervr-probe.py'))
ctor = builder['tq_clip_probe_source']()
bs = (ROOT / 'scripts/build-a04br3-powervr-probe.py').read_text()
assert "if args.tq_clip:" in bs and "parser.error('--tq-clip requires --vulkan')" in bs
with tempfile.TemporaryDirectory(prefix='pvr-tq-clip-') as tmp:
    tmp = Path(tmp)
    source = tmp / 'actual.c'
    source.write_text(h)
    common = [os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror',
              '-Wno-unused-function', '-Wno-unused-parameter', '-fsanitize=address,undefined', '-g']
    if sys.platform == 'linux':
        common += ['-fno-pie', '-no-pie']
    for darwin in (False, True):
        binary = tmp / ('darwin' if darwin else 'generic')
        subprocess.run(common + (['-DPVR_WINSYS_DARWIN=1'] if darwin else []) + [str(source), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=30)
    owner = tmp / 'owner.c'
    owner.write_text(ctor + '\n#include <assert.h>\n#include <string.h>\nint main(void){assert(!strcmp(getenv("PVR_DARWIN_TQ_CLIP"),"1"));return 0;}\n')
    binary = tmp / 'owner'
    subprocess.run(common + [str(owner), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], env={**os.environ, 'PVR_DARWIN_TQ_CLIP': '0'}, check=True)
    # Initialization must fail visibly instead of silently using the wrong path.
    owner.write_text(ctor.replace('setenv(', 'fail_setenv(').replace('#include <stdio.h>', '#include <stdio.h>\nstatic int fail_setenv(const char *a,const char *b,int c){(void)a;(void)b;(void)c;return -1;}') + '\nint main(void){return 99;}\n')
    subprocess.run(common + [str(owner), '-o', str(binary)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode == 1 and 'cannot enable TQ clip' in result.stderr
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/build-a04br3-powervr-probe.py'), '--check', '--tq-clip'], capture_output=True, text=True)
    assert result.returncode == 2 and '--tq-clip requires --vulkan' in result.stderr
print('PASS ASan/UBSan actual wrapper/reroute + source control-flow assertions + owner-only startup/failed-init/CLI gate; no GPU test')
