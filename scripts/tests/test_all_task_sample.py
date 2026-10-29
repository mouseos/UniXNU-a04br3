#!/usr/bin/env python3
"""Exercise the real bounded all-task capture with host process stubs."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
s = (root / 'package/BootProcessProbe.c').read_text()
body = s[s.index('#define ALL_TASK_MAX'):s.index('// Optional timer-IRQ PC histogram.')]
pre = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
struct task { int n; }; typedef struct task *task_t;
struct proc { int pid; const char *name; task_t task; }; typedef struct proc *proc_t;
#define PROC_RETURNED 0
#define PROC_ALLPROCLIST 1
#define PROC_NOWAITTRANS 4
static struct proc processes[260]; static unsigned nproc;
static int proc_pid(proc_t p) { return p->pid; }
static uint64_t proc_uniqueid(proc_t p) { return 10000 + p->pid; }
static const char *proc_best_name(proc_t p) { return p->name; }
static task_t proc_task(proc_t p) { return p->task; }
#ifdef strlcpy
#undef strlcpy
#endif
#define strlcpy test_strlcpy
static unsigned test_strlcpy(char *d,const char *s,unsigned n) { snprintf(d,n,"%s",s); return strlen(s); }
static uint64_t get_task_cpu_time(task_t t) { return 100 + t->n; }
static uint64_t get_task_resident_size(task_t t) { return 200 + t->n; }
static uint64_t get_task_compressed(task_t t) { return 300 + t->n; }
static uint64_t get_task_phys_footprint(task_t t) { return 400 + t->n; }
static uint64_t tick=0;
static uint64_t mach_absolute_time(void) { return ++tick; }
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t *out) { *out=t*10; }
static int iterating;
static void proc_iterate(unsigned flags,int (*cb)(proc_t,void*),void *a,int (*filter)(proc_t,void*),void *b) {
 assert(flags==5 && !filter && !b); iterating=1;
 for(unsigned i=0;i<nproc;i++) assert(cb(&processes[i],a)==PROC_RETURNED);
 iterating=0;
}
static char output[100000]; static unsigned used;
static int capture_printf(const char *fmt,...) {
 assert(!iterating); va_list ap; va_start(ap,fmt);
 int n=vsnprintf(output+used,sizeof(output)-used,fmt,ap);va_end(ap);
 assert(n>=0 && (unsigned)n<sizeof(output)-used);used+=(unsigned)n;return n;
}
#define printf capture_printf
'''
post = r'''
int main(void) {
 struct task t={7};
 for(unsigned i=0;i<260;i++) processes[i]=(struct proc){(int)i,"unselected_worker",&t};
 processes[1].task=0;nproc=260;boot_all_task_sample();
 assert(all_task_count==256 && all_task_overflow==4);
 assert(strstr(output,"epoch=1 start_ns=10") && strstr(output,"rows=256 overflow=4 snapshot=nonatomic"));
 assert(strstr(output,"pid=0 uid=10000 name=unselected_worker available=1 cpu_ns=1070 cpu_at_ns=20 resident=207 compressed=307 footprint=407"));
 assert(strstr(output,"pid=1 uid=10001 name=unselected_worker available=0 cpu_ns=0 cpu_at_ns=0"));
 used=0;output[0]=0;nproc=1;processes[0].pid=42;boot_all_task_sample();
 assert(all_task_count==1 && all_task_overflow==0 && all_task_epoch==2);
 assert(strstr(output,"pid=42 uid=10042") && strstr(output,"rows=1 overflow=0"));
}
'''
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / 'sample.c'
    exe = Path(d) / 'sample'
    p.write_text(pre + body + post)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(p), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS: bounded all-task sampling, overflow, unavailable task and epoch reset')

board = (root / 'package/A04BR3Board.cpp').read_text()
worker = board[board.index('void allTaskThread('):board.index('\nvoid procFlush(')]
heartbeat = board[board.index('void procLogThread('):board.index('\nstatic void a04br3_proclog_init')]
assert 'boot_all_task_sample' not in heartbeat
worker_pre = r'''
#include <cassert>
using wait_result_t=int;
unsigned gAllTaskSeconds=60, sleeps=0, samples=0;
unsigned expected_ms=60000;
void IOSleep(unsigned ms) { assert(ms==expected_ms); if (++sleeps==3) throw 1; }
void boot_all_task_sample() { assert(sleeps==samples+1); ++samples; }
'''
worker_post = r'''
int main() {
 try { allTaskThread(nullptr,0); } catch(int) {} assert(samples==2 && sleeps==3);
 gAllTaskSeconds=~0u/1000u; expected_ms=(~0u/1000u)*1000u; sleeps=samples=0;
 try { allTaskThread(nullptr,0); } catch(int) {} assert(samples==2 && sleeps==3);
}
'''
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / 'worker.cpp'
    exe = Path(d) / 'worker'
    p.write_text(worker_pre + worker + worker_post)
    subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', str(p), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS: independent worker sleeps before each sample; heartbeat has no synchronous call')
