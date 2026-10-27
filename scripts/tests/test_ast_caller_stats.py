#!/usr/bin/env python3
from pathlib import Path
import subprocess,tempfile
s=(Path(__file__).resolve().parents[2]/'package/BootProcessProbe.c').read_text();start=s.index('// Optional AST call-site histogram.');code='#include <stdint.h>\n#include <stdio.h>\n#include <assert.h>\n#include <pthread.h>\n'+s[start:]+r'''
static void *writer(void *a){unsigned c=(unsigned)(uintptr_t)a;for(unsigned i=0;i<64000;i++)native_ast_caller_sample(c,0x1000+c*4);return 0;}
int main(void){
 native_ast_caller_sample(0,0x1000);assert(ast_caller_rows[0].seen==0);boot_ast_caller_enable(1);boot_ast_caller_report();
 pthread_t t[4];for(unsigned i=0;i<4;i++)assert(!pthread_create(&t[i],0,writer,(void *)(uintptr_t)i));for(unsigned i=0;i<4;i++)assert(!pthread_join(t[i],0));
 for(unsigned i=0;i<4;i++){assert(ast_caller_rows[i].seen==64000);assert(ast_caller_rows[i].slots[0].count==1000);assert(!ast_caller_rows[i].dropped);}
 native_ast_caller_sample(4,0x1000);assert(ast_caller_bad_cpu==1);
 for(unsigned i=0;i<33;i++)for(unsigned n=0;n<64;n++)native_ast_caller_sample(0,0x2000+4*i);
 assert(ast_caller_rows[0].dropped==2);boot_ast_caller_report();boot_ast_caller_enable(0);native_ast_caller_sample(0,0x1000);assert(ast_caller_rows[0].seen==66112);
}
'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.c').write_text(code);subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-pthread','-fsanitize=address,undefined','-fno-pie','-no-pie',str(p/'test.c'),'-o',str(p/'test')],check=True);r=subprocess.check_output([str(p/'test')],text=True,timeout=30);assert 'unavailable: hook not observed' in r and 'cpu=0 pc=1000 samples=1000' in r and 'dropped=2' in r
print('PASS AST caller: default off, four single-writer CPUs, sampling, bounds, full table and report')
