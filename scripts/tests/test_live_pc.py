#!/usr/bin/env python3
import pathlib, subprocess, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
class LivePcTest(unittest.TestCase):
 def test_ring_and_image_races(self):
  source=(ROOT/'package/BootProcessProbe.c').read_text()
  code=source[source.index('// Separate opt-in timer-interrupted samples;'):]
  code=code.replace('__atomic_thread_fence(__ATOMIC_ACQUIRE);\n   if(stamp', '__atomic_thread_fence(__ATOMIC_ACQUIRE);\n   test_interference();\n   if(stamp')
  irq=code[code.index('void native_live_pc_sample'):code.index('static void live_pc_image_report')]
  for forbidden in ('for(', 'while(', '__atomic_fetch_', '__atomic_exchange_', '__atomic_compare_exchange_', 'printf(', 'task_lock'):
   self.assertNotIn(forbidden,irq)
  prefix=r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <pthread.h>
#define KERN_SUCCESS 0
typedef struct process *proc_t;
typedef struct task *task_t;
typedef void *vm_map_t;
struct task {vm_map_t map; uint32_t task_shared_region_slide;};
struct process {int pid,version; uint64_t uid; struct task task;};
struct thread {uint64_t thread_id;};
static struct process process={1,7,42,{(void *)1,0x1000}};
static _Thread_local unsigned test_cpu;
static _Thread_local struct thread thread={99};
static uint64_t image_info_address, mutate_address, fail_address;
static int mutate_on_read,mutate_reads, refs, proc_refs, change_version_on_read, inject_torn;
static void test_interference(void);
static int proc_getpid(proc_t p){return p->pid;}
static uint64_t proc_uniqueid(proc_t p){return p->uid;}
int proc_pidversion(proc_t p){return p->version;}
static task_t proc_task(proc_t p){assert(proc_refs>0);return &p->task;}
proc_t proc_find(int pid){if(pid!=process.pid)return 0;proc_refs++;return &process;}
int proc_rele(proc_t p){(void)p;assert(proc_refs>0);proc_refs--;return 0;}
static task_t current_task(void){return &process.task;}
static struct thread *current_thread(void){return &thread;}
static void *get_bsdtask_info(task_t t){(void)t;return &process;}
static int cpu_number(void){return (int)test_cpu;}
static unsigned boot_home_irq_hook_available(void){return 1;}
static uint64_t mach_absolute_time(void){static uint64_t n;return __atomic_add_fetch(&n,1,__ATOMIC_RELAXED);}
static void absolutetime_to_nanoseconds(uint64_t x,uint64_t *out){*out=x;}
static int task_lock_try(task_t t){(void)t;assert(refs>0);return 1;}
static void task_unlock(task_t t){(void)t;}
static void task_reference(task_t t){(void)t;assert(proc_refs>0);refs++;}
static void task_deallocate(task_t t){(void)t;assert(refs>0);refs--;}
static uint64_t task_get_all_image_info_addr(task_t t){(void)t;return image_info_address;}
static void vm_map_reference(vm_map_t m){(void)m;}
static void vm_map_deallocate(vm_map_t m){(void)m;}
static int vm_map_read_user(vm_map_t m,uint64_t a,void *p,unsigned n){(void)m;if(refs)assert(!proc_refs);if(a==fail_address)return 1;memcpy(p,(void *)(uintptr_t)a,n);if(a==mutate_address && ++mutate_reads==mutate_on_read)((uint8_t *)p)[0]^=1;if(change_version_on_read){process.version++;change_version_on_read=0;}return 0;}
'''
  suffix=r'''
