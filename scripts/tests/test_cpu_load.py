#!/usr/bin/env python3
"""Exercise the real bounded reporter against a simulated processor list."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='a04-cpuload-') as tmp:
    d = Path(tmp)
    for folder in ('kern', 'mach', 'sys'):
        (d / folder).mkdir()
    (d/'kern/processor.h').write_text(r'''
#include <stdint.h>
typedef unsigned natural_t;
typedef unsigned processor_state_t;
typedef struct processor { struct processor *processor_list; int cpu_id; processor_state_t state; } *processor_t;
extern processor_t processor_list; extern int processor_list_lock;
#define LCK_GRP_NULL 0
void simple_lock(int *, int); void simple_unlock(int *);
void processor_cpu_load_info(processor_t, natural_t *);
''')
    (d/'kern/clock.h').write_text('#include <stdint.h>\nvoid absolutetime_to_nanoseconds(uint64_t, uint64_t *);\n')
    (d/'mach/mach_time.h').write_text('#include <stdint.h>\nuint64_t mach_absolute_time(void);\n')
    (d/'mach/processor_info.h').write_text('#define CPU_STATE_USER 0\n#define CPU_STATE_SYSTEM 1\n#define CPU_STATE_IDLE 2\n#define CPU_STATE_NICE 3\n#define CPU_STATE_MAX 4\n')
    (d/'sys/systm.h').write_text('#include <stdio.h>\n')
    harness = d/'test.c'
    harness.write_text(r'''
#include <assert.h>
#include "kern/processor.h"
processor_t processor_list; int processor_list_lock, locked, calls; uint64_t now;
void simple_lock(int *p,int g) {(void)p;(void)g;assert(!locked);locked=1;}
void simple_unlock(int *p) {(void)p;assert(locked);locked=0;}
void processor_cpu_load_info(processor_t p,natural_t *t) {
 assert(!locked);++calls;t[0]=p->cpu_id+1;t[1]=10;t[2]=20;assert(t[3]==0);if(p->cpu_id==1)p->state=3;
}
uint64_t mach_absolute_time(void) {assert(!locked);return ++now;}
void absolutetime_to_nanoseconds(uint64_t t,uint64_t *n) {*n=t;}
void a04br3_cpu_load_report(void);
int main(void) {struct processor p[5];for(int i=0;i<5;i++){p[i].cpu_id=i;p[i].state=i==0?0:6;p[i].processor_list=i<4?&p[i+1]:0;}
 processor_list=p;a04br3_cpu_load_report();assert(calls==4 && !locked);
 processor_list=0;calls=0;a04br3_cpu_load_report();assert(!calls);}
''')
    exe = d/'test'
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I',str(d),
                    str(harness),str(root/'package/A04BR3CpuLoad.c'),'-o',str(exe)],check=True)
    out = subprocess.check_output([str(exe)],text=True)
    assert out.count(' ticks32=1 non_atomic=1') == 4
    assert 'cpu=0 at_ns=2 user=1 system=10 idle=20 nice=0 state_before=0 state_after=0' in out
    assert 'cpu=1 at_ns=3 user=2 system=10 idle=20 nice=0 state_before=6 state_after=3' in out
    assert 'cpus=4 extra=1 non_atomic=1' in out and 'cpus=0 extra=0 non_atomic=1' in out
    board = (root/'package/A04BR3Board.cpp').read_text()
    assert 'PE_parse_boot_argn("a04br3-cpu-load", &enabled, sizeof(enabled))' in board
    assert 'PE_parse_boot_argn("native-msdc-iostat", &ioStats, sizeof(ioStats))' in board
    assert 'if (enabled != 1 && ioStats != 1) return;' in board
    assert 'IOSleep(10000); a04br3_cpu_load_report();' in board
print('PASS: bounded core list, unlocked sampling, tick fields, empty list and opt-in wiring')
