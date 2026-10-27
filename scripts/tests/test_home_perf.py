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
#include <cstdarg>
static unsigned reports, lines, gotStage[64], gotDropped;
static uint64_t gotTime[64];
static void IOLog(const char *fmt, ...) {
 va_list ap; va_start(ap,fmt);
 if (strstr(fmt,"count=")) { (void)va_arg(ap,unsigned);gotDropped=va_arg(ap,unsigned);++reports; }
 else {gotStage[lines]=va_arg(ap,unsigned);gotTime[lines]=va_arg(ap,unsigned long long);++lines;}
 va_end(ap);
}
''' + body + r'''
int main() {
 homeDisplayRecord(1, 2); assert(homeDisplayCount==0);
 homeDisplayEnabled=1;
 for(unsigned i=0;i<35;++i)homeDisplayRecord(i%3, i);
 assert(homeDisplayCount==32 && homeDisplayDropped==3);
 assert(homeDisplayEvents[homeDisplayHead].us==3);
 homeDisplayLock=1;homeDisplayRecord(1,9);assert(homeDisplayDropped==4);
 homeDisplayReport();assert(homeDisplayCount==32);
 homeDisplayLock=0;homeDisplayReport();assert(homeDisplayCount==0 && homeDisplayDropped==0);
 assert(reports==1 && lines==32 && gotDropped==4);
 for(unsigned i=0;i<32;++i){assert(gotTime[i]==i+3 && gotStage[i]==(i+3)%3);}
 homeDisplayRecord(2,42);homeDisplayReport();
 assert(reports==2 && lines==33 && gotTime[32]==42 && gotStage[32]==2 && gotDropped==0);
}
'''
        with tempfile.TemporaryDirectory(prefix='home-display-') as tmp:
            src, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
            src.write_text(harness)
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            str(src), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_input_transfer_error_releases_once(self):
        source = (ROOT / 'package/A04BR3Input.cpp').read_text()
        start = source.index('    if (e) {', source.index('void touchThread('))
        end = source.index('    failures = 0;', start)
        body = source[start:end]
        harness = r'''
