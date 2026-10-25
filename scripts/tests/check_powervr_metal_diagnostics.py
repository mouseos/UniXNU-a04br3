#!/usr/bin/env python3
"""Actual Metal wrapper command/constructor lifecycle; no Apple build or GPU."""
from pathlib import Path
import itertools, os, runpy, shutil, subprocess, sys, tempfile
from unittest.mock import patch
R=Path(__file__).resolve().parents[2]
module=runpy.run_path(str(R/'scripts/build-a04br3-metal-plugin.py'))
main=module['main'];real_run=subprocess.run
flags=('--sipf1-dump','--submit-barrier','--serial-transfer','--tq-clip','--pipeline-failure-diag')
variables=('PVR_DARWIN_SIPF1_DUMP','PVR_DARWIN_SUBMIT_BARRIER','PVR_DARWIN_SERIAL_TRANSFER','PVR_DARWIN_TQ_CLIP','PVR_METAL_PIPELINE_DIAG')
with tempfile.TemporaryDirectory(prefix='pvr-metal-flags-') as t:
 root=Path(t);(root/'scripts').mkdir();builder=root/'scripts/build-vulkan-metal-plugin.py';builder.write_text('# fixture generic builder\n')
 board=root/'package/powervr/user/NativePowerVRMetalBoard.mm';board.parent.mkdir(parents=True);board.write_text('// board unchanged\n')
 library=root/'driver.dylib';library.write_bytes(b'fixture')
 out=root/'out'/'plugin';main.__globals__['ROOT']=root
 for bits in itertools.product((False,True),repeat=5):
  skip=bool(sum(bits)%2);seen=[]
  argv=['metal','--vulkan',str(library),'--out',str(out),*[f for f,b in zip(flags,bits) if b],*(['--skip-bridge'] if skip else [])]
  def generic(command,check):
   assert check;seen.append(command)
   sources=[Path(command[i+1]) for i,v in enumerate(command) if v=='--board-source']
   assert sources[0]==board and len(sources)==1+sum(bits)
   assert command.count('--skip-bridge')==int(skip)
   assert command[command.index('--vulkan-driver')+1]==str(library.resolve())
   assert '--portable-sha2' in command
   # Model real generic builder deleting OUT before compiling board sources.
   if out.exists():shutil.rmtree(out)
   out.mkdir(parents=True)
   assert all(p.is_file() for p in sources)
   for p in sources[1:]:assert p.parent==out.parent and p.suffix=='.mm' and out not in p.parents
   # Compile the exact generated constructor bodies on host; no object decoding.
   text='\n'.join(p.read_text() for p in sources[1:])+'\n#include <assert.h>\n#include <string.h>\nint main(void){\n'
   for var,on in zip(variables,bits):
    text+=f'assert(!strcmp(getenv("{var}"),"'+('1' if on else 'external')+'"));\n'
   text+='}\n';src=root/'constructors.c';src.write_text('#include <stdlib.h>\n'+text)
   real_run(['cc','-D_POSIX_C_SOURCE=200809L','-Wall','-Wextra','-Werror',str(src),'-o',str(root/'ctor')],check=True)
   env=dict(os.environ);env.update({v:'external' for v in variables})
   real_run([str(root/'ctor')],check=True,env=env)
  with patch.object(sys,'argv',argv),patch('subprocess.run',side_effect=generic):main()
  assert len(seen)==1
 assert board.read_text()=='// board unchanged\n'
print('PASS actual Metal command preparation: 32 independent flag combinations, exact constructors/env=1 only when selected, OUT cleanup survival, stale siblings unused, skip-bridge preserved; no GPU/build execution')
