"""実際のHWRT初期化関数とABI構造体を抽出してホストで実行する。実機アクセスなし。"""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest

DEVICE = Path(__file__).resolve().parents[2]
PVR = DEVICE / 'package/powervr'


def definition(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class HwrtTest(unittest.TestCase):
    def test_actual_mesa_macrotile_initialization(self):
        """固定Mesaと配布パッチから実関数を抽出し、kernelと同じ境界値を検証する。"""
        candidates = [DEVICE / '.cache/mesa-pvr', DEVICE.parent / '_research/mesa']
        if os.environ.get('PVR_MESA_SOURCE'):
            candidates.insert(0, Path(os.environ['PVR_MESA_SOURCE']))
        mesa = next((p for p in candidates if (p / '.git').exists()), None)
        if mesa is None:
            self.skipTest('Set PVR_MESA_SOURCE to the pinned Mesa checkout')
        pin = json.loads((DEVICE / 'scripts/data/pvr-mesa-inputs.json').read_text())['mesa']['commit']

        def original(name):
            return subprocess.check_output(['git', '-C', str(mesa), 'show', pin + ':' + name], text=True)

        render_path = 'src/imagination/vulkan/pvr_arch_job_render.c'
        patch = (DEVICE / 'patches/mesa/pvr-darwin-winsys.patch').read_text()
        section = next('diff --git ' + s for s in patch.split('diff --git ')[1:]
                       if s.startswith('a/' + render_path + ' '))
        with tempfile.TemporaryDirectory(prefix='powervr-mesa-hwrt-') as tmp:
            root = Path(tmp)
            render = root / render_path
            render.parent.mkdir(parents=True)
            render.write_text(original(render_path))
            # Apply only the reviewed render hunk in a temporary tree. Never edit Mesa's checkout.
            subprocess.run(['git', 'apply', '--unsafe-paths', '-'], cwd=root, input=section,
                           text=True, check=True, capture_output=True)
            source = render.read_text()
            utils = original('src/imagination/include/hwdef/rogue_hw_utils.h')
            header = original('src/imagination/vulkan/pvr_job_render.h')
            harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define ALIGN_POT(n,d) (((n)+(d)-1)&~((d)-1))
#define UNREACHABLE(msg) abort()
struct pvr_device_info {
 bool simple_internal_parameter_format;
 uint32_t simple_parameter_format_version, tile_size_x, tile_size_y;
};
#define PVR_HAS_FEATURE(dev,f) ((dev)->f)
#define PVR_GET_FEATURE_VALUE(dev,f,fallback) ((dev)->f)
#define PVR_FEATURE_VALUE(dev,f,out) (*(out)=(dev)->f, (dev)->f ? 0 : -1)
'''
            harness += definition(header, 'struct pvr_rt_mtile_info {') + ';\n'
            harness += 'static inline void\n' + definition(utils, 'rogue_get_num_macrotiles_xy(') + '\n'
            harness += definition(source, 'static inline void pvr_get_samples_in_xy(') + '\n'
            harness += definition(source, 'void pvr_arch_rt_mtile_info_init(') + '\n'
            harness += r'''
static void check(uint32_t version, uint32_t w, uint32_t h, uint32_t samples,
 uint32_t x1, uint32_t y1, uint32_t xmax, uint32_t ymax,
 uint32_t per_x, uint32_t per_y)
{
 struct pvr_device_info dev={version!=0,version,16,16};
 struct pvr_rt_mtile_info info;
 memset(&info,0xa5,sizeof(info));
 pvr_arch_rt_mtile_info_init(&dev,&info,w,h,samples);
 assert(info.tile_size_x==16 && info.tile_size_y==16);
 assert(info.num_tiles_x==DIV_ROUND_UP(w,16) && info.num_tiles_y==DIV_ROUND_UP(h,16));
 assert(info.mtile_x1==x1 && info.mtile_y1==y1);
 assert(info.x_tile_max==xmax && info.y_tile_max==ymax);
 assert(info.tiles_per_mtile_x==per_x && info.tiles_per_mtile_y==per_y);
 assert(info.mtiles_x==(version==1 ? 1u : 4u));
 assert(info.mtiles_y==(version==1 ? 1u : 4u));
 assert(info.mtile_x2==(version ? 0 : x1*2) && info.mtile_x3==(version ? 0 : x1*3));
 assert(info.mtile_y2==(version ? 0 : y1*2) && info.mtile_y3==(version ? 0 : y1*3));
}
int main(void)
{
 /* Same dimensions/sample counts as the kernel fixtures, plus the GE8300 limit. */
 check(1,64,64,1,4,4,3,3,4,4);
 check(1,65,33,1,5,3,4,2,5,3);
 check(1,1,1,1,1,1,0,0,1,1);
 check(1,33,65,2,3,5,2,4,3,10);
 check(1,33,65,4,3,5,2,4,6,10);
 check(1,65,33,4,5,3,4,2,10,6);
 check(1,4096,4096,4,256,256,255,255,512,512);
 check(2,65,33,1,2,2,5,3,2,2);
 check(2,257,129,4,6,4,17,9,12,8);
 check(0,65,33,1,4,4,4,2,4,4);
 check(0,257,129,4,8,4,16,8,16,8);
 puts("Mesa HWRT: 11 actual-function layout fixtures passed");
}
'''
            path, binary = root / 'mesa-hwrt.c', root / 'mesa-hwrt'
            path.write_text(harness)
            subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra',
                            '-Werror', '-fsanitize=address,undefined', '-g', str(path),
                            '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True, timeout=30)

    def test_actual_common_initialization(self):
        source = (PVR / 'kernel/pvr_hwrt.c').read_text()
        fwif = (PVR / 'linux/pvr_rogue_fwif.h').read_text()
        uapi = (PVR / 'linux/uapi/drm/pvr_drm.h').read_text()
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include "pvr_rogue_cr_defs_client.h"
typedef uint8_t u8;
typedef uint16_t __u16;
typedef uint32_t u32, __u32;
typedef uint64_t u64, __u64;
typedef u64 aligned_u64 __attribute__((aligned(8)));
#define __aligned(n) __attribute__((aligned(n)))
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define round_up(n,d) (((n)+(d)-1)&~((d)-1))
#define fls(n) ((n) ? 32-__builtin_clz(n) : 0)
static unsigned warnings;
#define WARN_ON(c) ({bool hit=!!(c); warnings+=hit; hit;})
#define WARN(c,...) WARN_ON(c)
struct pvr_device {
 bool has_tile_size_x, has_tile_size_y, has_isp_samples_per_pixel;
 bool has_simple_parameter_format_version;
 u32 tile_size_x, tile_size_y, isp_samples_per_pixel, simple_parameter_format_version;
};
#define PVR_HAS_FEATURE(dev,f) ((dev)->has_##f)
#define PVR_FEATURE_VALUE(dev,f,out) \
 ((dev)->has_##f ? (*(out)=(dev)->f,0) : -EINVAL)
struct pvr_file {struct pvr_device *pvr_dev;};
struct pvr_fw_object {unsigned unused;};
'''
        for name in ('drm_pvr_create_hwrt_geom_data_args',
                     'drm_pvr_create_hwrt_rt_data_args',
                     'drm_pvr_ioctl_create_hwrt_dataset_args'):
            harness += definition(uapi, 'struct ' + name + ' {') + ';\n'
        harness += definition(fwif, 'struct rogue_fwif_hwrtdata_common {') + ' __aligned(8);\n'
        harness += definition(source, 'struct pvr_rt_mtile_info {') + ';\n'
        harness += r'''
struct pvr_hwrt_dataset {
 struct rogue_fwif_hwrtdata_common common;
 struct pvr_fw_object *common_fw_obj;
};
static struct rogue_fwif_hwrtdata_common uploaded;
static struct pvr_fw_object object;
static int allocation_error;
#define PVR_BO_FW_FLAGS_DEVICE_UNCACHED 1
static int pvr_fw_object_create(struct pvr_device *dev, size_t size, u64 flags,
 void (*init)(void *,void *), void *priv, struct pvr_fw_object **out)
{
 assert(dev && size==88 && flags==PVR_BO_FW_FLAGS_DEVICE_UNCACHED);
 if (allocation_error) return allocation_error;
 memset(&uploaded,0xa5,sizeof(uploaded));
 init(&uploaded,priv);
 *out=&object;
 return 0;
}
'''
        for signature in ('static int\nget_cr_isp_mtile_size_val(',
                          'static int\nget_cr_multisamplectl_val(',
                          'static int\nget_cr_te_aa_val(',
                          'static void\nhwrtdata_common_init(',
                          'static int\nhwrt_init_common_fw_structure('):
            harness += definition(source, signature) + '\n'
        harness += r'''
static void check(u32 format,u32 w,u32 h,u32 samples,u32 spp,
 u32 te_screen,u32 mtile1,u32 mtile2,u32 stride,u32 isp_size,u32 teaa,u32 headers)
{
 struct pvr_device dev={true,true,true,format!=0,16,16,spp,format};
 struct pvr_file file={&dev};
 struct drm_pvr_ioctl_create_hwrt_dataset_args args={0};
 struct pvr_hwrt_dataset hwrt={0};
 args.width=w;args.height=h;args.samples=samples;args.layers=1;
 args.geom_data_args.tpc_stride=7;args.geom_data_args.tpc_size=4096;
 args.region_header_size=headers;
 args.isp_merge_lower_x=11;args.isp_merge_lower_y=12;
 args.isp_merge_upper_x=13;args.isp_merge_upper_y=14;
 args.isp_merge_scale_x=15;args.isp_merge_scale_y=16;
 warnings=0;
 assert(hwrt_init_common_fw_structure(&file,&args,&hwrt)==0);
 assert(warnings==0 && hwrt.common_fw_obj==&object);
 assert(memcmp(&uploaded,&hwrt.common,sizeof(uploaded))==0);
 assert(uploaded.te_screen==te_screen);
 assert(uploaded.te_mtile1==mtile1 && uploaded.te_mtile2==mtile2);
 assert(uploaded.mtile_stride==stride && uploaded.isp_mtile_size==isp_size);
 assert(uploaded.teaa==teaa);
 assert(uploaded.screen_pixel_max==((w-1)|((h-1)<<16)));
 assert(uploaded.tpc_stride==7 && uploaded.tpc_size==4096);
 assert(uploaded.rgn_header_size==headers); /* v1 header count is not converted */
 assert(!uploaded.geom_caches_need_zeroing);
 assert(uploaded.isp_merge_lower_x==11 && uploaded.isp_merge_lower_y==12);
 assert(uploaded.isp_merge_upper_x==13 && uploaded.isp_merge_upper_y==14);
 assert(uploaded.isp_merge_scale_x==15 && uploaded.isp_merge_scale_y==16);
 if(samples==1) {
  assert(uploaded.multi_sample_ctl==0x88 && uploaded.flipped_multi_sample_ctl==0x88);
 }
}
int main(void)
{
 /* Independent register fixtures: v1 uses the whole target as one macrotile. */
 check(1,64,64,1,1,0x3003,0x100000,0x100000,16,0x40004,0,16);
 check(1,65,33,1,1,0x2004,0x140000,0xc0000,15,0x50003,0,15);
 check(1,1,1,1,1,0,0x40000,0x40000,1,0x10001,0,1);
 check(1,33,65,2,1,0x4002,0xc0000,0x140000,15,0x3000a,4,30);
 check(1,33,65,4,1,0x4002,0xc0000,0x140000,15,0x6000a,6,60);
 check(1,65,33,4,2,0x2004,0x140000,0xc0000,15,0x50006,5,60);
 check(1,65,33,4,4,0x2004,0x140000,0xc0000,15,0x50003,9,60);
 /* Existing v2 grouped tiles and legacy 16-macrotile layouts stay unchanged. */
 check(2,65,33,1,1,0x3005,0x80000,0x80000,4,0x20002,0,16);
 check(2,257,129,4,1,0x9011,0x180000,0x100000,24,0xc0008,6,384);
 check(0,65,33,1,1,0x2004,0x100000|0x1000|12,0x100000|0x1000|12,
       16,0x40004,0,320);
 check(0,257,129,4,1,0x8010,0x200000|0x2000|24,0x100000|0x1000|12,
       32,0x100008,6,2560);
 /* Errors must still propagate without publishing an FW object. */
 struct pvr_device dev={true,true,true,true,16,16,1,1};
 struct pvr_file file={&dev};
 struct drm_pvr_ioctl_create_hwrt_dataset_args args={.width=64,.height=64,.samples=1};
 struct pvr_hwrt_dataset hwrt={0};
 dev.has_tile_size_y=false;
 assert(hwrt_init_common_fw_structure(&file,&args,&hwrt)==-EINVAL);
 assert(!hwrt.common_fw_obj);
 dev.has_tile_size_y=true;args.samples=0;
 assert(hwrt_init_common_fw_structure(&file,&args,&hwrt)==-EINVAL);
 args.samples=1;allocation_error=-ENOMEM;
 assert(hwrt_init_common_fw_structure(&file,&args,&hwrt)==-ENOMEM);
 assert(!hwrt.common_fw_obj);
 puts("HWRT: 11 register/ABI fixtures and 3 error paths passed");
}
'''
        with tempfile.TemporaryDirectory(prefix='powervr-hwrt-') as tmp:
            path, binary = Path(tmp) / 'hwrt.c', Path(tmp) / 'hwrt'
            path.write_text(harness)
            subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra',
                            '-Werror', '-fsanitize=address,undefined', '-g', '-I',
                            str(PVR / 'linux'), str(path), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True, timeout=30)


if __name__ == '__main__':
    unittest.main()
