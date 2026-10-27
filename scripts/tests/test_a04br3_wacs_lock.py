#!/usr/bin/env python3
"""ホストで本番ロックの所有権と全WACS2呼出元を検証する。"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
code = r'''
#include <atomic>
#include <cassert>
#include <mutex>
#include <thread>
#include <vector>
#include "A04BR3RegisterLock.cpp"
int allocations;
IOLock *IOLockAlloc(){ ++allocations; return new IOLock; }
void IOLockLock(IOLock *p){p->lock();}
bool IOLockTryLock(IOLock *p){return p->try_lock();}
void IOLockUnlock(IOLock *p){p->unlock();}
int main(){
 assert(!a04br3_wacs_lock() && !a04br3_wacs_try_lock());
 { A04BR3WacsGuard g; assert(!g); }
 a04br3_registers_init(); assert(allocations == 2);
 irq_context=true; assert(!a04br3_wacs_lock() && !a04br3_wacs_try_lock()); irq_context=false;
 irq_enabled=false; assert(!a04br3_wacs_lock() && !a04br3_wacs_try_lock()); irq_enabled=true;
 assert(a04br3_wacs_lock());
 std::atomic<bool> denied{false};
 std::thread other([&]{ A04BR3WacsGuard g(true); denied = !bool(g); });
 other.join(); assert(denied); a04br3_wacs_unlock();
 { A04BR3WacsGuard g(true); assert(g); }
 unsigned active=0, total=0;
 std::vector<std::thread> tasks;
 for (int i=0;i<8;i++) tasks.emplace_back([&]{
   for (int j=0;j<10000;j++) {
     A04BR3WacsGuard g; assert(g); assert(active++ == 0);
     ++total; assert(--active == 0);
   }
 });
 for (auto &t:tasks) t.join(); assert(total==80000);
 { A04BR3WacsGuard g; assert(g); } // early returns use the same destructor
 { A04BR3WacsGuard g(true); assert(g); }
 assert(a04br3_wacs_lock()); a04br3_wacs_poison(); a04br3_wacs_unlock();
 assert(!a04br3_wacs_lock() && !a04br3_wacs_try_lock());
}
'''
with tempfile.TemporaryDirectory(prefix='a04br3-wacs-test-') as tmp:
    p = Path(tmp)
    (p / 'IOKit').mkdir()
    (p / 'IOKit/IOLib.h').write_text('#pragma once\n#include <mutex>\nusing IOLock=std::mutex; IOLock *IOLockAlloc(); void IOLockLock(IOLock*); bool IOLockTryLock(IOLock*); void IOLockUnlock(IOLock*);\n')
    (p / 'machine').mkdir()
    (p / 'machine/machine_routines.h').write_text('inline thread_local bool irq_context=false, irq_enabled=true; inline bool ml_at_interrupt_context(){return irq_context;} inline bool ml_get_interrupts_enabled(){return irq_enabled;}\n')
    (p / 'test.cpp').write_text(code)
    subprocess.run(['c++', '-std=c++17', '-O2', '-pthread', '-I', str(p), '-I', str(root / 'package'), str(p / 'test.cpp'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True)

board = (root / 'package/A04BR3Board.cpp').read_text()
assert board.index('a04br3_registers_init(); // shared') < board.index('a04br3_autoreset_init();', board.index('extern "C" void unixnu_board_init'))
reset = (root / 'package/A04BR3AutoReset.cpp').read_text()
assert 'A04BR3WacsGuard guard;' in reset[reset.index('bool setFastbootFlag(void)'):reset.index('void watchdogArm')]
assert 'wacs(' not in reset[reset.index('// BEGIN panic-aware watchdog'):reset.index('// END panic-aware watchdog')]
inp = (root / 'package/A04BR3Input.cpp').read_text()
assert 'A04BR3WacsGuard guard;' in inp[inp.index('int powerOn(void)'):inp.index('int transfer(')]
gpu = (root / 'package/A04BR3Gpu.cpp').read_text()
assert 'A04BR3WacsGuard guard;' in gpu[gpu.index('void clockDiagnosticSnapshot'):gpu.index('unsigned selectClockParent')]
assert 'A04BR3WacsGuard guard;' in gpu[gpu.index('unsigned selectClockParent'):gpu.index('}  // namespace')]
print('PASS: WACS2所有権、競合、try-lock、未初期化、panic経路、各利用箇所')
