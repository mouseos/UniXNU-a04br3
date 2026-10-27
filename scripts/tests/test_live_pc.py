#!/usr/bin/env python3
import pathlib, subprocess, tempfile, unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

class LivePcTest(unittest.TestCase):
    def test_real_diagnostic_with_host_stubs(self):
        source = (ROOT / 'package/BootProcessProbe.c').read_text()
        code = source[source.index('// Separate opt-in timer-interrupted samples;'):]
        prefix = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <stdarg.h>
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
static struct thread thread={99};

static uint64_t image_info_address;
static uint64_t mutate_address; static int mutate_on_read, mutate_reads;
static int proc_getpid(proc_t p){return p->pid;}
static int proc_pid(proc_t p){return p->pid;}
static uint64_t proc_uniqueid(proc_t p){return p->uid;}
static int proc_pidversion(proc_t p){return p->version;}
static task_t proc_task(proc_t p){return &p->task;}
static task_t current_task(void){return &process.task;}
static struct thread *current_thread(void){return &thread;}
static void *get_bsdtask_info(task_t t){(void)t;return &process;}
static unsigned boot_home_irq_hook_available(void){return 1;}
static uint64_t mach_absolute_time(void){static uint64_t n;return ++n;}
static void absolutetime_to_nanoseconds(uint64_t x,uint64_t *out){*out=x;}
static int task_lock_try(task_t t){(void)t;return 1;}
static void task_unlock(task_t t){(void)t;}
static void task_reference(task_t t){(void)t;}
static void task_deallocate(task_t t){(void)t;}
static uint64_t task_get_all_image_info_addr(task_t t){(void)t;return image_info_address;}
static void vm_map_reference(vm_map_t m){(void)m;}
static void vm_map_deallocate(vm_map_t m){(void)m;}
static int vm_map_read_user(vm_map_t m,uint64_t a,void *p,unsigned n){(void)m;memcpy(p,(void *)(uintptr_t)a,n);if(a==mutate_address && ++mutate_reads==mutate_on_read)((uint8_t *)p)[0]^=1;return 0;}
static void proc_iterate(unsigned f,int (*fn)(proc_t,void *),void *a,int (*sel)(proc_t,void *),void *b){(void)f;if(sel(&process,b))fn(&process,a);}
'''
        suffix = r'''
char bootsessionuuid_string[37]="test-boot";
int main(void){
 boot_live_pc_init(0);native_live_pc_sample(0x1004,0x1000,1);assert(live_pc_seen==0);
 boot_live_pc_init(1);for(int i=0;i<16;i++)native_live_pc_sample(0x1004,0x1000,1);
 assert(live_pc_seen==16 && live_pc_rows[0].pc==0x1004 && live_pc_rows[0].lr==0x1000 && live_pc_rows[0].tid==99 && live_pc_rows[0].version==7);
 process.version=8;native_live_pc_sample(0x2000,0,0);assert(live_pc_rows[1].version==8 && !live_pc_rows[1].user);
 process.uid=43;for(int i=0;i<16;i++)native_live_pc_sample(0x3000,0,0);assert(live_pc_other>0);process.uid=42;
 for(int k=0;k<140;k++)for(int i=0;i<16;i++)native_live_pc_sample(0x4000+(uint64_t)k*4,0,0);
 assert(live_pc_full>0);live_pc_lock=1;for(int i=0;i<16;i++)native_live_pc_sample(0x4000,0,0);live_pc_lock=0;assert(live_pc_busy>0);
 uint8_t macho[160]={0};struct live_macho_head *h=(void *)macho;
 h->magic=0xfeedfacf;h->ncmds=2;h->sizeofcmds=96;
 uint32_t *u=(void *)(macho+32);u[0]=0x1b;u[1]=24;memset(macho+40,0x5a,16);
 uint32_t *seg=(void *)(macho+56);seg[0]=0x19;seg[1]=72;memcpy(macho+64,"__TEXT",6);uint64_t vm=0x100000000;memcpy(macho+80,&vm,8);
 struct live_image_row row={0};live_image_decode((void *)1,(uintptr_t)macho,&row);
 assert(row.valid && row.uuid_valid && row.uuid[0]==0x5a && row.text_valid && row.text_vm==vm);
 struct live_image_info entry={(uintptr_t)macho,0,0};
 struct live_info_head info={15,1,(uintptr_t)&entry,0,0,1,{0},0};
 image_info_address=(uintptr_t)&info;struct live_image_request req={1,1,0};
 live_image_capture(&process,&req);assert(req.seen);
 mutate_address=(uintptr_t)&entry;mutate_on_read=2;mutate_reads=0;
 req.epoch=2;live_image_capture(&process,&req);
 h->sizeofcmds=65537;memset(&row,0,sizeof(row));live_image_decode((void *)1,(uintptr_t)macho,&row);assert(!row.valid);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as td:
            c = pathlib.Path(td) / 'test.c'; binary = pathlib.Path(td) / 'test'
            c.write_text(prefix + code + suffix)
            subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unknown-attributes', str(c), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn("epoch=1 pid=1 uid=42 version=8", result.stdout)
            self.assertIn("headers_entries_unchanged=1", result.stdout)
            self.assertIn("headers_entries_unchanged=0", result.stdout)
            self.assertEqual(result.stdout.count("a04br3 livepc image epoch="), 1)

if __name__ == '__main__': unittest.main()
