#!/usr/bin/env python3
import importlib.util
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'a04br3-recorded-diagnostic-payload.py';s=importlib.util.spec_from_file_location('payload',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
good='sheng-smp=1 cc=1 gi=6 gs=1 native-io-trace=1 sheng-gpu-addon=1 a04br3-gpu-metal=1 sheng-fb-gpu=1'
assert m.checked_args(good)==len(good.encode())+1
for bad in (good+' cc=1',good+' armcpu-cycle-clock=0',good+' arc=1',good+' a04br3-gio=0',good+' '+'x'*1024,good.replace('gs=1','gs=0')):
 try:m.checked_args(bad)
 except ValueError:pass
 else:raise AssertionError('bad boot args accepted')
print('PASS bounded boot args, required options, duplicates and aliases')
