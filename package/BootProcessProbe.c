// SPDX-License-Identifier: BSD-2-Clause-Patent
// From UniXNU-tb128fu package/BootProcessProbe.c (fdb4434, itself UniXNU-sheng), unchanged except this header: a04br3 prints
// its rows on UART from the a04br3-proclog thread (package/A04BR3Board.cpp) instead of a boot-progress page.
// Process traversal can wait in proc_iterate despite the bounded thread scan.
// Run capture only in its separate worker. The display takes a nonblocking copy
// of the last complete result; never expose an in-progress row to the renderer.
#include <kern/task.h>
#include <kern/thread.h>
#include <kern/recount.h>
#include <kern/simple_lock.h>
#include <machine/machine_routines.h>
#include <machine/cpu_number.h>
#include <mach/mach_time.h>
#include <arm/thread.h>
#include <string.h>
#include <pexpert/pexpert.h>
#include <kern/misc_protos.h>
#include <kern/clock.h>
#include <vm/vm_map.h>
#include <vm/vm_page.h>
#include <vm/vm_pageout.h>
#include "BootMemoryQueueSnapshot.h"

// Nontransactional page counters, as in host_statistics64. Called only by
// the board's explicitly enabled stack diagnostics, with no VM lock held.
void boot_memory_snapshot(uint64_t out[7]) {
 out[0]=PAGE_SIZE;
 out[1]=vm_page_free_count;
 out[2]=vm_page_active_count;
 out[3]=vm_page_inactive_count;
 out[4]=vm_page_wire_count;
 out[5]=compressor_object ? VM_PAGE_COMPRESSOR_COUNT : 0;
 out[6]=vm_page_speculative_count;
}
// Supplemental policy/queue reads only; gate and bounded rows belong to Board.
void boot_memory_queue_snapshot(struct boot_memory_queue_record *out) {
 if (!out) return;
 *out=(struct boot_memory_queue_record){0};
 out->throttled=vm_page_throttled_count;
 out->lopage_free=vm_lopage_free_count;
 out->pageable_internal=vm_page_pageable_internal_count;
 out->pageable_external=vm_page_pageable_external_count;
 out->compressor_mode=vm_compressor_mode;
 out->compressor_present=VM_CONFIG_COMPRESSOR_IS_PRESENT ? 1u : 0u;
 out->compressor_active=VM_CONFIG_COMPRESSOR_IS_ACTIVE ? 1u : 0u;
 out->swap_present=VM_CONFIG_SWAP_IS_PRESENT ? 1u : 0u;
 out->swap_active=VM_CONFIG_SWAP_IS_ACTIVE ? 1u : 0u;
 out->freezer_swap_active=VM_CONFIG_FREEZER_SWAP_IS_ACTIVE ? 1u : 0u;
}
// Exact private process ABI from this source revision's proc.h/proc_internal.h.
// Do not mix BSD and Mach-private header type universes in one translation unit.
struct proc;typedef struct proc *proc_t;
char *proc_best_name(proc_t);
int proc_pid(proc_t);
uint64_t proc_uniqueid(proc_t);
task_t proc_task(proc_t);
void proc_iterate(unsigned,int (*)(proc_t,void *),void *,int (*)(proc_t,void *),void *);
#define PROC_RETURNED 0
#define PROC_ALLPROCLIST (1U<<0)
#define PROC_NOWAITTRANS (1U<<2)
char boot_process_rows[10][120];
extern void boot_ui_trace_begin(void) __attribute__((weak_import));
extern void boot_ui_trace_task(task_t,unsigned,int,const char *,unsigned) __attribute__((weak_import));
extern void boot_ui_trace_publish(void) __attribute__((weak_import));
extern int boot_ui_trace_role_peer(unsigned) __attribute__((weak_import));

