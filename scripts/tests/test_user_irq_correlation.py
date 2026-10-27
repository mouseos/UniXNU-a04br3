#!/usr/bin/env python3
"""Compile and exercise the actual IRQ observer and bounded storage on host."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'package/BootProcessProbe.c').read_text()
body=s[s.index('// Separate opt-in:'):s.index('// Default-off sampler,')]
head=r'''
#include <assert.h>
#include <stdint.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include "BootIrqCorrelation.h"
typedef struct {uint64_t pc; int valid;} arm_saved_state_t;
typedef struct {uint64_t thread_id; struct {arm_saved_state_t *upcb;} machine;} *thread_t;
struct proc {int pid; uint64_t uid; int version;};typedef struct proc *proc_t;
static struct proc proc={1,42,3};static arm_saved_state_t state={0x5501,1};
static struct {uint64_t thread_id;struct {arm_saved_state_t *upcb;} machine;} th={7,{&state}};
static void *current_task(void){return &proc;} static void *get_bsdtask_info(void *v){return v;}
static thread_t current_thread(void){return (thread_t)&th;}
int proc_getpid(proc_t p){return p->pid;} static uint64_t proc_uniqueid(proc_t p){return p->uid;}
int proc_pidversion(proc_t p){return p->version;}
static uint64_t time_value=123;static uint64_t mach_absolute_time(void){return time_value;}
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t *n){*n=t*2;}
static int sink(const char *s,...){(void)s;return 0;}
#define printf sink
'''
main=r'''
static void calls(uint64_t pc,unsigned user,unsigned n){for(unsigned i=0;i<n;++i)native_irq_correlation_sample(pc,user);}
static void *worker(void *p){(void)p;calls(0x8801,1,64000);return 0;}
static uint64_t retired;
static void *rotate(void *p){(void)p;for(unsigned n=0;n<2000;n++){
 struct uirq_record rows[UIRQ_SLOTS];uint64_t full,at;
 if(uirq_take(&uirq,rows,&full,&at,mach_absolute_time)){
  assert(full==0);for(unsigned j=0;j<UIRQ_SLOTS;j++)retired+=rows[j].hits;
 }
 }return 0;}
int main(void){
 calls(0x1234,1,64);assert(!uirq.ticks);boot_user_irq_init(1,1);
 proc.pid=2;calls(0x1234,1,64);assert(!uirq.ticks);proc.pid=1;
 calls(0x1234,1,64);
 th.machine.upcb=(arm_saved_state_t *)(uintptr_t)1; /* IRQ must never read it. */
 calls(0xffff0001,0,64);
 uint64_t hits=0;unsigned seen_user=0,seen_kernel=0;
 for(unsigned i=0;i<UIRQ_SLOTS;i++)if(uirq.slots[i].hits){
  struct uirq_record r=uirq.slots[i];hits+=r.hits;assert(r.tid==7);
  if(r.user){assert(r.pc==0x1200 && r.version==3);seen_user++;}
  else {assert(r.pc==0xffff0000 && r.version==3);seen_kernel++;}
 }
 assert(hits==2 && seen_user==1 && seen_kernel==1 && uirq.sampled==2);
 proc.version=4;calls(0x1234,1,64);unsigned versions=0;
 for(unsigned i=0;i<UIRQ_SLOTS;i++)if(uirq.slots[i].hits && uirq.slots[i].version==4)versions++;
 assert(versions==1);proc.version=3;
 proc.uid=43;calls(0x1234,1,64);assert(uirq.wrong_uid==1);proc.uid=42;
 uirq.lock=1;calls(0x1234,1,64);assert(uirq.busy==1);
 boot_user_irq_report();assert(uirq_epoch==0 && uirq_skipped==1);uirq.lock=0;
 boot_user_irq_report();assert(uirq_epoch==1 && uirq_skipped==0 && uirq_start==123);
 for(unsigned i=0;i<UIRQ_SLOTS;i++)calls(0x10000+(i<<8),1,64);
 calls(0x80000,1,64);assert(uirq.full==1);
 time_value=222;boot_user_irq_report();assert(uirq_epoch==2 && !uirq.full && uirq_start==222);
 pthread_t t[4];for(int i=0;i<4;i++)assert(!pthread_create(&t[i],0,worker,0));
 for(int i=0;i<4;i++)assert(!pthread_join(t[i],0));
 uint64_t total=0;for(unsigned i=0;i<UIRQ_SLOTS;i++)total+=uirq.slots[i].hits;
 assert(total+uirq.busy-1==4000);
 boot_user_irq_report();
 uint64_t start_samples=uirq.sampled,start_busy=uirq.busy;
 pthread_t report;assert(!pthread_create(&report,0,rotate,0));
 for(int i=0;i<4;i++)assert(!pthread_create(&t[i],0,worker,0));
 for(int i=0;i<4;i++)assert(!pthread_join(t[i],0));
 assert(!pthread_join(report,0));
 struct uirq_record final[UIRQ_SLOTS];uint64_t full,at;
 assert(uirq_take(&uirq,final,&full,&at,mach_absolute_time));assert(full==0);
 for(unsigned j=0;j<UIRQ_SLOTS;j++)retired+=final[j].hits;
 assert(uirq.sampled-start_samples==4000);
 assert(retired+uirq.busy-start_busy==4000);
 boot_user_irq_init(1,0);uint64_t old=uirq.ticks;calls(0x1234,1,64);assert(uirq.ticks==old);
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='user-irq-') as d:
 p=Path(d);(p/'test.c').write_text(head+body+main)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-pthread','-fsanitize=address,undefined','-fno-pie','-no-pie','-I',str(root/'package'),str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,timeout=15)
print('PASS IRQ user/kernel records, opt-in, UID, saturation, contention, epoch')
