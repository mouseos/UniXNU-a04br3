#!/usr/bin/env python3
import importlib.util
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'a04br3-recorded-diagnostic-payload.py';s=importlib.util.spec_from_file_location('payload',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
good='sheng-smp=1 cc=1 gi=6 gs=1 native-io-trace=1 sheng-gpu-addon=1 a04br3-gpu-metal=1 sheng-fb-gpu=1'
assert m.checked_args(good)==len(good.encode())+1
assert m.checked_args(good+' arw=1',True)==len((good+' arw=1').encode())+1
for bad in (good,good+' arw=0',good+' arw=1 arw=1',good+' armcpu-runtime-window=1'):
 try:m.checked_args(bad,True)
 except ValueError:pass
 else:raise AssertionError('invalid runtime-window args accepted')
for bad in (good+' arw=1',good+' cc=1',good+' armcpu-cycle-clock=0',good+' arc=1',good+' a04br3-gio=0',good+' '+'x'*1024,good.replace('gs=1','gs=0')):
 try:m.checked_args(bad)
 except ValueError:pass
 else:raise AssertionError('bad boot args accepted')
print('PASS bounded boot args, required options, duplicates and aliases')

assert m.checked_args(good+' arw=1 aq=1',True,True)==len((good+' arw=1 aq=1').encode())+1
for bad in (good+' arw=1',good+' arw=1 aq=0',good+' arw=1 aq=1 aq=1',good+' aq=1'):
 try:m.checked_args(bad,True,True)
 except ValueError:pass
 else:raise AssertionError('invalid PMUv3 experiment args accepted')
try:m.checked_args(good+' arw=1 aq=1',True,False)
except ValueError:pass
else:raise AssertionError('unexpected PMUv3 experiment accepted')

assert m.checked_args(good+' arw=2 aq=1',True,True,True)==len((good+' arw=2 aq=1').encode())+1
for line,window,active,repeat in ((good+' arw=1 aq=1',True,True,True),(good+' arw=2 aq=1',True,True,False),(good+' arw=2',True,False,True)):
 try:m.checked_args(line,window,active,repeat)
 except ValueError:pass
 else:raise AssertionError('invalid repeat flags accepted')
print('PASS explicit repeated active sampling arguments')

try:m.checked_args(good+' gs=0 arw=2 aq=1',True,True,True)
except ValueError:pass
else:raise AssertionError('conflicting required boot key accepted')

assert m.checked_args(good+' arw=3 aq=1',True,True,True,True)
for line,window,active,repeat,context in ((good+' arw=2 aq=1',True,True,True,True),(good+' arw=3 aq=1',True,True,True,False),(good+' arw=3 aq=1',True,True,False,True)):
 try:m.checked_args(line,window,active,repeat,context)
 except ValueError:pass
 else:raise AssertionError('invalid context arguments accepted')
print('PASS opt-in context diagnostic arguments')

config='CONFIG_XNU_ARMCPU_RUNTIME_WINDOW=y\nCONFIG_XNU_ARMCPU_IDLE_EPOCH=y\nCONFIG_XNU_PMUV3_ACTIVE=y\n'
markers=b'ArmCpu: runtime-scope\0ArmCpu: runtime-context unavailable=board-hook\0a04br3 cpu-context round='
names=('_a04br3_cpu_context_sample','_unixnu_pmuv3_active_start','_unixnu_pmuv3_active_stop','_unixnu_idle_epoch_observe')
symbols='\n'.join('00000001 (__TEXT_EXEC,__text) external '+n for n in names)
m.checked_context_kernel(config,markers,symbols)
for cfg,blob,nm in ((config.replace('CONFIG_XNU_PMUV3_ACTIVE=y',''),markers,symbols),(config,markers.replace(b'runtime-scope',b'missing'),symbols),(config,markers,symbols.replace('(__TEXT_EXEC,__text) external _a04br3_cpu_context_sample','(undefined) external _a04br3_cpu_context_sample')),(config,markers,symbols.replace(' external _a04br3_cpu_context_sample',' non-external _a04br3_cpu_context_sample'))):
 try:m.checked_context_kernel(cfg,blob,nm)
 except ValueError:pass
 else:raise AssertionError('invalid context kernel accepted')
print('PASS context config, markers and defined symbols')

asm="<_a04br3_cpu_context_sample>:\n  isb\n  mrs x0, CNTVCT_EL0\n  bl 123 <_a04br3_cpu_load_snapshot>\n  isb\n  mrs x1, CNTVCT_EL0\n"
m.checked_context_disassembly(asm)
for bad in (asm.replace("mrs x0, CNTVCT_EL0","nop"),asm.replace("isb\n  mrs x1","nop\n  mrs x1"),asm.replace("  bl 123 <_a04br3_cpu_load_snapshot>\n", "")+"  bl 123 <_a04br3_cpu_load_snapshot>\n"):
 try:m.checked_context_disassembly(bad)
 except ValueError:pass
 else:raise AssertionError('invalid context disassembly accepted')
print('PASS context timer instruction order')
