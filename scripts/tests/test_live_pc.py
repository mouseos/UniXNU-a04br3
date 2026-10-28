#!/usr/bin/env python3
import hashlib, os, pathlib, subprocess, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
class LivePcTest(unittest.TestCase):
 def test_pinned_xnu_parser_boundaries(self):
  path=os.environ.get('XNU_BOOTARGS_SOURCE')
  if not path: self.skipTest('set XNU_BOOTARGS_SOURCE to the pinned XNU bootargs.c')
  raw=pathlib.Path(path).read_bytes()
  self.assertEqual(hashlib.sha256(raw).hexdigest(),'814d2f779300bfbf7ef13c8c830ae0f98ada271586518b808d43a019a7e7db3a')
  source=raw.decode()
  core=source[source.index('static boolean_t\nPE_parse_boot_argn_internal('):source.index('\nboolean_t\nPE_parse_boot_argn(',source.index('static boolean_t\nPE_parse_boot_argn_internal('))]
  start=source.index('static boolean_t\nisargsep(char c)\n{')
  helpers=source[start:source.index('\nboolean_t\nPE_imgsrc_mount_supported()',start)]
  prefix=r'''
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
#include "LivePcPid.h"
typedef int boolean_t;
typedef boolean_t (*argsep_func_t)(char);
#define TRUE 1
#define FALSE 0
#define NUM 0
#define STR 1
struct i24 { int32_t i24:24; int32_t pad:8; };
static boolean_t isargsep(char);
static int argstrcpy(char *,char *);
static int argstrcpy2(char *,char *,unsigned);
static int argnumcpy(long long,void *,unsigned);
static int getval(char *,long long *,argsep_func_t,boolean_t);
'''
  suffix=r'''
int main() {
 const char *key="a04br3-live-pc-pid"; uint32_t old=0,pid=0;
 char overflow[]="a04br3-live-pc-pid=4294967352";
 assert(PE_parse_boot_argn_internal(overflow,key,&old,4,FALSE) && old==56);
 assert(live_pc_pid_arg(overflow,&pid) && pid==0);
 char text[]="a04br3-live-pc-pid=abcd"; old=0;
 assert(PE_parse_boot_argn_internal(text,key,&old,4,FALSE) && old!=0);
 assert(live_pc_pid_arg(text,&pid) && pid==0);
 char hex[]="a04br3-live-pc-pid=0x38"; old=0;
 assert(PE_parse_boot_argn_internal(hex,key,&old,4,FALSE) && old==56);
 assert(live_pc_pid_arg(hex,&pid) && pid==0);
 char octal[]="a04br3-live-pc-pid=070"; old=0;
 assert(PE_parse_boot_argn_internal(octal,key,&old,4,FALSE) && old==56);
 assert(live_pc_pid_arg(octal,&pid) && pid==70);
 char bare[]="a04br3-live-pc-pid"; old=0;
 assert(PE_parse_boot_argn_internal(bare,key,&old,4,FALSE) && old==1);
 assert(live_pc_pid_arg(bare,&pid) && pid==0);
 char max[]="a04br3-live-pc-pid=2147483647";
 assert(live_pc_pid_arg(max,&pid) && pid==2147483647u);
}
'''
  with tempfile.TemporaryDirectory() as tmp:
   src=pathlib.Path(tmp)/'xnu.c';exe=pathlib.Path(tmp)/'xnu'
   src.write_text(prefix+core+'\n'+helpers+'\n'+suffix)
   subprocess.run(['cc','-std=c11','-I',str(ROOT/'package'),str(src),'-o',str(exe)],check=True)
   subprocess.run([str(exe)],check=True)

 def test_runtime_pid_selection(self):
  source=(ROOT/'package/A04BR3Board.cpp').read_text()
  selection=source[source.index('  unsigned livePc = 0;'):source.index('  unsigned ipiReasonPerf = 0;')]
  prefix=r'''
#include <cstdint>
#include <cstring>
#include <cassert>
#include "LivePcPid.h"
static unsigned old_value, result;
static bool old_present, gLivePcAuto;
static const char *arguments;
static const char *PE_boot_args() { return arguments; }
static bool PE_parse_boot_argn(const char *name, void *out, unsigned size) {
 assert(size==sizeof(unsigned));
 assert(!strcmp(name,"a04br3-live-pc"));
 if (!old_present) return false;
 *(unsigned *)out=old_value; return true;
}
static void boot_live_pc_init(unsigned pid) { result=pid; }
static void boot_live_pc_auto_init() { result=999; }
static void select_pid() {
'''
  suffix=r'''
}
int main() {
 arguments="";old_present=false;select_pid();assert(result==0);
 old_present=true;old_value=1;select_pid();assert(result==1);
 old_value=56;select_pid();assert(result==0);
 old_value=1;
 const char *valid[]={"a04br3-live-pc-pid=56", "\tother=7 a04br3-live-pc-pid=00056\tend=1"};
 for(auto arg:valid){arguments=arg;select_pid();assert(result==56);}
 arguments="a04br3-live-pc-pid=2147483647";select_pid();assert(result==2147483647u);
 const char *invalid[]={"a04br3-live-pc-pid=0", "a04br3-live-pc-pid=2147483648", "a04br3-live-pc-pid=4294967352", "a04br3-live-pc-pid=999999999999999999999999999999", "a04br3-live-pc-pid=56x", "a04br3-live-pc-pid=0x38", "a04br3-live-pc-pid=+56", "a04br3-live-pc-pid=-56", "a04br3-live-pc-pid=", "a04br3-live-pc-pid", "a04br3-live-pc-pid =56", "a04br3-live-pc-pid=bad a04br3-live-pc-pid=56"};
 for(auto arg:invalid){arguments=arg;select_pid();assert(result==0);}
 arguments="a04br3-live-pc-pid-extra=56";select_pid();assert(result==1);
 arguments="a04br3-live-pc-pid=56 a04br3-live-pc-pid=57";select_pid();assert(result==56);
 arguments="a04br3-live-pc-backboard=1";select_pid();assert(result==999 && gLivePcAuto);
 arguments="a04br3-live-pc-backboard=4294967297 a04br3-live-pc-pid=56";select_pid();assert(result==0 && !gLivePcAuto);
 arguments="a04br3-live-pc-backboard=0 a04br3-live-pc-pid=56";select_pid();assert(result==0 && !gLivePcAuto);
}
'''
  with tempfile.TemporaryDirectory() as tmp:
   src=pathlib.Path(tmp)/'select.cpp'; exe=pathlib.Path(tmp)/'select'
   src.write_text(prefix+selection+suffix)
   subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(ROOT/'package'),str(src),'-o',str(exe)],check=True)
   subprocess.run([str(exe)],check=True)

 def test_ring_and_image_races(self):
  source=(ROOT/'package/BootProcessProbe.c').read_text()
  code=source[source.index('// Separate opt-in timer-interrupted samples;'):]
  code=code.replace('__atomic_thread_fence(__ATOMIC_ACQUIRE);\n   if(stamp', '__atomic_thread_fence(__ATOMIC_ACQUIRE);\n   test_interference();\n   if(stamp')
  code=code.replace('unsigned target_version=auto_mode?', 'test_selection_interference();\n unsigned target_version=auto_mode?')
  irq=code[code.index('void native_live_pc_sample'):code.index('static void live_pc_image_report')]
  for forbidden in ('for(', 'while(', '__atomic_fetch_', '__atomic_exchange_', '__atomic_compare_exchange_', 'printf(', 'task_lock', 'proc_iterate', 'proc_best_name', 'proc_find'):
   self.assertNotIn(forbidden,irq)
  prefix=r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <pthread.h>
