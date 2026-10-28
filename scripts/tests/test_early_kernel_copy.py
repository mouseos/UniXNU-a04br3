#!/usr/bin/env python3
"""Exercise the actual early copy probe with a bounded host fixture."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstdarg>
#include <cstring>
#include <string>
static int enabled, allocs, frees, fail_at, bad_cmp, stopped_clock, snap_count;
static uint64_t mem[7] = {4096, 10000, 30, 5000, 60, 0, 100};
static uint64_t tick;
static std::string logdata;
int PE_parse_boot_argn(const char *k, void *p, size_t n) { assert(!strcmp(k,"ecb") && n==sizeof(unsigned)); *(unsigned*)p=enabled; return enabled!=0; }
void boot_memory_snapshot(uint64_t *p) { ++snap_count; memcpy(p,mem,sizeof(mem)); }
void IOLog(const char *fmt, ...) { char b[1024]; va_list ap; va_start(ap,fmt); vsnprintf(b,sizeof(b),fmt,ap); va_end(ap); logdata+=b; }
void *IOMalloc(size_t n) { ++allocs; return allocs==fail_at ? nullptr : malloc(n); }
void IOFree(void *p,size_t) { ++frees; free(p); }
uint64_t mach_absolute_time() { return stopped_clock ? 100 : ++tick; }
void absolutetime_to_nanoseconds(uint64_t n,uint64_t *out) { *out=n*100; }
int fake_compare(const void *a,const void *b,size_t n) { return bad_cmp ? 1 : memcmp(a,b,n); }
#define memcmp fake_compare
#include "A04BR3EarlyCopy.h"
#undef memcmp
static void reset() { enabled=1;allocs=frees=fail_at=bad_cmp=stopped_clock=snap_count=0;tick=0;logdata.clear();uint64_t m[7]={4096,10000,30,5000,60,0,100};memcpy(mem,m,sizeof(mem)); }
int main() {
 reset();enabled=0;a04br3_early_copy_probe();assert(!allocs && !snap_count && logdata.empty());
 reset();enabled=2;a04br3_early_copy_probe();assert(!allocs && !snap_count);
 reset();mem[6]=mem[1]+1;a04br3_early_copy_probe();assert(!allocs && logdata.find("invalid_snapshot")!=std::string::npos);
 reset();mem[1]=100;mem[6]=0;a04br3_early_copy_probe();assert(!allocs && logdata.find("insufficient_unoccupied")!=std::string::npos);
 reset();mem[1]=8419;a04br3_early_copy_probe();assert(!allocs && logdata.find("insufficient_unoccupied")!=std::string::npos);
 reset();mem[1]=8420;a04br3_early_copy_probe();assert(allocs==2 && frees==2 && logdata.find("phase=result")!=std::string::npos);
 reset();fail_at=1;a04br3_early_copy_probe();assert(allocs==1 && !frees && logdata.find("source_allocation")!=std::string::npos);
 reset();fail_at=2;a04br3_early_copy_probe();assert(allocs==2 && frees==1 && logdata.find("destination_allocation")!=std::string::npos);
 reset();a04br3_early_copy_probe();assert(allocs==2 && frees==2 && snap_count==2 && logdata.find("bytes=262144 elapsed_ns=100 verified=1")!=std::string::npos);
 reset();stopped_clock=1;a04br3_early_copy_probe();assert(frees==2 && logdata.find("invalid_elapsed")!=std::string::npos && logdata.find("phase=result")==std::string::npos);
 reset();bad_cmp=1;a04br3_early_copy_probe();assert(frees==2 && logdata.find("verification_failed")!=std::string::npos && logdata.find("phase=result")==std::string::npos);
}
'''
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp)
    (p/'test.cpp').write_text(source)
    subprocess.run(['c++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-I',str(root/'package'),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
board = (root/'package/A04BR3Board.cpp').read_text()
assert board.index('a04br3_autoreset_init();', board.index('extern "C" void unixnu_board_init')) < board.index('a04br3_early_copy_probe();') < board.index('a04br3_gpu_init();')
print('PASS early copy: gating, bounds, reserve, allocation failure, copy, verification and clock')