#include <cassert>
#include <cstdint>
static unsigned publishes, reports, sleeps;
static uint32_t native_touch_seq=8, native_touch_hid_reports=9;
static uint64_t ticks=10;
static uint64_t mach_absolute_time(){return ticks++;}
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t *out){*out=t;}
static void publish(bool state,unsigned,unsigned){assert(!state);++publishes;}
static void IOSleep(unsigned n){assert(n==100);++sleeps;}
static void IOLog(const char *,...){++reports;}
static void run(bool &down,unsigned &perfEdges) {
 unsigned lastx=0,lasty=0,failures=0,perf=1;int e=5;
 for(unsigned once=0;once<1;++once) {
''' + body + r'''
 }
}
int main(){bool down=true;unsigned edges=0;run(down,edges);
 assert(!down && publishes==1 && edges==1 && reports==2 && sleeps==1);
 run(down,edges);assert(publishes==1 && edges==1);
 down=true;edges=32;unsigned old=reports;run(down,edges);
 assert(!down && publishes==2 && edges==32 && reports==old+1);
}
'''
        with tempfile.TemporaryDirectory(prefix='home-input-') as tmp:
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
        body = source[source.index('#if PRECISE_USER_KERNEL_TIME'):]
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
typedef struct fake_saved {uint64_t pc,lr;} arm_saved_state_t;
typedef struct fake_kernel {struct {struct {uint64_t fp,lr;} ss;} machine;} *thread_kernel_state_t;
struct fake_thread {struct fake_thread *next; unsigned state; uint64_t wait_event; int sched_lock; uint64_t kernel_stack,thread_id,continuation; struct {arm_saved_state_t *upcb;} machine;};
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
static int thread_lck_grp, interrupted, task_acquires, task_releases, sched_acquires, sched_releases;
static uint64_t kernel_stack_size=4096;
static struct fake_kernel fake_ks;
static thread_kernel_state_t thread_get_kernel_state(thread_t t){(void)t;return &fake_ks;}
static int is_saved_state64(arm_saved_state_t *s){return s!=0;}
static uint64_t get_saved_state_pc(arm_saved_state_t *s){return s->pc;}
static uint64_t get_saved_state_lr(arm_saved_state_t *s){return s->lr;}
struct recount_times_mach {uint64_t rtm_user,rtm_system;};
static struct recount_times_mach recount_task_times(task_t t){(void)t;return (struct recount_times_mach){3,4};}
static struct recount_times_mach recount_thread_times(thread_t t){(void)t;return (struct recount_times_mach){5,6};}
static unsigned strlcpy(char *d,const char *s,unsigned n) {snprintf(d,n,"%s",s);return strlen(s);}
static const char *proc_best_name(proc_t p){return p->name;}
static int proc_pid(proc_t p){return p->pid;}
static unsigned uid_generation=1;
static uint64_t proc_uniqueid(proc_t p){return 1000u*uid_generation+(unsigned)p->pid;}
static task_t proc_task(proc_t p){return p->task;}
static uint64_t get_task_cpu_time(task_t t){(void)t;return 17;}
static uint64_t get_task_resident_size(task_t t){(void)t;return 21;}
static uint64_t get_task_compressed(task_t t){(void)t;return 22;}
static uint64_t get_task_phys_footprint(task_t t){(void)t;return 23;}
static int task_lock_try(task_t t){if(t->locked)return 0;t->locked=1;++task_acquires;return 1;}
static void task_unlock(task_t t){assert(t->locked);t->locked=0;++task_releases;}
static bool ml_set_interrupts_enabled(bool v){bool old=!interrupted;interrupted=!v;return old;}
static int simple_lock_try(int *l,int *g){(void)g;if(*l)return 0;*l=1;++sched_acquires;return 1;}
static void thread_unlock(thread_t t){assert(t->sched_lock);t->sched_lock=0;++sched_releases;}
static uint64_t clock_value=99;
static uint64_t mach_absolute_time(void){return ++clock_value;}
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t *v){*v=t*2;}
static struct proc procs[16];
static void proc_iterate(unsigned flags,int (*cb)(proc_t,void*),void *a,int (*filter)(proc_t,void*),void *b){
 assert(flags==5);for(unsigned i=0;i<16;++i)if(procs[i].name && filter(&procs[i],b))cb(&procs[i],a);
}
static char output[8192];static unsigned output_used;
static int quiet_printf(const char *fmt,...){va_list ap;va_start(ap,fmt);int n=vsnprintf(output+output_used,sizeof(output)-output_used,fmt,ap);va_end(ap);if(n>0)output_used+=(unsigned)n;assert(output_used<sizeof(output));return n;}
#define printf quiet_printf
''' + body + r'''
int main(void){
 struct fake_thread th[40]={0};for(unsigned i=0;i<40;++i){th[i].state=TH_WAIT;th[i].wait_event=100+i;th[i].next=i<39?&th[i+1]:0;}
 th[0].sched_lock=1;th[1].state=TH_RUN;
 struct fake_task task={th,40,0};
 procs[0]=(struct proc){"SpringBoard",25,&task};
 procs[1]=(struct proc){"backboardd",56,&task};
 boot_home_perf_sample();assert(home_perf_count==2);
 assert(home_perf_rows[0].pid==25 && home_perf_rows[0].threads==40);
 assert(home_perf_rows[0].busy==1 && home_perf_rows[0].running==1 && home_perf_rows[0].waiting==30);
 assert(home_perf_rows[0].sampled==1 && home_perf_rows[0].wait==101 && !interrupted);
 assert(strstr(output,"p25 SpringBoard uid=1025 cpu_ns=34 cpu_at_ns=200"));
 assert(strstr(output,"p56 backboardd uid=1056 cpu_ns=34 cpu_at_ns=202"));
 assert(task_acquires==task_releases && sched_acquires==sched_releases);
 uid_generation=2;output_used=0;output[0]=0;boot_home_perf_sample();
 assert(strstr(output,"p25 SpringBoard uid=2025"));
 uid_generation=1;
 struct proc changed={"unselected_worker",25,&task};unsigned saved=home_perf_count;
 assert(home_perf_capture(&changed,NULL)==PROC_RETURNED && home_perf_count==saved);
 task.locked=1;boot_home_perf_sample();assert(home_perf_rows[0].busy==1 && !home_perf_rows[0].sampled);
 task.locked=0;
 procs[0]=(struct proc){"launchd",1,&task};procs[1].name=0;
 arm_saved_state_t savedpc={.pc=0x1234,.lr=0x5678};
 for(unsigned i=0;i<40;++i){th[i].thread_id=i+1;th[i].machine.upcb=&savedpc;}
 output_used=0;output[0]=0;boot_home_perf_sample();
 assert(home_perf_count==1 && home_perf_rows[0].detail_count==8);
 assert(strstr(output,"split=0 user_ns=6 system_ns=8"));
 assert(strstr(output,"pc=1234 lr=5678"));
 for(unsigned i=0;i<16;++i)procs[i]=(struct proc){"logd",(int)i+1,&task};
 boot_home_perf_sample();assert(home_perf_count==10);
 assert(task_acquires==task_releases && sched_acquires==sched_releases);
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
