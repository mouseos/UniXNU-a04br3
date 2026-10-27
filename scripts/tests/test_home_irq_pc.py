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
struct proc { int pid; };typedef struct proc *proc_t;
static struct proc proc={1};static uint64_t test_uid=42;
static uint64_t proc_uniqueid(proc_t p){(void)p;return test_uid;}
static void *current_task(void){return &proc;}
static void *get_bsdtask_info(void *p){return p;}
int proc_getpid(proc_t p){return p->pid;}
int native_irq_pc_hook_compiled(void){return 1;}
static uint64_t mach_absolute_time(void){return 123;}
static void absolutetime_to_nanoseconds(uint64_t n,uint64_t *out){*out=n*2;}
static int sink(const char *s,...){(void)s;return 0;}
#define printf sink
''' + body + r'''
static void *worker(void *p){(void)p;for(unsigned i=0;i<16000;++i)native_irq_pc_sample(0x8001,0);return 0;}
int main(void){
 native_irq_pc_sample(0x8001,0);assert(home_irq_ticks==0);
 boot_home_irq_init(1);native_irq_pc_sample(0x8001,1);native_irq_pc_sample(0,0);
 proc.pid=2;native_irq_pc_sample(0x8001,0);assert(home_irq_ticks==0);
 proc.pid=1;
 pthread_t threads[4];for(unsigned i=0;i<4;++i)assert(!pthread_create(&threads[i],0,worker,0));
 for(unsigned i=0;i<4;++i)assert(!pthread_join(threads[i],0));
 assert(home_irq_ticks==64000 && home_irq_sampled==4000 && home_irq_overflow==0);
 unsigned used=0;for(unsigned i=0;i<64;++i)if(home_irq_slots[i].pc){++used;assert(home_irq_slots[i].pc==0x8000 && home_irq_slots[i].hits==4000);}
 assert(used==1 && home_irq_uniqueid==42);
 test_uid=43;
 for(unsigned i=0;i<16;++i)native_irq_pc_sample(0x8001,0);
 assert(home_irq_other_generation==1 && home_irq_sampled==4000);
 test_uid=42;
 for(unsigned i=0;i<64;++i)for(unsigned j=0;j<16;++j)native_irq_pc_sample(0x10000+(uint64_t)i*256,0);
 assert(home_irq_sampled==4064 && home_irq_overflow==1);
 boot_home_irq_report();boot_home_irq_init(0);uint64_t old=home_irq_ticks;
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
