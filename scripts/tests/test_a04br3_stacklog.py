"""The real heartbeat must not collect UI stacks unless explicitly enabled."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'package/A04BR3Board.cpp'


class StacklogTest(unittest.TestCase):
    def test_main_register_snapshot_and_bounded_rows(self):
        package = SOURCE.parent
        probe = (package / 'BootUiTraceProbe.c').read_text()
        diag = probe[probe.index('struct bt_main_diag {'):
                     probe.index('static struct bt_main draft_main')]
        render = probe[probe.index('unsigned boot_ui_trace_role_rows('):
                       probe.index('// The hottest threads')]
        harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
/* Kernel uint64_t is unsigned long long; host libc may use unsigned long.
 * Keep the real rendering body and its kernel formats through this wrapper. */
static int row_printf(char *p, size_t n, const char *fmt, ...) {
 va_list ap; va_start(ap,fmt); int r=vsnprintf(p,n,fmt,ap); va_end(ap); return r;
}
#define snprintf row_printf
#include "BootUiTraceCore.h"
#include "BootUiTraceDetails.h"
#define BT_KFRAMES 12
#define BT_ROLES 4
typedef struct {uint64_t r[29];} arm_saved_state_t;
typedef struct fake_thread {uint64_t thread_id,wait_event;unsigned state,block_hint,c_switch;int sched_pri;} *thread_t;
static uint64_t get_saved_state_reg(arm_saved_state_t *s,unsigned n) {assert(n<29);return s->r[n];}
''' + diag + r'''
static struct bt_main published_main[4];
static struct bt_groups published;
static unsigned trace_lock,published_pid[4],published_slide[4];
static char published_names[4][32];
''' + render + r'''
int main(void) {
 arm_saved_state_t s={0};for(unsigned i=0;i<29;++i)s.r[i]=0xffff000000000000ULL+i;
 struct fake_thread t={.thread_id=0x1234,.wait_event=0x5678,.state=4,.block_hint=9,.c_switch=71,.sched_pri=37};
 uint64_t kernel[12];for(unsigned i=0;i<12;++i)kernel[i]=t.thread_id+i;
 bt_capture_main_diag(&draft_diag[1],&t,&s,kernel);
 const unsigned wanted[6]={8,9,10,19,20,21};
 for(unsigned i=0;i<6;++i)assert(draft_diag[1].regs[i]==s.r[wanted[i]]);
 assert(draft_diag[1].event==0x5678 && draft_diag[1].cs==71 && draft_diag[1].state==4);
 memcpy(published_diag,draft_diag,sizeof(draft_diag));
 memset(&s,0,sizeof(s));memset(draft_diag,0,sizeof(draft_diag));
 published_main[1].pid=27;
 for(unsigned cap=0;cap<=16;++cap) {
  char rows[18][120];memset(rows,0x5a,sizeof(rows));
  unsigned n=boot_ui_trace_role_rows(1,rows,cap,1);assert(n<=cap);
  for(unsigned i=cap;i<18;++i)for(unsigned j=0;j<120;++j)assert(rows[i][j]==0x5a);
  if(cap>=11) {
   assert(n==11 && strstr(rows[5],"x9=ffff000000000009") && strstr(rows[6],"x21=ffff000000000015"));
   assert(strstr(rows[7],"st=4 hint=9 pri=37 ev=5678 cs=71"));
   assert(strstr(rows[10],"123c 123d 123e 123f"));
  }
 }
 char rows[16][120];memset(rows,0x5a,sizeof(rows));
 trace_lock=1;assert(!boot_ui_trace_role_rows(1,rows,16,1));assert(rows[0][0]==0x5a);
 trace_lock=0;published_pid[1]=27;
 assert(boot_ui_trace_role_rows(1,rows,16,0)==1 && !strstr(rows[0],"REG"));
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='a04br3-stack-regs-') as tmp:
            c, binary = Path(tmp) / 'regs.c', Path(tmp) / 'regs'
            c.write_text(harness)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra',
                            '-Wno-unused-function', '-Werror',
                            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            '-isystem', str(package), str(c), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_capture_is_opt_in(self):
        source = SOURCE.read_text()
        report = source[source.index('static void a04br3_thread_report(void)'):
                        source.index('\nextern "C" void a04br3_display_report_set')]
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
volatile uint32_t armcpu_smp_requested, armcpu_smp_running, armcpu_smp_step[8];
volatile int32_t armcpu_smp_psci_rc[8];
char boot_process_rows[10][120];
unsigned enabled, captures, snapshots, traces;
void IOLog(const char *, ...) {}
bool PE_parse_boot_argn(const char *name, void *value, unsigned size) {
    assert(!strcmp(name, "a04br3-stacklog") && size == sizeof(unsigned));
    *static_cast<unsigned *>(value) = enabled; return enabled != 0;
}
bool a04br3_row_absent(const char *) { return false; }
void boot_process_capture() { ++captures; }
void boot_process_snapshot() { ++snapshots; }
void boot_memory_snapshot(uint64_t *out) { for (unsigned i=0;i<7;++i) out[i]=0; }
unsigned boot_ui_trace_role_rows(unsigned, char [][120], unsigned, unsigned) { ++traces; return 0; }
''' + report + r'''
int main(int argc, char **argv) {
    assert(argc == 2); enabled = argv[1][0] == '1';
    a04br3_thread_report(); a04br3_thread_report();
    assert(captures == enabled * 2 && snapshots == enabled * 2 && traces == enabled * 8);
}
'''
        with tempfile.TemporaryDirectory(prefix='a04br3-stacklog-test-') as tmp:
            cpp, binary = Path(tmp) / 'report.cpp', Path(tmp) / 'report'
            cpp.write_text(harness)
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(binary)], check=True)
            for enabled in ('0', '1'):
                subprocess.run([str(binary), enabled], check=True)


if __name__ == '__main__':
    unittest.main()