volatile unsigned boot_process_passes,boot_process_seen;
volatile unsigned boot_process_attempts,boot_process_busy;
static char capture_rows[10][120],published_rows[10][120];
static unsigned capture_seen,capture_passes,published_passes,published_seen,publish_lock;
static unsigned chrono_focus_initialized;
static char focus_name[32],focus2_name[32],focus3_name[32],focus4_name[32];
static unsigned trace_slot[10]={0,1,1};
static const char *targets[]={"launchd","backboardd","SpringBoard","PreBoard","runningboardd","lsd","keybagd","cfprefsd","containermanagerd","kernel_task"};
// boot-ui-follow=1: follow a wait chain by pid. follow_pid[0] is the process the SpringBoard slot sends to (traced in slot 4, role U),
// follow_pid[1] the one that process sends to (traced in slot 1, role B); 0 = nothing to follow.
static unsigned follow_enabled;
static int follow_pid[2];
// SpringBoard and backboardd keep their own slots (2, 3) even when the chain leads to them.
static int followed_slot(proc_t p) {
 int pid=proc_pid(p);const char *name=proc_best_name(p);
 if(!strcmp(name,targets[2]) || !strcmp(name,targets[3]))return 10;
 if(follow_pid[0]>0 && pid==follow_pid[0])return 4;
 if(follow_pid[1]>0 && pid==follow_pid[1])return 1;
 return 10;
}
// A duplicate focus name must not hide its traced slot behind an earlier
// untraced summary row (e.g. focus4=launchd). Equal trace priorities retain
// the original first-match order; each process is still captured only once.
static unsigned capture_slot(proc_t p) {
 const char *name=proc_best_name(p);unsigned index=10;
 for(unsigned i=0;i<10;++i) {
  if(strcmp(name,targets[i]))continue;
  // Followed pids own these slots. A duplicate name may still use another row.
  if((i==4 && follow_pid[0]>0) || (i==1 && follow_pid[1]>0))continue;
  if(index==10 || (!trace_slot[index] && trace_slot[i]))index=i;
 }
 if(proc_pid(p)==0)index=9;
 int followed=followed_slot(p);
 if(followed<10)index=(unsigned)followed;
 return index;
}
static int selected(proc_t p,void *arg) {
 (void)arg;
 return capture_slot(p)<10;
}
static int capture(proc_t p,void *arg) {
 (void)arg;
 const char *name=proc_best_name(p);unsigned index=capture_slot(p);
 if(index==10)return PROC_RETURNED;
 task_t task=proc_task(p);
 if(!task || !task_lock_try(task)) {
  snprintf(capture_rows[index],120,"%.18s p%d task unavailable/busy",name,proc_pid(p));return PROC_RETURNED;
 }
 unsigned count=0,running=0,waiting=0,busy=0,state=0,sampled=0;int priority=0;
 uint64_t wait=0,continuation=0,userPC=0;
 thread_t thread;
 queue_iterate(&task->threads,thread,thread_t,task_threads){
  if(count++>=128)break;
  bool irq=ml_set_interrupts_enabled(false);
  if(!simple_lock_try(&thread->sched_lock,&thread_lck_grp)){++busy;ml_set_interrupts_enabled(irq);continue;}
  unsigned flags=(unsigned)thread->state;
  running+=(flags&TH_RUN)!=0;waiting+=(flags&TH_WAIT)!=0;
  bool take=count==1;
  if(index==9){char tn[64]={};thread_get_thread_name(thread,tn);take=!strcmp(tn,"VM_pageout_scan");}
  if(take){
   sampled=1;state=flags;priority=thread->sched_pri;wait=thread->wait_event;
   continuation=(uint64_t)thread->continuation;
   if(thread->machine.upcb)userPC=(uint64_t)get_saved_state_pc(thread->machine.upcb);
  }
  thread_unlock(thread);ml_set_interrupts_enabled(irq);
 }
 unsigned total=(unsigned)task->thread_count,slide=task->task_shared_region_slide;task_unlock(task);
 snprintf(capture_rows[index],120,"%.15s p%d n%u r%u w%u b%u t%u s%x/%d W%llx C%llx U%llx +%x",
  name,proc_pid(p),total,running,waiting,busy,sampled,state,priority,wait,continuation,userPC,slide);
 if(trace_slot[index] && boot_ui_trace_task)boot_ui_trace_task(task,index,proc_pid(p),name,slide);
 capture_seen=capture_seen+1;return PROC_RETURNED;
}
void boot_process_capture(void) {
 // Exactly one capture worker. Refuse reentry without waiting or modifying its
 // partial results, including in host failure-injection checks.
 if(__atomic_exchange_n(&boot_process_busy,1,__ATOMIC_ACQUIRE))return;
 __atomic_fetch_add(&boot_process_attempts,1,__ATOMIC_RELAXED);
 if(!chrono_focus_initialized) {
  unsigned focus=0;PE_parse_boot_argn("boot-ui-chrono",&focus,sizeof(focus));
  if(focus==1){targets[1]="chronod";targets[3]="backboardd";}
  // boot-ui-focus=<process> replaces the sampled second slot; the chronod flag above stays as a fallback.
  if(PE_parse_boot_argn("boot-ui-focus",focus_name,sizeof(focus_name)) && focus_name[0]) {
   focus_name[sizeof(focus_name)-1]=0;targets[1]=focus_name;targets[3]="backboardd";
  }
  // boot-ui-focus2=<process> likewise replaces the third slot (normally SpringBoard, role S).
  if(PE_parse_boot_argn("boot-ui-focus2",focus2_name,sizeof(focus2_name)) && focus2_name[0]) {
   focus2_name[sizeof(focus2_name)-1]=0;targets[2]=focus2_name;
  }
  // boot-ui-focus3/4 add two more traced slots (replacing the PreBoard/runningboardd rows).
  if(PE_parse_boot_argn("boot-ui-focus3",focus3_name,sizeof(focus3_name)) && focus3_name[0]) {
   focus3_name[sizeof(focus3_name)-1]=0;targets[3]=focus3_name;trace_slot[3]=1;
  }
  if(PE_parse_boot_argn("boot-ui-focus4",focus4_name,sizeof(focus4_name)) && focus4_name[0]) {
   focus4_name[sizeof(focus4_name)-1]=0;targets[4]=focus4_name;trace_slot[4]=1;
  }
  PE_parse_boot_argn("boot-ui-follow",&follow_enabled,sizeof(follow_enabled));
  if(follow_enabled==1)trace_slot[4]=trace_slot[1]=1;
  chrono_focus_initialized=1;
 }
 // The previous pass's traces name the next processes to follow (a busy trace keeps the previous choice).
 if(follow_enabled==1 && boot_ui_trace_role_peer) {
  int first=boot_ui_trace_role_peer(1),second=boot_ui_trace_role_peer(3);
  if(first>=0) {
   if(first!=follow_pid[0])second=0;   // slot 4's trace belongs to the previous choice
   follow_pid[0]=first;
   if(second>=0)follow_pid[1]=first>0 && second!=first?second:0;
  }
 }
 for(unsigned i=0;i<10;++i)snprintf(capture_rows[i],120,"%.17s: absent from this process pass",targets[i]);
 capture_seen=0;
 if(boot_ui_trace_begin)boot_ui_trace_begin();
 // No waiting for exec/exit transitions; callouts run outside proc_list_lock.
 proc_iterate(PROC_ALLPROCLIST|PROC_NOWAITTRANS,capture,NULL,selected,NULL);
 if(boot_ui_trace_publish)boot_ui_trace_publish();
 ++capture_passes;
 if(!__atomic_exchange_n(&publish_lock,1,__ATOMIC_ACQUIRE)) {
  memcpy(published_rows,capture_rows,sizeof(published_rows));
  published_passes=capture_passes;published_seen=capture_seen;
  __atomic_store_n(&publish_lock,0,__ATOMIC_RELEASE);
 }
 __atomic_store_n(&boot_process_busy,0,__ATOMIC_RELEASE);
}
void boot_process_snapshot(void) {
 // One reader: the diagnostic monitor. Never allocate, walk processes or wait
 // for the worker; leave the previous complete display snapshot if it is busy.
 if(__atomic_exchange_n(&publish_lock,1,__ATOMIC_ACQUIRE))return;
 memcpy(boot_process_rows,published_rows,sizeof(boot_process_rows));
 boot_process_passes=published_passes;boot_process_seen=published_seen;
 __atomic_store_n(&publish_lock,0,__ATOMIC_RELEASE);
}

