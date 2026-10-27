#!/usr/bin/env python3
"""Exercise the actual IPI recorder and reporter with one writer per CPU row."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'package/BootProcessProbe.c').read_text()
start = src.index('static unsigned ipi_reason_enabled;')
end = src.index('\n}', src.index('void boot_ipi_reason_report(void)', start)) + 2
head = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <pthread.h>
static char output[8192];
static size_t used;
static int capture(const char *fmt, ...) {
 va_list ap; va_start(ap,fmt);
 int n=vsnprintf(output+used,sizeof(output)-used,fmt,ap);
 va_end(ap); assert(n>=0 && (size_t)n<sizeof(output)-used);used+=(size_t)n;return n;
}
#define printf capture
'''
main = r'''
static void report(void){used=0;output[0]=0;boot_ipi_reason_report();}
static void reset(void){
 boot_ipi_reason_enable(0);
 memset(ipi_reason_rows,0,sizeof(ipi_reason_rows));
 __atomic_store_n(&ipi_reason_out_of_range_seen,0,__ATOMIC_RELAXED);
}
static unsigned long long dest[4][4], kinds[4][7];
static void parse(void){
 const char *s=output;
 for(unsigned i=0;i<4;i++){
  unsigned src=99;int n=0;
  int fields=sscanf(s,"a04br3 ipi reason src=%u dest=%llu/%llu/%llu/%llu nop=%llu dec=%llu xcall=%llu ast=%llu imm=%llu other=%llu defer=%llu (non-atomic)\n%n",
   &src,&dest[i][0],&dest[i][1],&dest[i][2],&dest[i][3],
   &kinds[i][0],&kinds[i][1],&kinds[i][2],&kinds[i][3],&kinds[i][4],&kinds[i][5],&kinds[i][6],&n);
  assert(fields==12 && src==i && n>0);s+=n;
 }
 assert(strstr(s,"a04br3 ipi reason out_of_range_seen=")==s);
}
static unsigned started, stop;
static void *writer(void *arg){
 unsigned cpu=(unsigned)(uintptr_t)arg;
 native_ipi_signal_record(cpu,(cpu+1)%4,8,1);
 __atomic_fetch_add(&started,1,__ATOMIC_RELEASE);
 while(!__atomic_load_n(&stop,__ATOMIC_ACQUIRE)) {
  native_ipi_signal_record(cpu,(cpu+1)%4,8,1);
 }
 return NULL;
}
int main(void){
 assert(sizeof(struct ipi_reason_row)%64==0);
 report();assert(!*output);
 native_ipi_signal_record(0,1,8,0);assert(!ipi_reason_rows[0].counts[1][3]);
 boot_ipi_reason_enable(1);report();
 assert(strcmp(output,"a04br3 ipi reason unavailable: hook not observed (not built or no dispatch yet)\n")==0);
 native_ipi_signal_record(0,1,8,0);native_ipi_signal_record(0,1,0,1);
 native_ipi_signal_record(2,3,4,0);native_ipi_signal_record(3,0,0x200,0);native_ipi_signal_record(2,2,0x800,0);
 native_ipi_signal_record(4,0,4,0);native_ipi_signal_record(0,5,4,0);
 report();assert(!strstr(output,"unavailable"));parse();
 assert(dest[0][1]==2 && kinds[0][0]==1 && kinds[0][3]==1 && kinds[0][6]==1);
 assert(dest[2][3]==1 && dest[2][2]==1 && kinds[2][2]==1 && kinds[2][5]==1);
 assert(dest[3][0]==1 && kinds[3][4]==1 && strstr(output,"out_of_range_seen=1"));
 char previous[sizeof(output)];strcpy(previous,output);report();assert(!strcmp(previous,output));
 boot_ipi_reason_enable(0);report();assert(!*output);
 native_ipi_signal_record(0,1,8,0);boot_ipi_reason_enable(1);report();assert(!strcmp(previous,output));
 reset();boot_ipi_reason_enable(1);native_ipi_signal_record(4,0,4,0);report();
 assert(!strstr(output,"unavailable"));parse();assert(strstr(output,"out_of_range_seen=1"));
 for(unsigned i=0;i<4;i++)for(unsigned j=0;j<7;j++)assert(kinds[i][j]==0);
 reset();boot_ipi_reason_enable(1);
 pthread_t t[4];for(unsigned i=0;i<4;i++)assert(!pthread_create(&t[i],NULL,writer,(void *)(uintptr_t)i));
 while(__atomic_load_n(&started,__ATOMIC_ACQUIRE)!=4){}
 unsigned long long last[4]={0};
 for(unsigned iter=0;iter<3000;iter++){
  report();
  if(strstr(output,"unavailable")) {assert(!strstr(output,"src="));continue;}
  parse();
  for(unsigned i=0;i<4;i++){assert(kinds[i][3]>=last[i]);last[i]=kinds[i][3];}
 }
 __atomic_store_n(&stop,1,__ATOMIC_RELEASE);
 for(unsigned i=0;i<4;i++)assert(!pthread_join(t[i],NULL));
 report();parse();
 for(unsigned i=0;i<4;i++){
  uint64_t actual=__atomic_load_n(&ipi_reason_rows[i].counts[(i+1)%4][3],__ATOMIC_RELAXED);
  assert(actual>0 && kinds[i][3]==actual && kinds[i][6]==actual && dest[i][(i+1)%4]==actual && actual>=last[i]);
 }
 strcpy(previous,output);report();assert(!strcmp(previous,output));
}
'''
with tempfile.TemporaryDirectory(prefix='ipi-reporter-') as d:
    p = Path(d)
    (p / 'test.c').write_text(head + src[start:end] + main)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread',
                    '-fsanitize=address,undefined', '-fno-pie', '-no-pie',
                    str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True, timeout=30)
print('PASS: actual IPI reporter branches, cumulative preservation, concurrent single-CPU writers; ASan/UBSan')
