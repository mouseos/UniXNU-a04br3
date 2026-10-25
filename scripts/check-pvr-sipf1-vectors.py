#!/usr/bin/env python3
"""Check vendor-observed SIPF v1 records from the canonical patch; no SDK/GPU/Mesa checkout needed."""
from pathlib import Path
import os, subprocess, tempfile
root = Path(__file__).resolve().parents[1]
patch = (root / 'patches/mesa/pvr-darwin-winsys.patch').read_text()
marker = 'diff --git a/src/imagination/vulkan/pvr_darwin_sipf1.h b/src/imagination/vulkan/pvr_darwin_sipf1.h'
assert patch.count(marker) == 1
section = patch.split(marker, 1)[1].split('\ndiff --git ', 1)[0]
header = '\n'.join(line[1:] for line in section.splitlines() if line.startswith('+') and not line.startswith('+++')) + '\n'
# Literal records are from the observed vendor clip/primitive emitters, not a
# second implementation of the encoder. Bounds are rejected without GPU access.
fixture = '#include <assert.h>\n#include <stdint.h>\n#include "pvr_darwin_sipf1.h"\nint main(void){\n uint32_t h=0; uint64_t w=0;\n assert(pvr_sipf1_window_offset(0xe400000000ULL,0xe400000080ULL,&w)&&w==0x80);\n assert(pvr_sipf1_window_offset(0x10000,0x10080,&w)&&w==0x10080);\n assert(pvr_sipf1_window_offset(0x10000,0x3fffffff8ULL,&w)&&w==0x3fffffff8ULL);\n assert(!pvr_sipf1_window_offset(0x10000,0xfff8,&w));\n assert(!pvr_sipf1_window_offset(0x10000,0x400000000ULL,&w));\n assert(!pvr_sipf1_window_offset(1ULL<<40,1ULL<<40,&w));\n assert(!pvr_sipf1_window_offset(0,1ULL<<40,&w));\n assert(!pvr_sipf1_window_offset(0,0x10000,0));\n assert(pvr_sipf1_vertex_header(4,0,&h)&&h==0x60);\n assert(pvr_sipf1_vertex_header(4,8,&h)&&h==0x2060);\n assert(pvr_sipf1_vertex_header(4,16,&h)&&h==0x4060);\n assert(pvr_sipf1_vertex_header(16,8,&h)&&h==0x21e0);\n assert(pvr_sipf1_primitive(0x80,1,true,&w)&&w==UINT64_C(0x2100000328));\n assert(pvr_sipf1_primitive(0x80,1,false,&w)&&w==UINT64_C(0x2100000308));\n assert(pvr_sipf1_primitive(0x80,2,false,&w)&&w==UINT64_C(0x2100000f08));\n assert(pvr_sipf1_primitive(0x80,3,false,&w)&&w==UINT64_C(0x2100003f28));\n assert(pvr_sipf1_primitive(0x80,4,false,&w)&&w==UINT64_C(0x210000ff28));\n assert(pvr_sipf1_link(0x80,&w)&&w==UINT64_C(0x2000000001));\n assert(pvr_sipf1_primitive(0x3fffffff8ULL,1,true,&w));\n assert(!pvr_sipf1_primitive(0x400000000ULL,1,true,&w));\n assert(!pvr_sipf1_primitive(1,1,true,&w));\n assert(!pvr_sipf1_primitive(0,0,false,&w));\n assert(!pvr_sipf1_primitive(0,5,false,&w));\n assert(!pvr_sipf1_primitive(0,2,true,&w));\n assert(!pvr_sipf1_vertex_header(0,0,&h));assert(!pvr_sipf1_vertex_header(5,0,&h));assert(!pvr_sipf1_vertex_header(20,0,&h));assert(!pvr_sipf1_vertex_header(4,512,&h));\n return 0;\n}\n'
with tempfile.TemporaryDirectory(prefix='a04br3-sipf1-') as tmp:
    tmp = Path(tmp)
    (tmp/'pvr_darwin_sipf1.h').write_text(header)
    (tmp/'vectors.c').write_text(fixture)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-I'+str(tmp),
                    str(tmp/'vectors.c'), '-o', str(tmp/'vectors')], check=True)
    subprocess.run([str(tmp/'vectors')], check=True, timeout=30)
print('PASS 28 vendor-record/window/boundary assertions with actual canonical encoders, ASan/UBSan; no GPU test')