// Default-off sampler, invoked only by the existing heartbeat worker. No UI
// stack walk or printing while task/scheduler locks are held.
#if PRECISE_USER_KERNEL_TIME
#define HOME_PERF_PRECISE 1u
#else
#define HOME_PERF_PRECISE 0u
#endif
#define HOME_PERF_THREADS 8
struct home_perf_thread {
 uint64_t tid, user_ticks, system_ticks, pc, lr, event, continuation, kernel[4];
 unsigned state, valid_pc, valid_kernel;
};
struct home_perf_row {
 char name[24]; int pid; unsigned threads, running, waiting, busy, sampled, state, detail_count;
 uint64_t uniqueid, wait, cpu_ticks, cpu_at, resident, compressed, footprint;
 uint64_t user_ticks, system_ticks;
 struct home_perf_thread detail[HOME_PERF_THREADS];
};
// Capture saved kernel state only for a thread that is not runnable. A running
// thread's switched-out frame is stale; never claim it is its current stack.
static void home_perf_kernel_stack(thread_t th, struct home_perf_thread *d) {
 if ((th->state & TH_RUN) || !th->kernel_stack) return;
 uint64_t low=(uint64_t)th->kernel_stack, high=low+(uint64_t)kernel_stack_size;
 if (high<low) return;
 thread_kernel_state_t k=thread_get_kernel_state(th);
 uint64_t fp=k->machine.ss.fp; d->kernel[0]=k->machine.ss.lr;d->valid_kernel=1;
 for(unsigned i=1;i<4 && fp>=low && fp<=high && high-fp>=16 && !(fp&15);++i) {
  const uint64_t *frame=(const uint64_t *)fp;d->kernel[i]=frame[1];
  uint64_t next=frame[0];if(next<=fp)break;fp=next;
 }
}
static struct home_perf_row home_perf_rows[12];
static unsigned home_perf_count;
static int home_perf_selected(proc_t p, void *unused) {
 (void)unused; const char *n=proc_best_name(p);
 static const char *const names[]={"launchd","backboardd","SpringBoard","runningboardd","logd","kernel_task","WindowServer","mediaserverd","lsd","UserEventAgent","Preferences","iconservicesagent","iconservicesagen"};
 for (unsigned i=0;i<sizeof(names)/sizeof(names[0]);++i) if (!strcmp(n,names[i])) return 1;
 return proc_pid(p)==0;
}
static int home_perf_capture(proc_t p, void *unused) {
 (void)unused;
 if(home_perf_count>=12 || !home_perf_selected(p,NULL))return PROC_RETURNED;
 struct home_perf_row *r=&home_perf_rows[home_perf_count++];
 memset(r,0,sizeof(*r));strlcpy(r->name,proc_best_name(p),sizeof(r->name));r->pid=proc_pid(p);r->uniqueid=proc_uniqueid(p);
 task_t task=proc_task(p);if(!task){r->busy=1;return PROC_RETURNED;}
 r->cpu_ticks=get_task_cpu_time(task);r->cpu_at=mach_absolute_time();
 struct recount_times_mach times=recount_task_times(task);
 r->user_ticks=times.rtm_user;r->system_ticks=times.rtm_system;
 r->resident=get_task_resident_size(task);
 r->compressed=get_task_compressed(task);r->footprint=get_task_phys_footprint(task);
 if(!task_lock_try(task)){r->busy=1;return PROC_RETURNED;}
 r->threads=task->thread_count; thread_t th; unsigned scanned=0;
 queue_iterate(&task->threads,th,thread_t,task_threads){
  if(scanned++>=32)break;
  bool irq=ml_set_interrupts_enabled(false);
  if(!simple_lock_try(&th->sched_lock,&thread_lck_grp)){++r->busy;ml_set_interrupts_enabled(irq);continue;}
  unsigned st=(unsigned)th->state;r->running+=(st&TH_RUN)!=0;r->waiting+=(st&TH_WAIT)!=0;
  if(!r->sampled){r->sampled=1;r->state=st;r->wait=(uint64_t)th->wait_event;}
  if(r->pid==1 && r->detail_count<HOME_PERF_THREADS) {
   struct home_perf_thread *d=&r->detail[r->detail_count++];
   struct recount_times_mach tt=recount_thread_times(th);
   d->tid=th->thread_id;d->state=st;d->event=(uint64_t)th->wait_event;
   d->continuation=(uint64_t)th->continuation;d->user_ticks=tt.rtm_user;d->system_ticks=tt.rtm_system;
   arm_saved_state_t *saved=th->machine.upcb;
   if(saved && is_saved_state64(saved)){d->valid_pc=1;d->pc=get_saved_state_pc(saved);d->lr=get_saved_state_lr(saved);}
   home_perf_kernel_stack(th,d);
  }
  thread_unlock(th);ml_set_interrupts_enabled(irq);
 }
 task_unlock(task);return PROC_RETURNED;
}
void boot_home_perf_sample(void) {
 home_perf_count=0;
 proc_iterate(PROC_ALLPROCLIST|PROC_NOWAITTRANS,home_perf_capture,NULL,home_perf_selected,NULL);
 uint64_t now=0;absolutetime_to_nanoseconds(mach_absolute_time(),&now);
 printf("a04br3 perf sample ns=%llu tasks=%u\n",now,home_perf_count);
 for(unsigned i=0;i<home_perf_count;++i){
  struct home_perf_row *r=&home_perf_rows[i];uint64_t cpu=0, at=0, user=0, system=0;
  absolutetime_to_nanoseconds(r->cpu_ticks,&cpu);absolutetime_to_nanoseconds(r->cpu_at,&at);
  absolutetime_to_nanoseconds(r->user_ticks,&user);absolutetime_to_nanoseconds(r->system_ticks,&system);
  printf("a04br3 perf task p%d %.23s uid=%llu cpu_ns=%llu cpu_at_ns=%llu resident=%llu compressed=%llu footprint=%llu threads=%u run=%u wait=%u busy=%u sampled=%u state=%x event=%llx\n",
   r->pid,r->name,r->uniqueid,cpu,at,r->resident,r->compressed,r->footprint,r->threads,r->running,r->waiting,r->busy,r->sampled,r->state,r->wait);
  if(r->pid==1) {
   printf("a04br3 perf launchd times split=%u user_ns=%llu system_ns=%llu\n",HOME_PERF_PRECISE,user,system);
   for(unsigned j=0;j<r->detail_count;++j){
    struct home_perf_thread *d=&r->detail[j];uint64_t tu=0,ts=0;
    absolutetime_to_nanoseconds(d->user_ticks,&tu);absolutetime_to_nanoseconds(d->system_ticks,&ts);
    printf("a04br3 perf launchd thread tid=%llu state=%x event=%llx user_ns=%llu system_ns=%llu\n",
     d->tid,d->state,d->event,tu,ts);
    printf("a04br3 perf launchd pc tid=%llu valid=%u pc=%llx lr=%llx cont=%llx k_valid=%u k=%llx/%llx/%llx/%llx\n",
     d->tid,d->valid_pc,d->pc,d->lr,d->continuation,d->valid_kernel,d->kernel[0],d->kernel[1],d->kernel[2],d->kernel[3]);
   }
  }
 }
}

