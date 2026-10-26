#!/usr/bin/env python3
"""Compile actual Linux PVR PTE builders and the native map's flag expression."""
from pathlib import Path
import os,re,subprocess,tempfile
R=Path(__file__).resolve().parents[2]
S=(R/'package/powervr/kernel/pvr_mmu.c').read_text()
G=(R/'package/powervr/NativePowerVRGpu.cpp').read_text()
def function(source,signature):
 start=source.index(signature);b=source.index('{',start);e=b+1;n=1
 while n:n+=(source[e]=='{')-(source[e]=='}');e+=1
 return source[start:e]
macros=S[S.index('#define PVR_PAGE_TABLE_TYPEOF_ENTRY'):S.index('/**',S.index('#define PVR_PAGE_TABLE_TYPEOF_ENTRY'))]
assignment=re.search(r'flags_raw = pvr_page_flags_raw_create\([^;]+;',S[S.index('int pvr_mmu_map('):]).group()
uapi=(R/'package/powervr/linux/uapi/drm/pvr_drm.h').read_text()
defs='\n'.join(re.search(r'^#define '+name+r'[^\n]+',uapi,re.M).group() for name in ['DRM_PVR_BO_BYPASS_DEVICE_CACHE','DRM_PVR_BO_PM_FW_PROTECT','DRM_PVR_BO_ALLOW_CPU_USERSPACE_ACCESS'])
h=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include "native_powervr_bo_flags.h"
#include "pvr_rogue_mmu_defs.h"
#define _BITULL(n) (1ULL<<(n))
#define typeof_member(type,member) __typeof__(((type *)0)->member)
#define WRITE_ONCE(x,v) ((x)=(v))
#define ROGUE_MMUCTRL_PAGE_X_RANGE_CLRMSK ROGUE_MMUCTRL_PAGE_4KB_RANGE_CLRMSK
typedef uint64_t dma_addr_t;
struct pvr_page_table_l0_entry_raw {uint64_t val;};
struct pvr_page_flags_raw {struct pvr_page_table_l0_entry_raw val;};
'''+defs+'\n'+macros+'\nstatic struct pvr_page_flags_raw\n'+function(S,'\npvr_page_flags_raw_create(')+'\nstatic void\n'+function(S,'\npvr_page_table_l0_entry_raw_set(')+r'''
static uint64_t pte(uint64_t flags) {
 struct pvr_page_flags_raw flags_raw;
'''+assignment+r'''
 struct pvr_page_table_l0_entry_raw entry={0};
 pvr_page_table_l0_entry_raw_set(&entry,0x88001000,flags_raw);
 return entry.val;
}
int main(void) {
 assert(ROGUE_MMUCTRL_PT_DATA_READ_ONLY_EN==2);
 assert(!(NATIVE_POWERVR_BO_READ_ONLY&(DRM_PVR_BO_BYPASS_DEVICE_CACHE|DRM_PVR_BO_PM_FW_PROTECT|DRM_PVR_BO_ALLOW_CPU_USERSPACE_ACCESS)));
 for(unsigned cache=0;cache<2;++cache)for(unsigned pm=0;pm<2;++pm) {
  uint64_t flags=(cache?DRM_PVR_BO_BYPASS_DEVICE_CACHE:0)|(pm?DRM_PVR_BO_PM_FW_PROTECT:0);
  uint64_t rw=pte(flags),ro=pte(flags|NATIVE_POWERVR_BO_READ_ONLY);
  assert(!(rw&2)&&(ro&2));assert((rw^ro)==2);
  assert((ro&0xfffff000ULL)==0x88001000);
  assert(!(ro&NATIVE_POWERVR_BO_READ_ONLY));
 }
 puts("PASS actual pvr_mmu_map flags -> Linux builder -> final 64-bit PTE: immutable RO becomes bit1 only; PA/cache/PM flags preserved");
}
'''
# The user cannot supply or clear the internal flag; every map consumes the BO.
assert 'static_assert(!(NATIVE_POWERVR_BO_READ_ONLY & DRM_PVR_BO_FLAGS_MASK)' in G
mapping=function(G,'int vmMapLocked(')
assert mapping.count('size, bo->flags)')==2
request=function(G,'int NativePowerVRUserClient::importHost(')
assert 'args->handle || args->read_only' in request
assert '~static_cast<uint64_t>(DRM_PVR_BO_BYPASS_DEVICE_CACHE)' in request
assert 'args->read_only = (bo->flags & NATIVE_POWERVR_BO_READ_ONLY) != 0;' in request
native=(R/'package/powervr/NativePowerVR.cpp').read_text()
forward=function(native,'static int vmMapSg(')
assert 'pvr_mmu_map(op, size, flags, device_addr)' in forward
for api in ('native_powervr_vm_map_sg(', 'native_powervr_vm_map_scanout_sg('):
    wrapper=function(native,'extern \"C\" int '+api)
    assert 'size, flags,' in wrapper
alias=function(G,'IOReturn NativePowerVRUserClient::clientMemoryForType(')
assert 'else if (!(bo->flags & DRM_PVR_BO_ALLOW_CPU_USERSPACE_ACCESS))' in alias
with tempfile.TemporaryDirectory(prefix='pvr-readonly-pte-') as tmp:
 src=Path(tmp)/'test.c';src.write_text(h);binary=src.with_suffix('')
 subprocess.run([os.environ.get('CC','cc'),'-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                 '-I'+str(R/'package/powervr'),'-I'+str(R/'package/powervr/linux'),str(src),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True,timeout=30)
