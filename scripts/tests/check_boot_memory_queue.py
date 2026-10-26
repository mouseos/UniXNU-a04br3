#!/usr/bin/env python3
"""Compile actual supplemental/legacy getters with the pinned XNU config init,
struct and macros. No kernel, VM locks, card/device operations or SDK required.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PIN = 'd8b80295118ef25ac3a784134bcf95cd8e88109f'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--xnu-source', type=Path, required=True)
args = parser.parse_args()
xnu = args.xnu_source.resolve()
assert subprocess.check_output(['git', '-C', str(xnu), 'rev-parse', 'HEAD'], text=True).strip() == PIN
# Read the immutable pin, rather than trusting a potentially patched worktree.
def pinned(path):
    return subprocess.check_output(['git', '-C', str(xnu), 'show', f'{PIN}:{path}'], text=True)


def function(source, signature):
    start = source.index(signature)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


source = (ROOT / 'package/BootProcessProbe.c').read_text()
old = function(source, 'void boot_memory_snapshot(')
extra = function(source, 'void boot_memory_queue_snapshot(')
assert '#include <vm/vm_pageout.h>' in source
assert not any(token in extra for token in ('IOLog', 'IOLock', 'PE_parse', 'alloc(', 'vm_stats(', 'host_statistics', 'for (', 'while ('))
header = pinned('osfmk/vm/vm_pageout.h')
config = header[header.index('struct  vm_config {'):header.index('#define VM_PAGER_NOT_CONFIGURED')]
policy = header[header.index('#define VM_PAGER_NOT_CONFIGURED'):header.index('#endif  /* KERNEL_PRIVATE */', header.index('#define VM_PAGER_NOT_CONFIGURED'))]
init = function(pinned('osfmk/vm/vm_pageout.c'), 'void\nvm_config_init(void)')
# The getters' VM globals retain their exact pin types and names.
page_header = pinned('osfmk/vm/vm_page.h')
for name in ('vm_page_throttled_count', 'vm_page_pageable_internal_count', 'vm_page_pageable_external_count'):
    assert re.search(r'unsigned int\s+' + name + r'\s*;', page_header)
assert re.search(r'uint32_t\s+vm_lopage_free_count\s*;', page_header)
assert re.search(r'int\s+vm_compressor_mode\s*;', page_header)
program = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
#include "BootMemoryQueueSnapshot.h"
typedef int boolean_t;
#define TRUE 1
#define FALSE 0
#define OS_FALLTHROUGH __attribute__((fallthrough))
#define bzero(p,n) memset((p),0,(n))
#define PAGE_SIZE 4096
unsigned int vm_page_free_count,vm_page_active_count,vm_page_inactive_count,vm_page_wire_count,vm_page_speculative_count;
unsigned int vm_page_throttled_count,vm_page_pageable_internal_count,vm_page_pageable_external_count;
uint32_t vm_lopage_free_count;
int vm_compressor_mode;
struct object {uint64_t resident_page_count;};
struct object *compressor_object;
#define VM_PAGE_COMPRESSOR_COUNT (compressor_object->resident_page_count)
''' + config + '\nstruct vm_config vm_config;\n' + policy + '\n' + init + '\n' + old + '\n' + extra + r'''
static void check_mode(int mode,unsigned present,unsigned active,unsigned swap,unsigned freezer) {
 vm_compressor_mode=mode;vm_config_init();
 struct {uint64_t before;struct boot_memory_queue_record q;uint64_t after;} guarded;
 memset(&guarded,0xa5,sizeof guarded);boot_memory_queue_snapshot(&guarded.q);
 assert(guarded.before==0xa5a5a5a5a5a5a5a5ULL&&guarded.after==0xa5a5a5a5a5a5a5a5ULL);
 struct boot_memory_queue_record *q=&guarded.q;
 assert(q->compressor_mode==mode&&q->compressor_present==present&&q->compressor_active==active);
 assert(q->swap_present==present&&q->swap_active==swap&&q->freezer_swap_active==freezer);
 assert(q->throttled==61414&&q->lopage_free==19&&q->pageable_internal==100000&&q->pageable_external==20000);
}
int main(void) {
 boot_memory_queue_snapshot(NULL);
 vm_page_throttled_count=61414;vm_lopage_free_count=19;
 vm_page_pageable_internal_count=100000;vm_page_pageable_external_count=20000;
 check_mode(VM_PAGER_NOT_CONFIGURED,0,0,0,0);
 check_mode(VM_PAGER_COMPRESSOR_NO_SWAP,1,1,0,0);
 check_mode(VM_PAGER_COMPRESSOR_WITH_SWAP,1,1,1,0);
 check_mode(VM_PAGER_FREEZER_DEFAULT,1,0,0,0);
 check_mode(VM_PAGER_FREEZER_COMPRESSOR_NO_SWAP,1,0,0,0);
 check_mode(VM_PAGER_COMPRESSOR_NO_SWAP_PLUS_FREEZER_COMPRESSOR_WITH_SWAP,1,1,0,1);
 struct boot_memory_queue_record q;memset(&q,0xa5,sizeof q);
 vm_page_throttled_count=vm_lopage_free_count=vm_page_pageable_internal_count=vm_page_pageable_external_count=UINT32_MAX;
 vm_compressor_mode=INT32_MIN;memset(&vm_config,0,sizeof vm_config);boot_memory_queue_snapshot(&q);
 assert(q.throttled==UINT32_MAX&&q.lopage_free==UINT32_MAX&&q.pageable_internal==UINT32_MAX&&q.pageable_external==UINT32_MAX);
 assert(q.compressor_mode==INT32_MIN&&!q.compressor_active&&!q.swap_active&&!q.freezer_swap_active);
 // No cached state or interpretation of compressor page count as policy.
 vm_page_throttled_count=7;vm_config.compressor_is_active=TRUE;boot_memory_queue_snapshot(&q);
 assert(q.throttled==7&&q.compressor_active==1);
 uint64_t legacy[9];legacy[0]=legacy[8]=0x123456789abcdef0ULL;
 vm_page_free_count=1;vm_page_active_count=2;vm_page_inactive_count=3;vm_page_wire_count=4;vm_page_speculative_count=6;
 compressor_object=NULL;boot_memory_snapshot(legacy+1);
 assert(legacy[0]==0x123456789abcdef0ULL&&legacy[8]==legacy[0]);
 assert(legacy[1]==4096&&legacy[2]==1&&legacy[3]==2&&legacy[4]==3&&legacy[5]==4&&legacy[6]==0&&legacy[7]==6);
 struct object compressor={.resident_page_count=5};compressor_object=&compressor;boot_memory_snapshot(legacy+1);assert(legacy[6]==5);
 puts("PASS actual legacy7 + queue getter: pinned XNU config/macros/types, native modes 0/2/4/8/16/32, full-width counts, normalized flags, null/guarded output, live rereads, legacy bounds/semantics (ASan/UBSan)");
}
'''
with tempfile.TemporaryDirectory(prefix='boot-memory-queue-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(program)
    subprocess.run(['cc', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-pie', '-no-pie', '-I', str(ROOT / 'package'),
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True, timeout=30)
    # Public extra header must also be usable by Board's C++ translation unit.
    (path / 'header.cpp').write_text('#include "BootMemoryQueueSnapshot.h"\n'
                                   'using Getter=void(*)(boot_memory_queue_record *);\n'
                                   'Getter getter=&boot_memory_queue_snapshot;\n')
    subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'package'),
                    '-fsyntax-only', str(path / 'header.cpp')], check=True)
