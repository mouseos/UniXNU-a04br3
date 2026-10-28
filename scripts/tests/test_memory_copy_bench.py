#!/usr/bin/env python3
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

class MemoryCopyBenchTest(unittest.TestCase):
    def test_explicit_run_and_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = pathlib.Path(tmp) / 'bench'
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(ROOT / 'tools/a04br3-memory-copy-bench.c'), '-o', str(binary)], check=True)
            for args in [[], ['--run', '0'], ['--run', '65'], ['--run', '-1'], ['--run', '1x'], ['1']]:
                self.assertEqual(subprocess.run([str(binary), *args], capture_output=True).returncode, 2)
            result = subprocess.run([str(binary), '--run', '1'], capture_output=True, text=True, check=True)
            row = dict(item.split('=', 1) for item in result.stdout.splitlines()[-1].split()[1:])
            self.assertEqual(row['synthetic'], '1')
            self.assertEqual(row['verified'], '1')
            self.assertEqual(int(row['logical_bytes']), 1048576)
            self.assertEqual(int(row['elapsed_ns']), int(row['end_monotonic_ns']) - int(row['start_monotonic_ns']))
            self.assertGreater(int(row['elapsed_ns']), 0)

    def test_boot_stages_and_memory_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / 'mach').mkdir()
            (root / 'mach/mach.h').write_text(r"""
#include <stdint.h>
typedef int mach_port_t; typedef unsigned long vm_size_t;
typedef unsigned mach_msg_type_number_t; typedef int kern_return_t;
typedef struct { unsigned free_count, speculative_count, purgeable_count;
 uint64_t pageins,pageouts,reactivations,faults,purges,compressions,decompressions,swapins,swapouts;
} vm_statistics64_data_t;
typedef void *host_info64_t;
#define HOST_VM_INFO64_REV0_COUNT 1
#define HOST_VM_INFO64_REV1_COUNT 2
#define HOST_VM_INFO64_COUNT 2
#define HOST_VM_INFO64 1
#define KERN_SUCCESS 0
mach_port_t mach_host_self(void); mach_port_t mach_task_self(void);
kern_return_t host_page_size(mach_port_t, vm_size_t *);
kern_return_t host_statistics64(mach_port_t, int, host_info64_t, mach_msg_type_number_t *);
kern_return_t mach_port_deallocate(mach_port_t, mach_port_t);
""")
            harness = root / 'harness.c'
            harness.write_text(r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned waits[32], nw, calls; static int scenario;
static FILE *fake_freopen(const char *p,const char *m,FILE *f) {(void)p;(void)m;return f;}
static unsigned fake_sleep(unsigned n) {waits[nw++]=n; return 0;}
#define freopen fake_freopen
#define sleep fake_sleep
#define main bench_main
#include "SOURCE_PATH"
#undef main
mach_port_t mach_host_self(void) {return 1;}
mach_port_t mach_task_self(void) {return 2;}
kern_return_t mach_port_deallocate(mach_port_t p,mach_port_t q) {(void)p;(void)q;return 0;}
kern_return_t host_page_size(mach_port_t p,vm_size_t *n) {(void)p;*n=4096;return 0;}
kern_return_t host_statistics64(mach_port_t p,int f,host_info64_t out,mach_msg_type_number_t *n) {
 (void)p;(void)f; ++calls;
 if (scenario==3) return 1;
 unsigned pages = scenario==1 ? 8703 : scenario==2 && calls<=2 ? 10239 : scenario>=4 ? 11264 : 10240;
 unsigned spec = scenario==4 ? 1024 : scenario==5 ? 1025 : scenario==6 ? 11265 : 0;
 ((vm_statistics64_data_t *)out)->free_count=pages;
 ((vm_statistics64_data_t *)out)->speculative_count=spec;
 vm_statistics64_data_t *st=(vm_statistics64_data_t *)out;
 st->pageins=11;st->pageouts=12;st->reactivations=13;st->faults=14;st->purgeable_count=15;st->purges=16;
 st->compressions=17;st->decompressions=18;st->swapins=19;st->swapouts=20;
 if(scenario==7)*n=HOST_VM_INFO64_REV0_COUNT;
 if(scenario==8)*n=0;
 return 0;
}
int main(int argc,char **argv) {
 scenario=argc>1 ? atoi(argv[1]) : 0;
 char *args[]={"bench","--boot-run",0}; int result=bench_main(2,args);
 fprintf(stderr,"test waits=%u first=%u second=%u calls=%u result=%d\n",nw,waits[0],waits[1],calls,result);
 return result;
}
""".replace('SOURCE_PATH', str(ROOT / 'tools/a04br3-memory-copy-bench.c')))
            binary = root / 'bench'
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-D_POSIX_C_SOURCE=200809L', '-D__APPLE__', '-I', str(root), str(harness), '-o', str(binary)], check=True)
            expected = [(0, 0, 6, 6), (1, 1, 6, 0), (2, 0, 8, 6),
                        (3, 1, 1, 0), (4, 0, 6, 6), (5, 0, 6, 3), (6, 1, 1, 0),
                        (7, 0, 6, 6), (8, 1, 1, 0)]
            for scenario, rc, calls, results in expected:
                result = subprocess.run([str(binary), str(scenario)], text=True, capture_output=True)
                self.assertEqual(result.returncode, rc, result.stderr)
                rows = re.findall(r'logical_bytes=.*verified=1', result.stdout)
                self.assertEqual(len(rows), results, result.stdout)
                self.assertIn(f'calls={calls} result={rc}', result.stderr)
                self.assertIn('first=180', result.stderr)
                if scenario in (0, 7):
                    self.assertIn('page_size=4096 pageins=11 pageouts=12 reactivations=13 faults=14 purgeable_pages=15 purges=16 non_atomic=1', result.stdout)
                if scenario == 0:
                    self.assertIn('phase=vm_activity_extended compressions=17 decompressions=18 swapins=19 swapouts=20 non_atomic=1', result.stdout)
                if scenario == 7:
                    self.assertIn('phase=vm_activity_extended status=unavailable', result.stdout)
                if scenario == 8:
                    self.assertIn('reason=vm_statistics_unavailable', result.stderr)
                if scenario == 0:
                    self.assertEqual(result.stdout.count('phase=recovery delay_seconds=10'), 5)
                    self.assertEqual(result.stdout.count('phase=sample size_mib=4'), 3)
                    self.assertEqual(result.stdout.count('phase=sample size_mib=1'), 3)
                    self.assertIn('phase=complete samples=6 incomplete_sizes=0', result.stdout)
                if scenario == 1:
                    self.assertIn('phase=complete samples=0 incomplete_sizes=2', result.stdout)
                if scenario == 2:
                    self.assertEqual(result.stdout.count('phase=retry'), 2)
                if scenario == 3:
                    self.assertIn('reason=vm_statistics_unavailable', result.stderr)
                if scenario == 5:
                    self.assertEqual(result.stdout.count('phase=retry'), 2)
                    self.assertEqual(result.stdout.count('size_bytes=1048576 start_monotonic_ns='), 3)
                if scenario == 6:
                    self.assertIn('reason=vm_statistics_inconsistent', result.stderr)

if __name__ == '__main__':
    unittest.main()