#define KERN_SUCCESS 0
#define PROC_RETURNED 0
#define PROC_ALLPROCLIST 1
#define PROC_NOWAITTRANS 4
typedef struct process *proc_t;
typedef struct task *task_t;
typedef void *vm_map_t;
struct task {vm_map_t map; uint32_t task_shared_region_slide;};
struct process {int pid,version; uint64_t uid; struct task task;};
struct thread {uint64_t thread_id;};
static struct process process={1,7,42,{(void *)1,0x1000}};
static const char *test_name="backboardd";
static int process_count=1;
static _Thread_local unsigned test_cpu;
static _Thread_local struct thread thread={99};
static uint64_t image_info_address, mutate_address, fail_address;
static int mutate_on_read,mutate_reads, refs, proc_refs, change_version_on_read, inject_torn;
static int inject_selection;
static void test_interference(void);
static void test_selection_interference(void);
static int proc_getpid(proc_t p){return p->pid;}
static int proc_pid(proc_t p){return p->pid;}
static const char *proc_best_name(proc_t p){(void)p;return test_name;}
static void proc_iterate(unsigned flags,int (*cb)(proc_t,void *),void *arg,int (*filter)(proc_t,void *),void *farg){assert(flags==PROC_ALLPROCLIST);(void)filter;(void)farg;for(int i=0;i<process_count;i++)cb(&process,arg);}
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
static void test_selection_interference(void){if(inject_selection){inject_selection=0;
 uint64_t s=__atomic_load_n(&live_pc_target_seq,__ATOMIC_RELAXED);
 __atomic_store_n(&live_pc_target_seq,s+1,__ATOMIC_SEQ_CST);__atomic_thread_fence(__ATOMIC_SEQ_CST);
 __atomic_store_n(&live_pc_pid,2,__ATOMIC_RELAXED);__atomic_store_n(&live_pc_target_uid,43,__ATOMIC_RELAXED);
 __atomic_store_n(&live_pc_target_version,8,__ATOMIC_RELAXED);__atomic_store_n(&live_pc_target_seq,s+2,__ATOMIC_RELEASE);
 }}
