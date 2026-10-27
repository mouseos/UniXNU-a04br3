"""Exercise the real bounded task sampler with lock-failure fixtures."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class HomePerfTest(unittest.TestCase):
    def test_display_buffer(self):
        source = (ROOT / 'package/LegacyDisplayProbe.cpp').read_text()
        body = source[source.index('struct HomeDisplayEvent {'):source.index('struct SwapEvent {')]
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
static unsigned reports, lines, dropped;
static void IOLog(const char *fmt, ...) { if (strstr(fmt,"count=")) ++reports; else ++lines; }
''' + body + r'''
int main() {
 homeDisplayRecord(1, 2); assert(homeDisplayCount==0);
 homeDisplayEnabled=1;
 for(unsigned i=0;i<35;++i)homeDisplayRecord(i%3, i);
 assert(homeDisplayCount==32 && homeDisplayDropped==3);
 homeDisplayLock=1;homeDisplayRecord(1,9);assert(homeDisplayDropped==4);
 homeDisplayReport();assert(homeDisplayCount==32);
 homeDisplayLock=0;homeDisplayReport();assert(homeDisplayCount==0 && homeDisplayDropped==0);
 assert(reports==1 && lines==32); (void)dropped;
}
'''
        with tempfile.TemporaryDirectory(prefix='home-display-') as tmp:
            src, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
            src.write_text(harness)
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            str(src), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_sampler_and_gate(self):
        board = (ROOT / 'package/A04BR3Board.cpp').read_text()
        self.assertIn('if (perf == 1)', board)
        self.assertIn('PE_parse_boot_argn("a04br3-home-perf"', board)
        source = (ROOT / 'package/BootProcessProbe.c').read_text()
        body = source[source.index('struct home_perf_row {'):]
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
struct fake_thread {struct fake_thread *next; unsigned state; uint64_t wait_event; int sched_lock;};
typedef struct fake_thread *thread_t;
struct fake_task {thread_t threads; unsigned thread_count; int locked;};
typedef struct fake_task *task_t;
struct proc {const char *name;int pid;task_t task;};typedef struct proc *proc_t;
#define PROC_RETURNED 0
#define PROC_ALLPROCLIST 1
#define PROC_NOWAITTRANS 4
#define TH_RUN 1
#define TH_WAIT 2
#define queue_iterate(head,var,type,field) for((var)=*(head);(var);(var)=(var)->next)
static int thread_lck_grp, interrupted;
static unsigned strlcpy(char *d,const char *s,unsigned n) {snprintf(d,n,"%s",s);return strlen(s);}
static const char *proc_best_name(proc_t p){return p->name;}
static int proc_pid(proc_t p){return p->pid;}
static task_t proc_task(proc_t p){return p->task;}
static uint64_t get_task_cpu_time(task_t t){(void)t;return 17;}
static uint64_t get_task_resident_size(task_t t){(void)t;return 21;}
static uint64_t get_task_compressed(task_t t){(void)t;return 22;}
static uint64_t get_task_phys_footprint(task_t t){(void)t;return 23;}
static int task_lock_try(task_t t){return !t->locked;}
static void task_unlock(task_t t){(void)t;}
static bool ml_set_interrupts_enabled(bool v){bool old=!interrupted;interrupted=!v;return old;}
static int simple_lock_try(int *l,int *g){(void)g;return !*l;}
static void thread_unlock(thread_t t){(void)t;}
static uint64_t mach_absolute_time(void){return 100;}
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t *v){*v=t*2;}
static struct proc procs[16];
static void proc_iterate(unsigned flags,int (*cb)(proc_t,void*),void *a,int (*filter)(proc_t,void*),void *b){
 assert(flags==5);for(unsigned i=0;i<16;++i)if(procs[i].name && filter(&procs[i],b))cb(&procs[i],a);
}
static int quiet_printf(const char *fmt,...){(void)fmt;return 0;}
#define printf quiet_printf
''' + body + r'''
int main(void){
 struct fake_thread th[40]={0};for(unsigned i=0;i<40;++i){th[i].state=TH_WAIT;th[i].wait_event=100+i;th[i].next=i<39?&th[i+1]:0;}
 th[0].sched_lock=1;th[1].state=TH_RUN;
 struct fake_task task={th,40,0};
 procs[0]=(struct proc){"SpringBoard",25,&task};
 procs[1]=(struct proc){"unselected",99,&task};
 boot_home_perf_sample();assert(home_perf_count==1);
 assert(home_perf_rows[0].pid==25 && home_perf_rows[0].threads==40);
 assert(home_perf_rows[0].busy==1 && home_perf_rows[0].running==1 && home_perf_rows[0].waiting==30);
 assert(home_perf_rows[0].sampled==1 && home_perf_rows[0].wait==101 && !interrupted);
 task.locked=1;boot_home_perf_sample();assert(home_perf_rows[0].busy==1 && !home_perf_rows[0].sampled);
 task.locked=0;for(unsigned i=0;i<16;++i)procs[i]=(struct proc){"logd",(int)i+1,&task};
 boot_home_perf_sample();assert(home_perf_count==10);
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='home-perf-') as tmp:
            src, binary = Path(tmp) / 'test.c', Path(tmp) / 'test'
            src.write_text(harness)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            str(src), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