char bootsessionuuid_string[37]="test-boot";
static void test_interference(void){if(inject_torn){inject_torn=0;for(unsigned i=0;i<1100;i++)native_live_pc_sample(0x7000+i,(0x7000+i)^0x55aa,1);}}
static void *writer(void *arg){test_cpu=(unsigned)(uintptr_t)arg;thread.thread_id=100+test_cpu;for(unsigned i=0;i<100000;i++){uint64_t pc=0x10000000ULL+((uint64_t)test_cpu<<24)+i;native_live_pc_sample(pc,pc^0x55aa,1);}return 0;}
int main(void){
 boot_live_pc_init(0);native_live_pc_sample(1,2,1);assert(live_pc_cpus[0].ticks==0);
 boot_live_pc_init(1);native_live_pc_sample(0x1234,0x1234^0x55aa,1);assert(live_pc_cpus[0].written==1);
 test_cpu=9;native_live_pc_sample(1,2,1);assert(live_pc_bad_cpu==1);test_cpu=0;
 uint8_t macho[160]={0};struct live_macho_head *h=(void *)macho;h->magic=0xfeedfacf;h->ncmds=2;h->sizeofcmds=96;
 uint32_t *u=(void *)(macho+32);u[0]=0x1b;u[1]=24;memset(macho+40,0x5a,16);
 uint32_t *seg=(void *)(macho+56);seg[0]=0x19;seg[1]=72;memcpy(macho+64,"__TEXT",6);uint64_t vm=0x100000000;memcpy(macho+80,&vm,8);
 uint64_t checked=0;assert(live_image_entry_address(UINT64_MAX-10,1,&checked)!=0);assert(live_image_entry_address(UINT64_MAX-24,1,&checked)==0 && checked==UINT64_MAX);
 struct live_image_row row={0};live_image_decode((void *)1,(uintptr_t)macho,&row);assert(row.complete && row.uuid_valid && row.text_valid && row.text_vm==vm);
 struct live_image_info entry={(uintptr_t)macho,0,0};struct live_info_head info={15,1,(uintptr_t)&entry,0,0,1,{0},0};image_info_address=(uintptr_t)&info;
 live_pc_image_report(1,1011);assert(!refs && !proc_refs);
 mutate_address=(uintptr_t)&entry;mutate_on_read=2;mutate_reads=0;live_pc_image_report(1,1012);assert(!refs && !proc_refs);
 mutate_address=0;fail_address=(uintptr_t)(macho+56);live_pc_image_report(1,1013);fail_address=0;
 change_version_on_read=1;live_pc_image_report(1,1014);assert(!refs && !proc_refs);process.version=7;
 fail_address=(uintptr_t)&info;live_pc_image_report(1,1015);fail_address=0;
 h->ncmds=3;memset(&row,0,sizeof(row));live_image_decode((void *)1,(uintptr_t)macho,&row);assert(row.valid && !row.complete);h->ncmds=2;
 for(unsigned i=0;i<2000;i++)native_live_pc_sample(0x9000+i,(0x9000+i)^0x55aa,1);inject_torn=1;boot_live_pc_report();
 pthread_t a,b;pthread_create(&a,0,writer,(void *)0);pthread_create(&b,0,writer,(void *)1);
 for(unsigned i=0;i<15;i++)boot_live_pc_report();
 pthread_join(a,0);pthread_join(b,0);boot_live_pc_report();
 assert(!refs && !proc_refs);return 0;
}
'''
  with tempfile.TemporaryDirectory() as tmp:
   c=pathlib.Path(tmp)/'test.c';b=pathlib.Path(tmp)/'test';c.write_text(prefix+code+suffix)
   subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unknown-attributes','-pthread',str(c),'-o',str(b)],check=True)
   output=subprocess.run([str(b)],check=True,capture_output=True,text=True).stdout
  lines=output.splitlines()
  for epoch,status in [(1011,'complete'),(1012,'changed'),(1013,'partial'),(1014,'changed'),(1015,'failed')]:
   row=next(x for x in lines if f'images epoch={epoch} ' in x)
   self.assertIn('status='+status,row)
   self.assertIn('exec_version=',row);self.assertIn('dyld_version=',row)
   self.assertIn('snapshot=nonatomic',row)
  self.assertFalse(any('livepc image epoch=1012 ' in x or 'livepc image epoch=1014 ' in x for x in lines))
  partial=next(x for x in lines if 'livepc image epoch=1013 ' in x)
  self.assertIn('detail_complete=0',partial)
  self.assertIn('text_valid=0',partial)
  self.assertIn('uuid_valid=0',partial)
  for row in (x for x in lines if 'livepc sample ' in x):
   fields=dict(x.split('=',1) for x in row.split() if '=' in x)
   self.assertEqual(int(fields['lr'],16),int(fields['pc'],16)^0x55aa)
  self.assertTrue(any(' lost=' in x and int(x.split(' lost=')[1].split()[0])>0 for x in lines))
  self.assertTrue(any(' torn=' in x and int(x.split(' torn=')[1].split()[0])>0 for x in lines))
if __name__=='__main__':unittest.main()