// Optional timer-IRQ PC histogram. Called by the generic, compile-gated XNU
// timer hook with interrupts masked. Bounded atomics only: no allocation,
// waiting, stack walk or logging. A single try-lock drops contended samples.
// Slots rotate every report; overflow/contended samples are explicitly counted.
#define HOME_IRQ_SLOTS 64u
#define HOME_IRQ_RATE 16u
struct home_irq_slot { uint64_t pc, hits; };
static struct home_irq_slot home_irq_slots[HOME_IRQ_SLOTS];
static unsigned home_irq_pid;
static uint64_t home_irq_ticks, home_irq_sampled, home_irq_overflow, home_irq_uniqueid, home_irq_other_generation;
static uint64_t home_irq_contended, home_irq_epoch, home_irq_epoch_overflow, home_irq_start;
static uint64_t home_irq_report_skipped;
static unsigned home_irq_table_lock;
int proc_getpid(proc_t);
extern int native_irq_pc_hook_compiled(void) __attribute__((weak_import));
unsigned boot_home_irq_hook_available(void) {
 return native_irq_pc_hook_compiled ? (unsigned)native_irq_pc_hook_compiled() : 0u;
}
void boot_home_irq_init(unsigned pid) {
 __atomic_store_n(&home_irq_pid,pid,__ATOMIC_RELEASE);
}
void native_irq_pc_sample(uint64_t pc,unsigned user) {
 unsigned wanted=__atomic_load_n(&home_irq_pid,__ATOMIC_ACQUIRE);
 if(!wanted || user || !pc)return;
 proc_t p=(proc_t)get_bsdtask_info(current_task());
 if(!p || proc_getpid(p)!=(int)wanted)return;
 uint64_t tick=__atomic_fetch_add(&home_irq_ticks,1,__ATOMIC_RELAXED);
 if(tick%HOME_IRQ_RATE)return;
 uint64_t uid=proc_uniqueid(p);
 if(!uid){__atomic_fetch_add(&home_irq_other_generation,1,__ATOMIC_RELAXED);return;}
 uint64_t expected=0;
 __atomic_compare_exchange_n(&home_irq_uniqueid,&expected,uid,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);
 if(__atomic_load_n(&home_irq_uniqueid,__ATOMIC_ACQUIRE)!=uid){
  __atomic_fetch_add(&home_irq_other_generation,1,__ATOMIC_RELAXED);return;
 }
 __atomic_fetch_add(&home_irq_sampled,1,__ATOMIC_RELAXED);
 uint64_t bucket=pc&~255ULL;
 if(!bucket){__atomic_fetch_add(&home_irq_overflow,1,__ATOMIC_RELAXED);return;}
 if(__atomic_exchange_n(&home_irq_table_lock,1,__ATOMIC_ACQUIRE)) {
  __atomic_fetch_add(&home_irq_contended,1,__ATOMIC_RELAXED);return;
 }
 unsigned start=(unsigned)((bucket>>8)%HOME_IRQ_SLOTS);
 for(unsigned i=0;i<HOME_IRQ_SLOTS;++i) {
  struct home_irq_slot *slot=&home_irq_slots[(start+i)%HOME_IRQ_SLOTS];
  if(!slot->pc)slot->pc=bucket;
  if(slot->pc==bucket){++slot->hits;__atomic_store_n(&home_irq_table_lock,0,__ATOMIC_RELEASE);return;}
 }
 ++home_irq_epoch_overflow;
 __atomic_fetch_add(&home_irq_overflow,1,__ATOMIC_RELAXED);
 __atomic_store_n(&home_irq_table_lock,0,__ATOMIC_RELEASE);
}
void boot_home_irq_report(void) {
 unsigned pid=__atomic_load_n(&home_irq_pid,__ATOMIC_ACQUIRE);
 if(!pid)return;
 if(__atomic_exchange_n(&home_irq_table_lock,1,__ATOMIC_ACQUIRE)){++home_irq_report_skipped;return;}
 uint64_t end_ticks=mach_absolute_time(),start_ticks=home_irq_start;home_irq_start=end_ticks;
 uint64_t epoch_overflow=home_irq_epoch_overflow;home_irq_epoch_overflow=0;
 uint64_t skipped=home_irq_report_skipped;home_irq_report_skipped=0;
 struct home_irq_slot snapshot[HOME_IRQ_SLOTS];
 memcpy(snapshot,home_irq_slots,sizeof(snapshot));
 memset(home_irq_slots,0,sizeof(home_irq_slots));
 uint64_t epoch=++home_irq_epoch;
 __atomic_store_n(&home_irq_table_lock,0,__ATOMIC_RELEASE);
 struct {uint64_t pc,hits;} top[8]={0};uint64_t stored=0;
 for(unsigned i=0;i<HOME_IRQ_SLOTS;++i) {
  uint64_t pc=snapshot[i].pc,hits=snapshot[i].hits;stored+=hits;
  if(!pc || !hits)continue;
  for(unsigned j=0;j<8;++j)if(hits>top[j].hits){
   for(unsigned k=7;k>j;--k)top[k]=top[k-1];
   top[j].pc=pc;top[j].hits=hits;break;
  }
 }
 uint64_t at=0,start=0;absolutetime_to_nanoseconds(end_ticks,&at);
 if(start_ticks)absolutetime_to_nanoseconds(start_ticks,&start);
 printf("a04br3 irq pc pid=%u uid=%llu hook=%u epoch=%llu start_ns=%llu at_ns=%llu stored=%llu lost_full=%llu skipped=%llu shift=8 rate=%u\n",
  pid,__atomic_load_n(&home_irq_uniqueid,__ATOMIC_ACQUIRE),boot_home_irq_hook_available(),epoch,start,at,stored,epoch_overflow,skipped,HOME_IRQ_RATE);
 printf("a04br3 irq pc counts epoch=%llu ticks=%llu samples=%llu overflow=%llu busy=%llu other=%llu\n",
  epoch,__atomic_load_n(&home_irq_ticks,__ATOMIC_RELAXED),__atomic_load_n(&home_irq_sampled,__ATOMIC_RELAXED),
  __atomic_load_n(&home_irq_overflow,__ATOMIC_RELAXED),__atomic_load_n(&home_irq_contended,__ATOMIC_RELAXED),__atomic_load_n(&home_irq_other_generation,__ATOMIC_RELAXED));
 for(unsigned i=0;i<8 && top[i].hits;++i)
  printf("a04br3 irq pc bucket=%llx hits=%llu epoch=%llu\n",top[i].pc,top[i].hits,epoch);
}

