#!/usr/bin/env python3
import importlib.util,struct
from pathlib import Path
root=Path(__file__).resolve().parents[1]
def load(n,p):
 spec=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
g=load('gpu',root/'a04br3-prepare-diagnostic-gpu.py');p=load('probe',root/'build-a04br3-powervr-probe.py')
hashes=['11'*20,'22'*20];module=struct.pack('<I16sI',2,b'\0'*16,2)+b''.join(bytes.fromhex(h)+bytes([2,0,0,0]) for h in hashes);bundle=struct.pack('<II',1,8)+module
assert g.trust_hashes(bundle,p)==set(hashes)
try:g.trust_hashes(b'bad',p)
except (ValueError,struct.error):pass
else:raise AssertionError('accepted invalid trust cache')
print('PASS trust module exact CDHash enumeration and malformed refusal')
