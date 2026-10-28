#!/usr/bin/env python3
"""Compile and exercise the actual board launch hook with a mock kernel ABI."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    for name in ('sys', 'pexpert'):
        (d / name).mkdir()
    (d / 'sys/param.h').write_text('#include <stdint.h>\ntypedef uint64_t user_addr_t; typedef uint64_t user64_addr_t;\n#define USER_ADDR_NULL 0\n')
    (d / 'sys/systm.h').write_text('#include <stdio.h>\n#include <stddef.h>\nint copyout(const void*, unsigned long, size_t); int copyin(unsigned long, void*, size_t);\n')
    (d / 'sys/proc.h').write_text('typedef void *proc_t;\n')
    (d / 'sys/sysproto.h').write_text('struct posix_spawn_args { unsigned long pid,path,adesc,argv,envp; };\nint posix_spawn(void*,struct posix_spawn_args*,int*);\n')
    (d / 'pexpert/pexpert.h').write_text('int PE_parse_boot_argn(const char*, void*, int);\n')
    harness = d / 'test.c'
    harness.write_text(r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stddef.h>
#include <sys/sysproto.h>
static unsigned char area[4096];
static int enabled, short_enabled, spawns, fail;
int PE_parse_boot_argn(const char *name, void *v, int n) {
 (void)n; if (!strcmp(name,"mb")) { *(uint32_t *)v=short_enabled; return short_enabled!=0; }
 if (strcmp(name,"a04br3-membench")) return 0;
 *(uint32_t *)v=enabled; return enabled!=0;
}
int copyout(const void *p, unsigned long a, size_t n) { memcpy((void*)a,p,n); return 0; }
int copyin(unsigned long a, void *p, size_t n) { memcpy(p,(void*)a,n); return 0; }
int posix_spawn(void *p, struct posix_spawn_args *a, int *r) {
 (void)p; (void)r; ++spawns;
 assert(!strcmp((char*)a->path,"/private/var/ngpu/bin/a04br3-memory-copy-bench"));
 uint64_t *v=(uint64_t*)a->argv; assert(v[0]==a->path && !strcmp((char*)v[1],"--boot-run") && !v[2]);
 assert(!*(uint64_t*)a->envp && !a->adesc); *(int*)a->pid=42; return fail;
}
int a04br3_memory_bench_spawn(void *,uint64_t);
int main(void) { uint64_t scratch=(uint64_t)area; enabled=0;
 assert(!a04br3_memory_bench_spawn(0,scratch) && !spawns);
 enabled=2; assert(!a04br3_memory_bench_spawn(0,scratch) && !spawns);
 enabled=0; short_enabled=1; fail=5; assert(a04br3_memory_bench_spawn(0,scratch)==5 && spawns==1);
 fail=0; assert(!a04br3_memory_bench_spawn(0,scratch) && spawns==2);
 assert(!a04br3_memory_bench_spawn(0,scratch) && spawns==2); return 0; }
''')
    exe = d / 'test'
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I',str(d),str(harness),str(root/'package/A04BR3ProbeLaunch.c'),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
print('PASS: disabled, invalid, spawn failure, argv, retry and one successful spawn')
