#!/usr/bin/env python3
from pathlib import Path
import subprocess,tempfile
s=(Path(__file__).resolve().parents[2]/'package/BootProcessProbe.c').read_text();start=s.index('// Optional AST call-site histogram.');code='#include <stdint.h>\n#include <stdio.h>\n#include <assert.h>\n#include <pthread.h>\n#include <string.h>\n'+s[start:]+r'''
static void *writer(void *a){unsigned c=(unsigned)(uintptr_t)a;for(unsigned i=0;i<64000;i++)native_ast_caller_sample(c,0x1000+c*4);return 0;}
int main(void){
 native_ast_caller_sample(0,0x1000);assert(ast_caller_rows[0].seen==0);boot_ast_caller_enable(1);boot_ast_caller_report();
 pthread_t t[4];for(unsigned i=0;i<4;i++)assert(!pthread_create(&t[i],0,writer,(void *)(uintptr_t)i));for(unsigned i=0;i<4;i++)assert(!pthread_join(t[i],0));
 for(unsigned i=0;i<4;i++){assert(ast_caller_rows[i].seen==64000);assert(ast_caller_rows[i].slots[0].count>700 && ast_caller_rows[i].slots[0].count<1300);assert(!ast_caller_rows[i].dropped);}
 native_ast_caller_sample(4,0x1000);assert(ast_caller_bad_cpu==1);boot_ast_caller_report();
 memset(ast_caller_rows,0,sizeof(ast_caller_rows));memset(ast_caller_report_previous,0,sizeof(ast_caller_report_previous));
 for(unsigned i=0;i<64000;i++)native_ast_caller_sample(0,(i&1)?0x2000:0x3000);
 assert(ast_caller_rows[0].slots[0].pc && ast_caller_rows[0].slots[1].pc);assert(ast_caller_rows[0].slots[0].count>300 && ast_caller_rows[0].slots[1].count>300);
 boot_ast_caller_report();for(unsigned i=0;i<64000;i++)native_ast_caller_sample(0,0x4000);boot_ast_caller_report();
 ast_caller_rows[1].slots[1].count=5;boot_ast_caller_report();assert(ast_caller_report_previous[1][1]==0);ast_caller_rows[1].slots[1].pc=0x7770;boot_ast_caller_report();assert(ast_caller_report_previous[1][1]==5);
 for(unsigned i=0;i<40;i++)for(unsigned n=0;n<1024;n++)native_ast_caller_sample(0,0x5000+4*i);
 assert(ast_caller_rows[0].dropped>0);boot_ast_caller_report();boot_ast_caller_enable(0);uint64_t before=ast_caller_rows[0].seen;native_ast_caller_sample(0,0x1000);assert(ast_caller_rows[0].seen==before);
}
'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.c').write_text(code);subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-pthread','-fsanitize=address,undefined','-fno-pie','-no-pie',str(p/'test.c'),'-o',str(p/'test')],check=True);r=subprocess.check_output([str(p/'test')],text=True,timeout=30);assert 'unavailable: hook not observed' in r and 'cpu=0 pc=4000 samples_delta=' in r and 'omitted_delta=' in r and 'cpu=1 pc=7770 samples_delta=5' in r
print('PASS AST caller: off, single writers, randomized alternating callers, interval changes, bounds, full table, reporting')