// IPI counters are enabled only for the diagnostic boot option. The weak XNU
// hook runs with interrupts disabled, before an actual PE dispatch; no stacks,
// allocation, cross-calls or logging happen on the signal path.
static unsigned ipi_reason_enabled;
struct ipi_reason_row { uint64_t counts[4][7]; } __attribute__((aligned(64)));
static struct ipi_reason_row ipi_reason_rows[4];
static unsigned ipi_reason_out_of_range_seen;
void boot_ipi_reason_enable(unsigned enabled) {
 __atomic_store_n(&ipi_reason_enabled, enabled == 1, __ATOMIC_RELAXED);
}
void native_ipi_signal_record(unsigned source, unsigned target, unsigned signal, unsigned defer) {
 if (!__atomic_load_n(&ipi_reason_enabled, __ATOMIC_RELAXED)) return;
 if (source >= 4 || target >= 4) { __atomic_store_n(&ipi_reason_out_of_range_seen,1U,__ATOMIC_RELAXED); return; }
 unsigned kind = signal == 0 ? 0 : signal == 1 ? 1 : signal == 4 ? 2 : signal == 8 ? 3 : signal == 0x200 ? 4 : 5;
 // XNU supplies the current CPU as source with interrupts disabled. Each
 // source row therefore has exactly one writer; the worker only reads it.
 // Atomic loads/stores avoid an unbounded exclusive-store retry loop.
 uint64_t n=__atomic_load_n(&ipi_reason_rows[source].counts[target][kind],__ATOMIC_RELAXED);
 __atomic_store_n(&ipi_reason_rows[source].counts[target][kind],n+1,__ATOMIC_RELAXED);
 if (defer) {
  n=__atomic_load_n(&ipi_reason_rows[source].counts[target][6],__ATOMIC_RELAXED);
  __atomic_store_n(&ipi_reason_rows[source].counts[target][6],n+1,__ATOMIC_RELAXED);
 }
}
void boot_ipi_reason_report(void) {
 if (!__atomic_load_n(&ipi_reason_enabled, __ATOMIC_RELAXED)) return;
 unsigned observed=__atomic_load_n(&ipi_reason_out_of_range_seen,__ATOMIC_RELAXED);
 for (unsigned source=0; source<4 && !observed; ++source)
  for (unsigned target=0; target<4 && !observed; ++target)
   for (unsigned kind=0; kind<6; ++kind)
    if (__atomic_load_n(&ipi_reason_rows[source].counts[target][kind],__ATOMIC_RELAXED)) { observed=1; break; }
 if (!observed) {
  printf("a04br3 ipi reason unavailable: hook not observed (not built or no dispatch yet)\n");
  return;
 }
 for (unsigned source=0; source<4; ++source) {
  uint64_t kinds[7]={0},dest[4]={0};
  for (unsigned target=0; target<4; ++target) for (unsigned kind=0; kind<7; ++kind) {
   uint64_t n=__atomic_load_n(&ipi_reason_rows[source].counts[target][kind],__ATOMIC_RELAXED);
   kinds[kind]+=n; if (kind<6) dest[target]+=n;
  }
  printf("a04br3 ipi reason src=%u dest=%llu/%llu/%llu/%llu nop=%llu dec=%llu xcall=%llu ast=%llu imm=%llu other=%llu defer=%llu (non-atomic)\n",
   source,(unsigned long long)dest[0],(unsigned long long)dest[1],(unsigned long long)dest[2],(unsigned long long)dest[3],
   (unsigned long long)kinds[0],(unsigned long long)kinds[1],(unsigned long long)kinds[2],(unsigned long long)kinds[3],
   (unsigned long long)kinds[4],(unsigned long long)kinds[5],(unsigned long long)kinds[6]);
 }
 printf("a04br3 ipi reason out_of_range_seen=%u\n",__atomic_load_n(&ipi_reason_out_of_range_seen,__ATOMIC_RELAXED));
}

// Optional AST call-site histogram. Caller supplies the current CPU with local
// interrupts masked; each per-CPU row has one writer and bounded linear scan.
#define AST_CALLER_CPUS 4u
#define AST_CALLER_SLOTS 32u
#define AST_CALLER_RATE 64u
struct ast_caller_slot { uint64_t pc, count; };
struct ast_caller_row { uint64_t seen, dropped; uint32_t rng; struct ast_caller_slot slots[AST_CALLER_SLOTS]; } __attribute__((aligned(64)));
static struct ast_caller_row ast_caller_rows[AST_CALLER_CPUS];
unsigned native_ast_caller_enabled;
static unsigned ast_caller_bad_cpu;
void boot_ast_caller_enable(unsigned enabled) { __atomic_store_n(&native_ast_caller_enabled, enabled == 1, __ATOMIC_RELAXED); }
void native_ast_caller_sample(unsigned cpu, uint64_t pc) {
 if (!__atomic_load_n(&native_ast_caller_enabled,__ATOMIC_RELAXED)) return;
 if (cpu >= AST_CALLER_CPUS) { __atomic_store_n(&ast_caller_bad_cpu,1U,__ATOMIC_RELAXED); return; }
 struct ast_caller_row *row=&ast_caller_rows[cpu];
 uint64_t seen=__atomic_load_n(&row->seen,__ATOMIC_RELAXED);
 __atomic_store_n(&row->seen,seen+1,__ATOMIC_RELAXED);
 uint32_t r=__atomic_load_n(&row->rng,__ATOMIC_RELAXED);
 if (!r) r=0x9e3779b9U ^ ((cpu+1U)*0x85ebca6bU);
 r ^= r << 13; r ^= r >> 17; r ^= r << 5;
 __atomic_store_n(&row->rng,r,__ATOMIC_RELAXED);
 if ((r & (AST_CALLER_RATE-1U)) != 0) return;
 if (!pc) { uint64_t n=__atomic_load_n(&row->dropped,__ATOMIC_RELAXED);__atomic_store_n(&row->dropped,n+1,__ATOMIC_RELAXED);return; }
 for (unsigned i=0;i<AST_CALLER_SLOTS;i++) {
  uint64_t key=__atomic_load_n(&row->slots[i].pc,__ATOMIC_RELAXED);
  if (!key) { __atomic_store_n(&row->slots[i].pc,pc,__ATOMIC_RELAXED); key=pc; }
  if (key==pc) { uint64_t n=__atomic_load_n(&row->slots[i].count,__ATOMIC_RELAXED);__atomic_store_n(&row->slots[i].count,n+1,__ATOMIC_RELAXED);return; }
 }
 uint64_t n=__atomic_load_n(&row->dropped,__ATOMIC_RELAXED);__atomic_store_n(&row->dropped,n+1,__ATOMIC_RELAXED);
}
// Only the one report worker updates previous counts; the IRQ path never resets them.
static uint64_t ast_caller_report_previous[AST_CALLER_CPUS][AST_CALLER_SLOTS];
void boot_ast_caller_report(void) {
 if (!__atomic_load_n(&native_ast_caller_enabled,__ATOMIC_RELAXED)) return;
 unsigned observed=__atomic_load_n(&ast_caller_bad_cpu,__ATOMIC_RELAXED);
 for (unsigned c=0;c<AST_CALLER_CPUS;c++) if (__atomic_load_n(&ast_caller_rows[c].seen,__ATOMIC_RELAXED)) observed=1;
 if (!observed) { printf("a04br3 ast caller unavailable: hook not observed (not built or no calls yet)\n"); return; }
 for (unsigned c=0;c<AST_CALLER_CPUS;c++) {
  struct ast_caller_row *row=&ast_caller_rows[c];
  printf("a04br3 ast caller cpu=%u seen=%llu dropped=%llu rate=%u (seen/dropped cumulative, sampled deltas non-atomic)\n",c,
   (unsigned long long)__atomic_load_n(&row->seen,__ATOMIC_RELAXED),(unsigned long long)__atomic_load_n(&row->dropped,__ATOMIC_RELAXED),AST_CALLER_RATE);
  struct ast_caller_slot top[4]={0}; uint64_t sampled=0;
  for (unsigned i=0;i<AST_CALLER_SLOTS;i++) {
   uint64_t pc=__atomic_load_n(&row->slots[i].pc,__ATOMIC_RELAXED);
   // If the writer publishes a new slot during this snapshot, leave its
   // previous value untouched until the PC is visible to the reporter.
   if (!pc) continue;
   uint64_t current=__atomic_load_n(&row->slots[i].count,__ATOMIC_RELAXED);
   uint64_t previous=ast_caller_report_previous[c][i];
   ast_caller_report_previous[c][i]=current;
   uint64_t n=current-previous; sampled+=n;
   if (!pc || !n) continue;
   for (unsigned j=0;j<4;j++) if (n>top[j].count) {
    for (unsigned k=3;k>j;k--) top[k]=top[k-1];
    top[j].pc=pc;top[j].count=n;break;
   }
  }
  uint64_t shown=0;
  for (unsigned i=0;i<4 && top[i].count;i++) {
   shown+=top[i].count;
   printf("a04br3 ast caller cpu=%u pc=%llx samples_delta=%llu\n",c,(unsigned long long)top[i].pc,(unsigned long long)top[i].count);
  }
  printf("a04br3 ast caller cpu=%u sampled_delta=%llu omitted_delta=%llu\n",c,(unsigned long long)sampled,(unsigned long long)(sampled-shown));
 }
 printf("a04br3 ast caller bad_cpu=%u (cumulative)\n",__atomic_load_n(&ast_caller_bad_cpu,__ATOMIC_RELAXED));
}

