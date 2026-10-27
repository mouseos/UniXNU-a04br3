"""Exercise the actual timer observer with bounded and concurrent fixtures."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

class IrqPcTest(unittest.TestCase):
    def test_observer(self):
        src = (ROOT / 'package/BootProcessProbe.c').read_text()
        body = src[src.index('// Optional timer-IRQ PC histogram.'):]
        body = body.replace('__attribute__((weak_import))', '')
        harness = r'''
#include <assert.h>
#include <stdint.h>
#include <pthread.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
struct proc { int pid; };typedef struct proc *proc_t;
static struct proc proc={1};static uint64_t test_uid=42;
static uint64_t proc_uniqueid(proc_t p){(void)p;return test_uid;}
static void *current_task(void){return &proc;}
static void *get_bsdtask_info(void *p){return p;}
int proc_getpid(proc_t p){return p->pid;}
int native_irq_pc_hook_compiled(void){return 1;}
static uint64_t test_clock=123;
static uint64_t mach_absolute_time(void){return __atomic_load_n(&test_clock,__ATOMIC_RELAXED);}
static void absolutetime_to_nanoseconds(uint64_t n,uint64_t *out){*out=n*2;}
static uint64_t reported_stored, last_epoch, last_start, last_end, last_full, last_skipped;
static int sink(const char *s,...){
 if(strstr(s,"irq pc pid=")){
  va_list ap;va_start(ap,s);(void)va_arg(ap,unsigned);(void)va_arg(ap,uint64_t);
  (void)va_arg(ap,unsigned);last_epoch=va_arg(ap,uint64_t);last_start=va_arg(ap,uint64_t);
  last_end=va_arg(ap,uint64_t);
  __atomic_fetch_add(&reported_stored,va_arg(ap,uint64_t),__ATOMIC_RELAXED);
  last_full=va_arg(ap,uint64_t);last_skipped=va_arg(ap,uint64_t);va_end(ap);
 }
 return 0;
}
static int hold_copy, copy_entered, release_copy;
static void *test_memcpy(void *dst,const void *src,size_t n){
 if(__atomic_load_n(&hold_copy,__ATOMIC_ACQUIRE)){
  __atomic_store_n(&copy_entered,1,__ATOMIC_RELEASE);
  while(!__atomic_load_n(&release_copy,__ATOMIC_ACQUIRE)) {}
 }
 return memcpy(dst,src,n);
}
#define memcpy test_memcpy
#define printf sink
''' + body + r'''
static void *worker(void *p){(void)p;for(unsigned i=0;i<16000;++i)native_irq_pc_sample(0x8001,0);return 0;}
static void *single_report(void *p){(void)p;boot_home_irq_report();return 0;}
static void *reporter(void *p){(void)p;for(unsigned i=0;i<2000;++i)boot_home_irq_report();return 0;}
int main(void){
 native_irq_pc_sample(0x8001,0);assert(home_irq_ticks==0);
 boot_home_irq_init(1);native_irq_pc_sample(0x8001,1);native_irq_pc_sample(0,0);
 proc.pid=2;native_irq_pc_sample(0x8001,0);assert(home_irq_ticks==0);
 proc.pid=1;
 pthread_t threads[4];for(unsigned i=0;i<4;++i)assert(!pthread_create(&threads[i],0,worker,0));
 for(unsigned i=0;i<4;++i)assert(!pthread_join(threads[i],0));
 assert(home_irq_ticks==64000 && home_irq_sampled==4000 && home_irq_overflow==0);
 unsigned used=0;for(unsigned i=0;i<64;++i)if(home_irq_slots[i].pc){++used;assert(home_irq_slots[i].pc==0x8000 && home_irq_slots[i].hits+home_irq_contended==4000);}
 assert(used==1 && home_irq_uniqueid==42);
 test_uid=43;
 for(unsigned i=0;i<16;++i)native_irq_pc_sample(0x8001,0);
 assert(home_irq_other_generation==1 && home_irq_sampled==4000);
 test_uid=42;
 for(unsigned i=0;i<64;++i)for(unsigned j=0;j<16;++j)native_irq_pc_sample(0x10000+(uint64_t)i*256,0);
 assert(home_irq_sampled==4064 && home_irq_overflow==1 && home_irq_epoch_overflow==1);
 boot_home_irq_report();assert(home_irq_epoch==1 && last_epoch==1 && last_start==0 && last_end==246 && last_full==1 && last_skipped==0);
 for(unsigned i=0;i<64;++i)assert(home_irq_slots[i].pc==0 && home_irq_slots[i].hits==0);
 uint64_t busy=home_irq_contended;home_irq_table_lock=1;
 for(unsigned i=0;i<16;++i)native_irq_pc_sample(0x33000,0);
 assert(home_irq_contended==busy+1);test_clock=150;boot_home_irq_report();assert(home_irq_epoch==1 && home_irq_report_skipped==1);
 home_irq_table_lock=0;
 for(unsigned i=0;i<16;++i)native_irq_pc_sample(0x33000,0);
 unsigned recovered=0;for(unsigned i=0;i<64;++i)if(home_irq_slots[i].pc==0x33000)recovered++;
 assert(recovered==1);test_clock=200;boot_home_irq_report();assert(home_irq_epoch==2 && home_irq_report_skipped==0 && home_irq_epoch_overflow==0 && last_epoch==2 && last_start==246 && last_end==400 && last_full==0 && last_skipped==1);
 /* Force an IRQ sample while report holds the actual table lock. */
 __atomic_store_n(&hold_copy,1,__ATOMIC_RELEASE);
 pthread_t held_report;assert(!pthread_create(&held_report,0,single_report,0));
 while(!__atomic_load_n(&copy_entered,__ATOMIC_ACQUIRE)) {}
 uint64_t forced_busy=__atomic_load_n(&home_irq_contended,__ATOMIC_RELAXED);
 for(unsigned i=0;i<16;++i)native_irq_pc_sample(0x8001,0);
 assert(__atomic_load_n(&home_irq_contended,__ATOMIC_RELAXED)==forced_busy+1);
 __atomic_store_n(&release_copy,1,__ATOMIC_RELEASE);
 assert(!pthread_join(held_report,0));__atomic_store_n(&hold_copy,0,__ATOMIC_RELEASE);
 uint64_t before_samples=home_irq_sampled,before_busy=home_irq_contended,before_reported=reported_stored;
 pthread_t reporter_thread;assert(!pthread_create(&reporter_thread,0,reporter,0));
 for(unsigned i=0;i<4;++i)assert(!pthread_create(&threads[i],0,worker,0));
 for(unsigned i=0;i<4;++i)assert(!pthread_join(threads[i],0));
 assert(!pthread_join(reporter_thread,0));boot_home_irq_report();
 assert(home_irq_sampled-before_samples==4000);
 assert(reported_stored-before_reported+home_irq_contended-before_busy==4000);
 boot_home_irq_init(0);uint64_t old=home_irq_ticks;
 native_irq_pc_sample(0x8000,0);assert(home_irq_ticks==old);
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='home-irq-pc-') as tmp:
            source, binary = Path(tmp)/'test.c', Path(tmp)/'test'
            source.write_text(harness)
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',
                            '-Wno-address','-pthread','-fsanitize=address,undefined',
                            '-fno-omit-frame-pointer',str(source),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)

if __name__ == '__main__':
    unittest.main()