static void *writer(void *arg){test_cpu=(unsigned)(uintptr_t)arg;thread.thread_id=100+test_cpu;for(unsigned i=0;i<100000;i++){uint64_t pc=0x10000000ULL+((uint64_t)test_cpu<<24)+i;native_live_pc_sample(pc,pc^0x55aa,1);}return 0;}
static void *selection_writer(void *arg){(void)arg;for(unsigned i=0;i<100000;i++){
 uint64_t s=__atomic_load_n(&live_pc_target_seq,__ATOMIC_RELAXED);
 __atomic_store_n(&live_pc_target_seq,s+1,__ATOMIC_SEQ_CST);__atomic_thread_fence(__ATOMIC_SEQ_CST);
 __atomic_store_n(&live_pc_pid,1,__ATOMIC_RELAXED);
 __atomic_store_n(&live_pc_target_version,(i&1)?7:8,__ATOMIC_RELAXED);
 __atomic_store_n(&live_pc_target_uid,(i&1)?43:42,__ATOMIC_RELAXED);
 __atomic_store_n(&live_pc_target_seq,s+2,__ATOMIC_RELEASE);
 }return 0;}
int main(void){
 boot_live_pc_init(0);native_live_pc_sample(1,2,1);assert(live_pc_cpus[0].ticks==0);
 boot_live_pc_init(56);native_live_pc_sample(1,2,1);assert(live_pc_cpus[0].ticks==0);
 process.pid=56;native_live_pc_sample(0x2222,0x3333,1);assert(live_pc_cpus[0].written==1);
 process.pid=1;native_live_pc_sample(1,2,1);assert(live_pc_cpus[0].written==1);
 memset(live_pc_cpus,0,sizeof(live_pc_cpus));
 boot_live_pc_auto_init();process_count=0;live_pc_refresh_target();native_live_pc_sample(1,2,1);assert(live_pc_cpus[0].ticks==0);
 process_count=1;process.pid=56;live_pc_refresh_target();native_live_pc_sample(0x2222,0x7777,1);assert(live_pc_cpus[0].written==1);
 assert(live_pc_cpus[0].rows[0].pid==56 && live_pc_cpus[0].rows[0].uid==42 && live_pc_cpus[0].rows[0].exec_version==7);
 process.uid=43;native_live_pc_sample(1,2,1);assert(live_pc_cpus[0].written==1 && live_pc_cpus[0].ticks==1); /* same PID, new process */
 live_pc_refresh_target();native_live_pc_sample(1,2,1);assert(live_pc_cpus[0].ticks==2);
 process.version=8;native_live_pc_sample(1,2,1);assert(live_pc_cpus[0].ticks==2); /* exec before refresh */
 live_pc_refresh_target();native_live_pc_sample(1,2,1);assert(live_pc_cpus[0].ticks==3);
 process_count=2;live_pc_refresh_target();native_live_pc_sample(1,2,1);assert(live_pc_cpus[0].ticks==3);
 process_count=1;test_name="other";live_pc_refresh_target();native_live_pc_sample(1,2,1);assert(live_pc_cpus[0].ticks==3);
 /* Force an old PID/UID with a new exec version across publication. */
 test_name="backboardd";process.pid=1;process.uid=42;process.version=7;live_pc_refresh_target();
 process.version=8;inject_selection=1;native_live_pc_sample(1,2,1);assert(live_pc_cpus[0].ticks==3);
 live_pc_auto=0;process.pid=1;process.uid=42;process.version=7;memset(live_pc_cpus,0,sizeof(live_pc_cpus));
 boot_live_pc_init(1);native_live_pc_sample(0x1234,0x1234^0x55aa,1);assert(live_pc_cpus[0].written==1);
 test_cpu=9;native_live_pc_sample(1,2,1);assert(live_pc_bad_cpu==1);test_cpu=0;
 uint8_t macho[160]={0};struct live_macho_head *h=(void *)macho;h->magic=0xfeedfacf;h->ncmds=2;h->sizeofcmds=96;
 uint32_t *u=(void *)(macho+32);u[0]=0x1b;u[1]=24;memset(macho+40,0x5a,16);
 uint32_t *seg=(void *)(macho+56);seg[0]=0x19;seg[1]=72;memcpy(macho+64,"__TEXT",6);uint64_t vm=0x100000000;memcpy(macho+80,&vm,8);
 uint64_t checked=0;assert(live_image_entry_address(UINT64_MAX-10,1,&checked)!=0);assert(live_image_entry_address(UINT64_MAX-24,1,&checked)==0 && checked==UINT64_MAX);
 struct live_image_row row={0};live_image_decode((void *)1,(uintptr_t)macho,&row);assert(row.complete && row.uuid_valid && row.text_valid && row.text_vm==vm);
 struct live_image_info entry={(uintptr_t)macho,0,0};struct live_info_head info={15,1,(uintptr_t)&entry,0,0,1,{0},0};image_info_address=(uintptr_t)&info;
 live_pc_image_report(1,1011,0,0);assert(!refs && !proc_refs);
 mutate_address=(uintptr_t)&entry;mutate_on_read=2;mutate_reads=0;live_pc_image_report(1,1012,0,0);assert(!refs && !proc_refs);
 mutate_address=0;fail_address=(uintptr_t)(macho+56);live_pc_image_report(1,1013,0,0);fail_address=0;
 change_version_on_read=1;live_pc_image_report(1,1014,0,0);assert(!refs && !proc_refs);process.version=7;
 fail_address=(uintptr_t)&info;live_pc_image_report(1,1015,0,0);fail_address=0;
 live_pc_image_report(1,1016,999,7);live_pc_image_report(1,1017,42,8);assert(!refs && !proc_refs);
 h->ncmds=3;memset(&row,0,sizeof(row));live_image_decode((void *)1,(uintptr_t)macho,&row);assert(row.valid && !row.complete);h->ncmds=2;
 for(unsigned i=0;i<2000;i++)native_live_pc_sample(0x9000+i,(0x9000+i)^0x55aa,1);inject_torn=1;boot_live_pc_report();
 pthread_t a,b;pthread_create(&a,0,writer,(void *)0);pthread_create(&b,0,writer,(void *)1);
 for(unsigned i=0;i<15;i++)boot_live_pc_report();
 pthread_join(a,0);pthread_join(b,0);boot_live_pc_report();
 /* Neither published identity matches (1,42,7); only a torn mixture would. */
 memset(live_pc_cpus,0,sizeof(live_pc_cpus));memset(live_pc_previous,0,sizeof(live_pc_previous));
 __atomic_store_n(&live_pc_target_version,8,__ATOMIC_RELAXED);__atomic_store_n(&live_pc_target_uid,42,__ATOMIC_RELEASE);
 boot_live_pc_auto_init();pthread_create(&a,0,writer,(void *)0);pthread_create(&b,0,selection_writer,0);
 pthread_join(a,0);pthread_join(b,0);
 assert(live_pc_cpus[0].ticks==0 && live_pc_cpus[0].written==0);
 __atomic_store_n(&live_pc_auto,0,__ATOMIC_RELEASE);
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
  for epoch in (1016,1017):
   self.assertTrue(any(f'images epoch={epoch} status=changed reason=target_reused_or_exec' in x for x in lines))
   self.assertFalse(any(f'livepc image epoch={epoch} ' in x for x in lines))
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