// Separate opt-in timer-interrupted samples; saved thread UPCBs are not used.
#define LIVE_PC_CPUS 4u
#define LIVE_PC_SLOTS 64u
#define LIVE_PC_RATE 16u
struct live_pc_row { uint64_t stamp,pc,lr,tid,uid; unsigned user,exec_version,pid; };
struct live_pc_cpu { uint64_t ticks,written; struct live_pc_row rows[LIVE_PC_SLOTS]; } __attribute__((aligned(64)));
int proc_pidversion(proc_t);
proc_t proc_find(int);
int proc_rele(proc_t);
static struct live_pc_cpu live_pc_cpus[LIVE_PC_CPUS];
static unsigned live_pc_pid,live_pc_bad_cpu;
static unsigned live_pc_auto,live_pc_target_version;
static uint64_t live_pc_target_uid,live_pc_target_seq;
static uint64_t live_pc_previous[LIVE_PC_CPUS],live_pc_epoch,live_pc_start;
extern char bootsessionuuid_string[37] __attribute__((weak_import));
void boot_live_pc_init(unsigned pid) { __atomic_store_n(&live_pc_pid,pid,__ATOMIC_RELEASE); }
void boot_live_pc_auto_init(void) { __atomic_store_n(&live_pc_auto,1,__ATOMIC_RELEASE); }
struct live_pc_selection { unsigned count,pid,version; uint64_t uid; };
// Only the process-log worker performs discovery. proc_iterate supplies a
// referenced process and can block; it must never run in the timer hook.
static int live_pc_select(proc_t p,void *arg) {
 struct live_pc_selection *s=arg;
 if(strcmp(proc_best_name(p),"backboardd"))return PROC_RETURNED;
 ++s->count;
 int pid=proc_pid(p);uint64_t uid=proc_uniqueid(p);
 if(pid>0 && uid){s->pid=(unsigned)pid;s->uid=uid;s->version=(unsigned)proc_pidversion(p);}
 return PROC_RETURNED;
}
static void live_pc_refresh_target(void) {
 struct live_pc_selection s={0};
 proc_iterate(PROC_ALLPROCLIST,live_pc_select,&s,NULL,NULL);
 // A sequence protects the entire identity. The hook makes one bounded
 // attempt and drops the sample if publication overlaps it.
 uint64_t seq=__atomic_load_n(&live_pc_target_seq,__ATOMIC_RELAXED);
 __atomic_store_n(&live_pc_target_seq,seq+1,__ATOMIC_SEQ_CST);
 __atomic_thread_fence(__ATOMIC_SEQ_CST);
 __atomic_store_n(&live_pc_pid,s.count==1 && s.uid ? s.pid : 0,__ATOMIC_RELAXED);
 __atomic_store_n(&live_pc_target_version,s.version,__ATOMIC_RELAXED);
 __atomic_store_n(&live_pc_target_uid,s.count==1 ? s.uid : 0,__ATOMIC_RELAXED);
 __atomic_store_n(&live_pc_target_seq,seq+2,__ATOMIC_RELEASE);
 printf("a04br3 livepc target name=backboardd matches=%u pid=%u uid=%llu exec_version=%u\n",s.count,s.count==1 && s.uid?s.pid:0,(unsigned long long)(s.count==1?s.uid:0),s.version);
}
// IRQs masked: the current CPU is the sole writer of its ring. Only bounded
// loads/stores are used: no retrying RMW, lock, allocation, scan, or logging.
void native_live_pc_sample(uint64_t pc,uint64_t lr,unsigned user) {
 unsigned auto_mode=__atomic_load_n(&live_pc_auto,__ATOMIC_ACQUIRE);
 uint64_t selection_seq=auto_mode?__atomic_load_n(&live_pc_target_seq,__ATOMIC_ACQUIRE):0;
 if(auto_mode && (selection_seq&1))return;
 unsigned pid=__atomic_load_n(&live_pc_pid,__ATOMIC_ACQUIRE);
 uint64_t expected=auto_mode?__atomic_load_n(&live_pc_target_uid,__ATOMIC_RELAXED):0;
 unsigned target_version=auto_mode?__atomic_load_n(&live_pc_target_version,__ATOMIC_RELAXED):0;
 if(auto_mode) {
  __atomic_thread_fence(__ATOMIC_ACQUIRE);
  if(__atomic_load_n(&live_pc_target_seq,__ATOMIC_ACQUIRE)!=selection_seq)return;
 }
 if(!pid || !pc)return;
 proc_t p=(proc_t)get_bsdtask_info(current_task());
 if(!p || proc_getpid(p)!=(int)pid)return;
 uint64_t uid=proc_uniqueid(p);unsigned version=(unsigned)proc_pidversion(p);
 if(auto_mode) {
  if(!expected || uid!=expected || version!=target_version)return;
 }
 unsigned cpu=(unsigned)cpu_number();
 if(cpu>=LIVE_PC_CPUS){__atomic_store_n(&live_pc_bad_cpu,1,__ATOMIC_RELAXED);return;}
 struct live_pc_cpu *c=&live_pc_cpus[cpu];
 uint64_t ticks=__atomic_load_n(&c->ticks,__ATOMIC_RELAXED);
 __atomic_store_n(&c->ticks,ticks+1,__ATOMIC_RELAXED);
 if(ticks%LIVE_PC_RATE)return;
 uint64_t seq=__atomic_load_n(&c->written,__ATOMIC_RELAXED);
 struct live_pc_row *r=&c->rows[seq%LIVE_PC_SLOTS];
 __atomic_store_n(&r->stamp,2*seq+1,__ATOMIC_SEQ_CST);
 __atomic_thread_fence(__ATOMIC_SEQ_CST);
 __atomic_store_n(&r->pc,pc,__ATOMIC_RELAXED);
 __atomic_store_n(&r->lr,lr,__ATOMIC_RELAXED);
 __atomic_store_n(&r->tid,current_thread()->thread_id,__ATOMIC_RELAXED);
 __atomic_store_n(&r->uid,uid,__ATOMIC_RELAXED);
 __atomic_store_n(&r->exec_version,version,__ATOMIC_RELAXED);
 __atomic_store_n(&r->pid,pid,__ATOMIC_RELAXED);
 __atomic_store_n(&r->user,user,__ATOMIC_RELAXED);
 __atomic_store_n(&r->stamp,2*seq+2,__ATOMIC_RELEASE);
 __atomic_store_n(&c->written,seq+1,__ATOMIC_RELEASE);
}
static void live_pc_image_report(unsigned pid,uint64_t epoch,uint64_t expected_uid,unsigned expected_version);
void boot_live_pc_report(void) {
 unsigned auto_mode=__atomic_load_n(&live_pc_auto,__ATOMIC_ACQUIRE);
 if(auto_mode)live_pc_refresh_target();
 unsigned pid=__atomic_load_n(&live_pc_pid,__ATOMIC_ACQUIRE);
 if(!pid && !auto_mode)return;
 uint64_t target_uid=auto_mode?__atomic_load_n(&live_pc_target_uid,__ATOMIC_ACQUIRE):0;
 unsigned target_version=auto_mode?__atomic_load_n(&live_pc_target_version,__ATOMIC_RELAXED):0;
 uint64_t now=mach_absolute_time(),start=live_pc_start;live_pc_start=now;
 uint64_t epoch=++live_pc_epoch,ns=0,start_ns=0;
 absolutetime_to_nanoseconds(now,&ns);if(start)absolutetime_to_nanoseconds(start,&start_ns);
 const char *boot=(bootsessionuuid_string && bootsessionuuid_string[0])?bootsessionuuid_string:"unavailable";
 printf("a04br3 livepc boot=%s pid=%u epoch=%llu previous_report_start_ns=%llu report_start_ns=%llu sample_time=unavailable hook=%u rate=%u bad_cpu=%u\n",boot,pid,(unsigned long long)epoch,(unsigned long long)start_ns,(unsigned long long)ns,boot_home_irq_hook_available(),LIVE_PC_RATE,__atomic_load_n(&live_pc_bad_cpu,__ATOMIC_RELAXED));
 for(unsigned cpu=0;cpu<LIVE_PC_CPUS;cpu++) {
  struct live_pc_cpu *c=&live_pc_cpus[cpu];
  uint64_t end=__atomic_load_n(&c->written,__ATOMIC_ACQUIRE),begin=live_pc_previous[cpu],lost=0,torn=0;
  if(end-begin>LIVE_PC_SLOTS){lost=end-begin-LIVE_PC_SLOTS;begin=end-LIVE_PC_SLOTS;}
  live_pc_previous[cpu]=end;
  for(uint64_t seq=begin;seq<end;seq++) {
   struct live_pc_row *r=&c->rows[seq%LIVE_PC_SLOTS];
   uint64_t stamp=__atomic_load_n(&r->stamp,__ATOMIC_ACQUIRE);
   uint64_t pc=__atomic_load_n(&r->pc,__ATOMIC_RELAXED),lr=__atomic_load_n(&r->lr,__ATOMIC_RELAXED);
   uint64_t tid=__atomic_load_n(&r->tid,__ATOMIC_RELAXED),uid=__atomic_load_n(&r->uid,__ATOMIC_RELAXED);
   unsigned user=__atomic_load_n(&r->user,__ATOMIC_RELAXED),version=__atomic_load_n(&r->exec_version,__ATOMIC_RELAXED),sample_pid=__atomic_load_n(&r->pid,__ATOMIC_RELAXED);
   __atomic_thread_fence(__ATOMIC_ACQUIRE);
   if(stamp!=2*seq+2 || __atomic_load_n(&r->stamp,__ATOMIC_ACQUIRE)!=stamp){torn++;continue;}
   printf("a04br3 livepc sample epoch=%llu cpu=%u seq=%llu pid=%u uid=%llu exec_version=%u tid=%llu mode=%s pc=%llx lr=%llx\n",(unsigned long long)epoch,cpu,(unsigned long long)seq,sample_pid,(unsigned long long)uid,version,(unsigned long long)tid,user?"user":"kernel-unslid",(unsigned long long)pc,(unsigned long long)lr);
  }
  printf("a04br3 livepc cpu=%u epoch=%llu ticks=%llu end=%llu lost=%llu torn=%llu\n",cpu,(unsigned long long)epoch,(unsigned long long)__atomic_load_n(&c->ticks,__ATOMIC_RELAXED),(unsigned long long)end,(unsigned long long)lost,(unsigned long long)torn);
 }
 if(pid)live_pc_image_report(pid,epoch,target_uid,target_version);
}

