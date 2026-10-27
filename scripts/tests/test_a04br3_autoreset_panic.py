"""Exercise the production panic latch, callback and reset register sequence."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'package/A04BR3AutoReset.cpp'


class PanicWatchdogTest(unittest.TestCase):
    def test_actual_functions(self):
        source = SOURCE.read_text()
        block = source.split('// BEGIN panic-aware watchdog')[1].split('// END panic-aware watchdog')[0]
        block = block[block.index('\n'):]
        halt = 'for (;;) { __asm__ volatile("wfi"); }'
        self.assertEqual(block.count(halt), 1)
        block = block.replace(halt, 'throw ResetStopped{};')
        prelude = r'''
#include <cassert>
#include <cstdint>
#include <vector>
#include <utility>
struct ResetStopped {};
enum { kPEHaltCPU=0, kPERestartCPU=1, kPEHangCPU=2,
 kPEUPSDelayHaltCPU=3, kPEPanicRestartCPU=4, kPEPanicSync=5,
 kPEPagingOff=6, kPEPanicBegin=7, kPEPanicEnd=8,
 kPEPanicRestartCPUNoCallouts=9, kPEPanicDiagnosticsDone=10,
 kPEPanicDiagnosticsInProgress=11 };
unsigned active, delays, previousCalls;
volatile uint32_t a04br3_runtime_probe_active;
unsigned panic_active() { return active; }
uint8_t dummy;
volatile uint8_t *gToprgu = &dummy;
constexpr unsigned kWdtRestart=8, kWdtMode=0, kWdtSwrst=0x14;
uint32_t mode = 0x22000015;
std::vector<std::pair<unsigned, uint32_t>> writes;
uint32_t rd(volatile uint8_t *, unsigned reg) { assert(reg==kWdtMode); return mode; }
void wr(volatile uint8_t *p, unsigned reg, uint32_t value) {
 assert(p); writes.emplace_back(reg,value);
}
void IODelay(unsigned n) { assert(n==100); ++delays; }
int (*PE_halt_restart)(unsigned);
'''
        tests = r'''
int previous(unsigned type) {
 ++previousCalls;
 if (type >= 7 && type <= 11) assert(gPanicLatched);
 return 123;
}
void clear() {
 gPanicLatched=0; active=0; a04br3_runtime_probe_active=0; writes.clear(); delays=0;
 gToprgu=&dummy; gPreviousHaltRestart=previous;
}
void expectReset(unsigned type, bool enabled) {
 writes.clear(); delays=0;
 try { boardHaltRestart(type); assert(false); } catch (ResetStopped&) {}
 assert(writes.size()==3 && delays==1);
 assert(writes[0].first==kWdtRestart && writes[0].second==0x1971);
 assert(writes[1].first==kWdtMode);
 assert(writes[1].second==((mode & 0x00ffffa6U)|0x22000014U|(enabled?1U:0U)));
 assert(writes[2].first==kWdtSwrst && writes[2].second==0x1209);
}
int main() {
 clear(); PE_halt_restart=previous; installHaltRestart();
 assert(PE_halt_restart==boardHaltRestart && gPreviousHaltRestart==previous);
 installHaltRestart(); assert(gPreviousHaltRestart==previous);
 watchdogKick(); assert(writes.size()==1); writes.clear();
 a04br3_runtime_probe_active=1; watchdogKick(); assert(writes.empty());
 a04br3_runtime_probe_active=0; watchdogKick(); assert(writes.size()==1); writes.clear();
 for (unsigned type : {7U,8U,10U,11U}) {
  clear(); assert(boardHaltRestart(type)==123);
  assert(gPanicLatched && writes.empty() && !delays);
  watchdogKick(); active=0; watchdogKick(); assert(writes.empty());
 }
 clear(); active=1; watchdogKick(); active=0; watchdogKick();
 assert(gPanicLatched && writes.empty());
 clear(); expectReset(kPEPanicRestartCPU,true);
 watchdogKick(); assert(writes.size()==3);
 clear(); expectReset(kPEPanicRestartCPUNoCallouts,true);
 clear(); expectReset(kPERestartCPU,false);
 clear(); boardHaltRestart(kPEPanicBegin); expectReset(kPERestartCPU,true);
 for (unsigned type : {0U,2U,3U,5U,6U,99U}) {
  clear(); assert(boardHaltRestart(type)==123);
  assert(!gPanicLatched && writes.empty() && !delays);
 }
 clear(); gPreviousHaltRestart=nullptr;
 assert(boardHaltRestart(kPEPanicBegin)==-1 && gPanicLatched);
 clear(); gToprgu=nullptr; assert(boardHaltRestart(kPEPanicRestartCPU)==123);
 assert(gPanicLatched && writes.empty());
}
'''
        with tempfile.TemporaryDirectory(prefix='a04br3-panic-test-') as tmp:
            cpp = Path(tmp) / 'panic.cpp'
            binary = Path(tmp) / 'panic'
            cpp.write_text(prelude + block + tests)
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            '-fno-pie', '-no-pie', str(cpp), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_opt_in_and_shared_kick(self):
        source = SOURCE.read_text()
        init = source.split('extern "C" void a04br3_autoreset_init(void)')[1]
        self.assertLess(init.index('seconds == 0'), init.index('ml_io_map'))
        self.assertLess(init.index('watchdogArm('), init.index('installHaltRestart();'))
        self.assertLess(init.index('installHaltRestart();'), init.index('kernel_thread_start('))
        shot = source.split('void sendShot(')[1].split('void cycleThread')[0]
        cycle = source.split('void cycleThread')[1].split('}  // namespace')[0]
        self.assertIn('watchdogKick();', shot)
        self.assertIn('watchdogKick();', cycle)
        self.assertIn('resetRequested();', cycle)
        self.assertNotIn('kWdtRestart', shot + cycle)


if __name__ == '__main__':
    unittest.main()
