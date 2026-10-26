#!/usr/bin/env python3
"""Exercise production pair guards including nonclock drift and missing input hashes."""
from pathlib import Path
import runpy
R=Path(__file__).resolve().parents[2]
m=runpy.run_path(str(R/'scripts/build-a04br3-buffer-rows-pair.py'))
reference='-v a04br3-powervrprobed=1 sheng-gpu-addon=1 a04br3-gpu-clock=380'
a,om=m['paired_arguments'](reference,['-v','a04br3-proclog=60'],0)
assert 'a04br3-proclog' in om and a[-1]=='a04br3-gpu-clock=0'
keys=('kernel_sha256','payload_kernel_sha256','data_ramdisk_sha256','gpu_addon_sha256','trust_cache_sha256')
l={k:'same' for k in keys};l['kernel_args']=' '.join(a)
r=dict(l,kernel_args=reference)
m['verify_pair'](l,r)
for key in keys:
    bad=dict(r);bad[key]='different'
    try:m['verify_pair'](l,bad)
    except ValueError:pass
    else:raise RuntimeError('missed '+key)
for args in (reference+' a04br3-proclog=60',reference+' a04br3-gpu-clock=0'):
    try:m['verify_pair'](l,dict(r,kernel_args=args))
    except ValueError:pass
    else:raise RuntimeError('missed bootarg drift')
for ref in ('-v',reference+' sheng-gpu-addon=1',reference+' '+('x'*1001)):
    try:m['paired_arguments'](ref,[],0)
    except ValueError:pass
    else:raise RuntimeError('missed invalid reference')
print('PASS actual pair planning,1000-byte cap, duplicate/nonclock drift and5 artifact hashes')