// dyld_all_image_infos prefix and dyld_image_info are the 64-bit ABI. Only
// bounded reads from a referenced map; this worker may block on page faults.
#define LIVE_IMAGES_MAX 128u
struct live_image_info { uint64_t addr,path,mod; };
struct live_info_head { uint32_t version,count; uint64_t array,notify; uint8_t detached,initialized,pad[6]; uint64_t dyld; };
struct live_macho_head { uint32_t magic,cputype,subtype,filetype,ncmds,sizeofcmds,flags,reserved; };
struct live_image_row { uint64_t addr,text_vm,text_size; uint8_t uuid[16]; unsigned valid,uuid_valid,text_valid,complete; };
static struct live_image_row live_image_rows[LIVE_IMAGES_MAX+1];
static struct live_image_info live_image_entries[LIVE_IMAGES_MAX];
static int live_read(vm_map_t map,uint64_t addr,void *buf,unsigned len) {
 if(!addr || addr+len<addr)return -1;
 return vm_map_read_user(map,addr,buf,len)==KERN_SUCCESS ? 0 : -1;
}
static int live_image_entry_address(uint64_t base,unsigned index,uint64_t *out) {
 uint64_t offset=(uint64_t)index*sizeof(struct live_image_info);
 if(base>UINT64_MAX-offset)return -1;
 *out=base+offset;return 0;
}
static void live_image_decode(vm_map_t map,uint64_t addr,struct live_image_row *row) {
 struct live_macho_head h={0};row->addr=addr;
 if(live_read(map,addr,&h,sizeof(h)) || h.magic!=0xfeedfacf || h.ncmds>1024 || h.sizeofcmds>65536)return;
 row->valid=1;uint64_t off=sizeof(h),end=off+h.sizeofcmds;
 for(unsigned i=0;i<h.ncmds;i++) {
  if(off+8>end || addr+off<addr || addr+off+8<addr+off)return;
  uint32_t c[2];if(live_read(map,addr+off,c,sizeof(c)) || c[1]<8 || off+c[1]>end)return;
  if(c[0]==0x1b) {
   if(c[1]<24 || live_read(map,addr+off+8,row->uuid,16))return;
   row->uuid_valid=1;
  } else if(c[0]==0x19) {
   struct {uint32_t cmd,size;char name[16];uint64_t vmaddr,vmsize;} segment;
   if(c[1]<72 || live_read(map,addr+off,&segment,sizeof(segment)))return;
   if(!memcmp(segment.name,"__TEXT\0",7)) {
    row->text_vm=segment.vmaddr;row->text_size=segment.vmsize;row->text_valid=1;
   }
  }
  off+=c[1];
 }
 if(off==end)row->complete=1;
}
// Follow XNU's task_for_pid pattern: proc_find supplies an exec-stable proc
// reference; obtain a task reference while it is held, then release the proc
// before user memory reads. Re-find and compare identity afterwards.
static void live_pc_image_report(unsigned pid,uint64_t epoch,uint64_t expected_uid,unsigned expected_version) {
 uint64_t start=mach_absolute_time(),start_ns=0,end_ns=0;
 proc_t p=proc_find((int)pid);
 if(!p){printf("a04br3 livepc images epoch=%llu status=failed reason=proc_unavailable\n",(unsigned long long)epoch);return;}
 uint64_t uid=proc_uniqueid(p);unsigned exec_version=(unsigned)proc_pidversion(p);
 if(expected_uid && (expected_uid!=uid || expected_version!=exec_version)){proc_rele(p);printf("a04br3 livepc images epoch=%llu status=changed reason=target_reused_or_exec\n",(unsigned long long)epoch);return;}
 task_t task=proc_task(p);
 if(!uid || !task){proc_rele(p);printf("a04br3 livepc images epoch=%llu status=failed reason=task_unavailable\n",(unsigned long long)epoch);return;}
 task_reference(task);
 proc_rele(p);
 if(!task_lock_try(task)){task_deallocate(task);printf("a04br3 livepc images epoch=%llu status=failed reason=task_busy\n",(unsigned long long)epoch);return;}
 vm_map_t map=task->map;uint64_t info=task_get_all_image_info_addr(task);unsigned slide=task->task_shared_region_slide;
 if(map)vm_map_reference(map);
 task_unlock(task);
 struct live_info_head first={0},last={0};unsigned read_count=0,failed=0,details_failed=0,changed=0;
 memset(live_image_rows,0,sizeof(live_image_rows));
 if(!map || live_read(map,info,&first,sizeof(first)) || !first.array || first.count>4096)failed=1;
 if(!failed) {
  unsigned limit=first.count<LIVE_IMAGES_MAX?first.count:LIVE_IMAGES_MAX;
  for(unsigned i=0;i<limit;i++) {
   struct live_image_info image;uint64_t entry_addr;
   if(live_image_entry_address(first.array,i,&entry_addr) || live_read(map,entry_addr,&image,sizeof(image))){failed=1;break;}
   live_image_entries[i]=image;live_image_decode(map,image.addr,&live_image_rows[i]);
   if(!live_image_rows[i].complete)details_failed++;
   read_count++;
  }
  if(!failed && first.dyld){live_image_decode(map,first.dyld,&live_image_rows[read_count]);if(!live_image_rows[read_count].complete)details_failed++;}
  for(unsigned i=0;i<read_count && !failed;i++) {
   struct live_image_info check;uint64_t entry_addr;
   if(live_image_entry_address(first.array,i,&entry_addr) || live_read(map,entry_addr,&check,sizeof(check)))failed=1;
   else if(memcmp(&check,&live_image_entries[i],sizeof(check)))changed=1;
  }
  if(live_read(map,info,&last,sizeof(last)))failed=1;
  else if(memcmp(&first,&last,sizeof(first)))changed=1;
 }
 p=proc_find((int)pid);
 if(!p)changed=1;
 else {if(proc_uniqueid(p)!=uid || (unsigned)proc_pidversion(p)!=exec_version || proc_task(p)!=task)changed=1;proc_rele(p);}
 if(!task_lock_try(task))changed=1;
 else {if(task->map!=map || task_get_all_image_info_addr(task)!=info || task->task_shared_region_slide!=slide)changed=1;task_unlock(task);}
 if(map)vm_map_deallocate(map);
 task_deallocate(task);
 absolutetime_to_nanoseconds(start,&start_ns);absolutetime_to_nanoseconds(mach_absolute_time(),&end_ns);
 const char *status=failed?"failed":changed?"changed":(first.count>LIVE_IMAGES_MAX || details_failed)?"partial":"complete";
 printf("a04br3 livepc images epoch=%llu pid=%u uid=%llu exec_version=%u start_ns=%llu end_ns=%llu info=%llx cache_slide=%x cache_slide_valid=%u dyld_version=%u count=%u captured=%u truncated=%u detail_incomplete=%u status=%s snapshot=nonatomic mapping_at_sample=unknown\n",(unsigned long long)epoch,pid,(unsigned long long)uid,exec_version,(unsigned long long)start_ns,(unsigned long long)end_ns,(unsigned long long)info,slide,slide!=0xffffffffU,first.version,first.count,read_count,first.count>LIVE_IMAGES_MAX,details_failed,status);
 if(!failed && !changed) for(unsigned i=0;i<read_count+!!first.dyld;i++) {
  struct live_image_row *r=&live_image_rows[i];
  if(!r->complete){r->uuid_valid=0;r->text_valid=0;r->text_vm=0;r->text_size=0;memset(r->uuid,0,sizeof(r->uuid));}
  printf("a04br3 livepc image epoch=%llu index=%u dyld=%u mapping_at_sample=unknown load=%llx text_vm=%llx text_size=%llx text_valid=%u macho=%u detail_complete=%u uuid_valid=%u uuid=%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x\n",(unsigned long long)epoch,i,i==read_count,(unsigned long long)r->addr,(unsigned long long)r->text_vm,(unsigned long long)r->text_size,r->text_valid,r->valid,r->complete,r->uuid_valid,r->uuid[0],r->uuid[1],r->uuid[2],r->uuid[3],r->uuid[4],r->uuid[5],r->uuid[6],r->uuid[7],r->uuid[8],r->uuid[9],r->uuid[10],r->uuid[11],r->uuid[12],r->uuid[13],r->uuid[14],r->uuid[15]);
 }
}
