#!/usr/bin/env python3
"""Run pinned ArmCpu worker with the board's actual qualification function.

The worker's other platform services are host mocks. The board's actual gate
and lease wrappers are separately exercised by test_pmu_gate_integration.py.
"""
import pathlib, subprocess, sys, tempfile
root = pathlib.Path(__file__).resolve().parents[2]
if len(sys.argv) != 2:
    raise SystemExit('usage: test_pmu_window_cross.py ARMCPU_CHECKOUT')
arm = pathlib.Path(sys.argv[1]).resolve()
head = subprocess.check_output(['git', '-C', str(arm), 'rev-parse', 'HEAD'], text=True).strip()
if head != '3e46911033f3624b53437a3caab7b88455d71091':
    raise SystemExit('wrong ArmCpu HEAD')
if subprocess.check_output(['git', '-C', str(arm), 'status', '--porcelain', '--untracked-files=no'], text=True):
    raise SystemExit('ArmCpu tracked files modified')
board = (root/'package/A04BR3AutoReset.cpp').read_text()
start = board.index('static bool a04br3_pmu_access_qualified(unsigned cpu) {')
end = board.index('\nextern "C" bool a04br3_pmu_qualification_available', start)
qualification = board[start:end]
src = (arm/'tests/runtime-window-runner.cpp').read_text()
src = src.replace('#include "../', '#include "' + str(arm) + '/')
a = src.index('static bool a04br3_pmu_qualification_available(unsigned)')
b = src.index('\nstatic bool a04br3_pmu_lease_acquire', a)
src = src[:a] + qualification + '\nstatic bool a04br3_pmu_qualification_available(unsigned c) { return a04br3_pmu_access_qualified(c); }' + src[b:]
src = src[:src.index('int main(){')] + r'''
int main() {
 for (unsigned mode=0; mode<3; ++mode) {
  auto arg=mode ? reinterpret_cast<void *>(static_cast<uintptr_t>(mode)) : nullptr;
  samples=bindCount=gateBegins=pmuReads=contexts=terminations=0;
#if ARMCPU_RUNTIME_ACTIVE
  nearCalls=0;
#endif
  armcpu_runtime_window_worker(arg,0);
  assert(samples==0 && bindCount==0 && gateBegins==0 && pmuReads==0 && contexts==0);
#if ARMCPU_RUNTIME_ACTIVE
  assert(nearCalls==0);
#endif
  assert(!leaseActive && !armcpu_window_poison && !a04br3_runtime_probe_active && terminations==1);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='pmu-cross-') as d:
    cpp=pathlib.Path(d)/'cross.cpp'; exe=pathlib.Path(d)/'cross'; cpp.write_text(src)
    subprocess.run(['c++','-std=c++17','-DARMCPU_RUNTIME_ACTIVE=1','-DARMCPU_RUNTIME_BUSY_CONTROL=1',str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
print('PASS pinned worker with actual board qualification: arw 1/2/3; no PMU/context/bind/gate; other services mocked')
