"""The real heartbeat must not collect UI stacks unless explicitly enabled."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'package/A04BR3Board.cpp'


class StacklogTest(unittest.TestCase):
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
