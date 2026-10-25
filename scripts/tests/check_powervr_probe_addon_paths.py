#!/usr/bin/env python3
"""Actual owner addon packing must replace the exact runtime dylib path."""
from pathlib import Path
import hashlib
import re
import runpy
import tempfile
ROOT=Path(__file__).resolve().parents[2]
b=runpy.run_path(str(ROOT/'scripts/build-a04br3-powervr-probe.py'))
seed=runpy.run_path(str(ROOT/'scripts/a04br3-seed-powervr-probe.py'))
source=(ROOT/'package/powervr/user/powervrprobed.c').read_text()
runtime=re.search(r'#define POWERVRPROBED_VULKAN_LIBRARY "([^"]+)"',source)[1]
prefix='/System/Library/Extensions/'
assert runtime.startswith(prefix)
addon_name='addons/'+runtime[len(prefix):]
assert addon_name=='addons/NativePowerVRVulkan.bundle/'+b['LIB_NAME']
with tempfile.TemporaryDirectory(prefix='probe-addon-paths-') as t:
 out=Path(t);(out/'powervrprobed').write_bytes(b'current daemon')
 (out/(b['LABEL']+'.plist')).write_bytes(b['job_plist']())
 for job in (False,True):
  entries=b['unpack_addon'](b['pack_addon'](b['addon_files'](out,job)))
  assert len(entries)==1+job and not any(n==addon_name for n,_,_ in entries)
 (out/'lib').mkdir()
 stale=out/addon_name;stale.parent.mkdir(parents=True);stale.write_bytes(b'old recovered driver')
 for generation in (1,2):
  signed=bytes(range(256))*17+f'final signed generation {generation}'.encode()
  (out/'lib'/b['LIB_NAME']).write_bytes(signed)
  planned=seed['plan'](out,False)
  assert any(src==out/'lib'/b['LIB_NAME'] and dst==addon_name and mode=='0755' for src,dst,mode in planned)
  for job in (False,True):
   files=b['addon_files'](out,job)
   entries=b['unpack_addon'](b['pack_addon'](files))
   assert len(entries)==3+job
   assert len({n for n,_,_ in entries})==len(entries)
   d={n:(m,data) for n,m,data in entries}
   assert d['lib/'+b['LIB_NAME']]==d[addon_name]==(0o755,signed)
   assert hashlib.sha256(d[addon_name][1]).digest()==hashlib.sha256((out/'lib'/b['LIB_NAME']).read_bytes()).digest()
   if job:assert d['daemons/'+b['LABEL']+'.plist']==(0o644,b['job_plist']())
   assert d['bin/powervrprobed']==(0o755,b'current daemon')
print('PASS actual addon pack/unpack: runtime macro + seed mapping, signed-source byte parity at both paths, stale input excluded, refreshed library, permissions and counts 1/2/3/4')
