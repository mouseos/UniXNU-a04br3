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
typedef struct { unsigned free_count; } vm_statistics64_data_t;
typedef void *host_info64_t;
#define HOST_VM_INFO64_COUNT 1
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
static unsigned waits[4], nw, calls; static int scenario;
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
 (void)p;(void)f;(void)n; ++calls;
 if (scenario==3) return 1;
 unsigned pages= scenario==1 ? 8703 : scenario==2 ? (calls==1 ? 8704 : 10239) : 10240;
 ((vm_statistics64_data_t *)out)->free_count=pages; return 0;
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
            expected = [(0, 0, 2, 2), (1, 1, 1, 0), (2, 1, 2, 1), (3, 1, 1, 0)]
            for scenario, rc, calls, results in expected:
                result = subprocess.run([str(binary), str(scenario)], text=True, capture_output=True)
                self.assertEqual(result.returncode, rc, result.stderr)
                self.assertEqual(len(re.findall(r'logical_bytes=.*verified=1', result.stdout)), results)
                self.assertIn(f'calls={calls} result={rc}', result.stderr)
                if calls == 2:
                    self.assertIn('waits=2 first=180 second=10', result.stderr)
                else:
                    self.assertIn('waits=1 first=180', result.stderr)
                if scenario == 2:
                    self.assertIn('reason=insufficient_free_pages', result.stderr)
                if scenario == 3:
                    self.assertIn('reason=vm_statistics_unavailable', result.stderr)

if __name__ == '__main__':
    unittest.main()
