#!/usr/bin/env python3
"""Actual Metal wrapper command/constructor lifecycle; no Apple build or GPU."""
from pathlib import Path
import contextlib, io, itertools, os, runpy, shutil, subprocess, sys, tempfile
from unittest.mock import patch
R=Path(__file__).resolve().parents[2]
module=runpy.run_path(str(R/'scripts/build-a04br3-metal-plugin.py'))
main=module['main'];real_run=subprocess.run
flags=('--sipf1-dump','--submit-barrier','--serial-transfer','--tq-clip','--pipeline-failure-diag','--require-fetch-for-apple3','--roa-color','--async-creation','--ui-latency')
variables=('PVR_DARWIN_SIPF1_DUMP','PVR_DARWIN_SUBMIT_BARRIER','PVR_DARWIN_SERIAL_TRANSFER','PVR_DARWIN_TQ_CLIP','PVR_METAL_PIPELINE_DIAG','PVR_METAL_REQUIRE_FETCH_FOR_APPLE3','PVR_DARWIN_ROA_COLOR','PVR_METAL_ASYNC_CREATION','PVR_METAL_LATENCY_DIAG')
board_source=(R/'package/powervr/user/NativePowerVRMetalBoard.mm').read_text()
assert 'if(asyncCreation && !strcmp(asyncCreation,"1"))\n        native_bridge_metal_set_async_creation(true);' in board_source
gate_start=board_source.index('    const char *asyncCreation=')
gate_end=board_source.index('native_bridge_metal_set_async_creation(true);',gate_start)+len('native_bridge_metal_set_async_creation(true);')
board_gate=board_source[gate_start:gate_end]
with tempfile.TemporaryDirectory(prefix='pvr-metal-flags-') as t:
 root=Path(t);(root/'scripts').mkdir();builder=root/'scripts/build-vulkan-metal-plugin.py';builder.write_text('# fixture generic builder\n')
 gate=root/'board-gate.c'
 gate.write_text('#include <stdlib.h>\n#include <stdbool.h>\n#include <string.h>\n#include <assert.h>\n'
                 'static unsigned calls; static void native_bridge_metal_set_async_creation(bool on){assert(on);calls++;}\n'
                 'static void prepare(void){\n'+board_gate+'\n}\n'
                 'int main(void){const char *values[]={"", "0", "1", "01", "2", "true", "-1"};\n'
                 'unsetenv("PVR_METAL_ASYNC_CREATION");prepare();assert(!calls);\n'
                 'for(unsigned i=0;i<sizeof(values)/sizeof(values[0]);i++){calls=0;setenv("PVR_METAL_ASYNC_CREATION",values[i],1);prepare();assert(calls==!strcmp(values[i],"1"));}}\n')
 real_run(['cc','-D_POSIX_C_SOURCE=200809L','-Wall','-Wextra','-Werror',str(gate),'-o',str(root/'board-gate')],check=True)
 real_run([str(root/'board-gate')],check=True)
 # Compile the exact dladdr/formatting block; no Apple SDK or assumed runtime address.
 anchor_start=board_source.index('        // BEGIN actual plugin image anchor')
 anchor_end=board_source.index('        // END actual plugin image anchor',anchor_start)
 anchor=board_source[anchor_start:anchor_end]
 assert board_source.index('if(pipelineDiag && !strcmp(pipelineDiag,"1")) {') < anchor_start
 fixture=root/'image-anchor.cpp'
 fixture.write_text(r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
struct Dl_info {const char *dli_fname;void *dli_fbase;};
static int scenario;
static std::string shown;
static void native_metal_board_prepare(unsigned) {}
static int dladdr(const void *address,Dl_info *info) {
 assert(address==reinterpret_cast<const void *>(&native_metal_board_prepare));
 if(scenario==1)return 0;
 info->dli_fbase=scenario==3?nullptr:reinterpret_cast<void *>(UINT64_MAX);
 info->dli_fname=scenario==2?nullptr:scenario==4?"/bundle/abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ":"/actual/current/NativePowerVRMetal.bundle/NativePowerVRMetal";
 return 1;
}
static void native_metal_board_text(const char *s) {shown=s;assert(shown.size()<63);}
static void run(){char imageLine[64];
'''+anchor+r'''
}
int main(){
 for(scenario=0;scenario<5;scenario++) {
 run();
 if(scenario==1 || scenario==3)assert(shown=="IM plugin=unknown");
 else if(scenario==0)assert(shown=="IM plugin=ffffffffffffffff name=NativePowerVRMetal");
 else if(scenario==2)assert(shown=="IM plugin=ffffffffffffffff name=?");
 else assert(shown=="IM plugin=ffffffffffffffff name=abcdefghijklmnopqrstuvwxyzAB");
 }
 puts("PASS actual plugin dladdr anchor: exact function pointer, basename, unknown, 64-bit base and line length <63");
}
''')
 real_run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror',str(fixture),'-o',str(root/'image-anchor')],check=True)
 real_run([str(root/'image-anchor')],check=True)
 board=root/'package/powervr/user/NativePowerVRMetalBoard.mm';board.parent.mkdir(parents=True);board.write_text('// board unchanged\n')
 library=root/'driver.dylib';library.write_bytes(b'fixture')
 out=root/'out'/'plugin';main.__globals__['ROOT']=root
 metal=root/'drivers/NativeMetal';metal.mkdir(parents=True)
 (metal/'NativeVulkanMetalPlugin.mm').write_text('PVR_METAL_LATENCY_DIAG\n')
 (metal/'NativeBridgeMetalFrontend.mm').write_text('native_bridge_metal_enable_latency\n')
 # Keep the full independent flag matrix with no pool override. Then exercise
 # every accepted pool size both alone and with all flags, including exact 16.
 cases=[(bits,None) for bits in itertools.product((False,True),repeat=len(flags))]
 cases += [(bits,pool) for bits in ((False,)*len(flags),(True,)*len(flags)) for pool in (4,8,16,32,64)]
 cases += [(tuple(flag=='--async-creation' for flag in flags),16)] # async + pool16 + skip-bridge
 cases += [((False,)*len(flags),None)] # stale pool constructor must not be reused
 for bits,pool in cases:
  skip=bool(sum(bits)%2);seen=[]
  argv=['metal','--vulkan',str(library),'--out',str(out),*[f for f,b in zip(flags,bits) if b],*(['--skip-bridge'] if skip else [])]
  if pool is not None:argv += ['--pool-block-mib',str(pool)]
  def generic(command,check):
   assert check;seen.append(command)
   sources=[Path(command[i+1]) for i,v in enumerate(command) if v=='--board-source']
   assert sources[0]==board and len(sources)==1+sum(bits)+int(pool is not None)
   policy=[p for p in sources if p.name.endswith('.pool-size-enable.mm')]
   if pool is None:
    assert not policy and all('NMB_POOL_BLOCK_MIB' not in p.read_text() for p in sources)
   else:
    assert len(policy)==1
    assert policy[0].read_text()==(
     '#include <stdlib.h>\n'
     '__attribute__((constructor)) static void a04br3_pool_size_enable(void) {\n'
     f'  setenv("NMB_POOL_BLOCK_MIB", "{pool}", 1);\n'
     '}\n')
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
   if pool is None:
    text+='if(getenv("EXPECT_POOL_INHERITED"))assert(!strcmp(getenv("NMB_POOL_BLOCK_MIB"),"external"));else assert(!getenv("NMB_POOL_BLOCK_MIB"));\n'
   else:
    text+=f'assert(!strcmp(getenv("NMB_POOL_BLOCK_MIB"),"{pool}"));\n'
   text+='}\n';src=root/'constructors.c';src.write_text('#include <stdlib.h>\n'+text)
   real_run(['cc','-D_POSIX_C_SOURCE=200809L','-Wall','-Wextra','-Werror',str(src),'-o',str(root/'ctor')],check=True)
   env=dict(os.environ);env.update({v:'external' for v in variables})
   env.pop('EXPECT_POOL_INHERITED',None)
   if pool is None:env.pop('NMB_POOL_BLOCK_MIB',None)
   else:env['NMB_POOL_BLOCK_MIB']='external' # explicit constructor overwrites it
   real_run([str(root/'ctor')],check=True,env=env)
   if pool is None:
    env.update(NMB_POOL_BLOCK_MIB='external',EXPECT_POOL_INHERITED='1')
    real_run([str(root/'ctor')],check=True,env=env)
  with patch.object(sys,'argv',argv),patch('subprocess.run',side_effect=generic):main()
  assert len(seen)==1
 # Reject at argument parsing, before generated inputs or the generic build.
 invalid=([],['0'],['-1'],['3'],['12'],['128'],['16x'],['16.0'],[''],['18446744073709551616'])
 for value in invalid:
  before={p.name:p.read_bytes() for p in out.parent.glob('*.mm')}
  errors=io.StringIO()
  with patch.object(sys,'argv',['metal','--vulkan',str(library),'--out',str(out),'--pool-block-mib',*value]),patch('subprocess.run') as build,contextlib.redirect_stderr(errors):
   try:main()
   except SystemExit as error:assert error.code==2
   else:raise AssertionError(f'invalid pool value accepted: {value}')
   build.assert_not_called()
  assert '--pool-block-mib' in errors.getvalue()
  assert before=={p.name:p.read_bytes() for p in out.parent.glob('*.mm')}
 assert board.read_text()=='// board unchanged\n'
 with patch.object(sys,'argv',['metal','--vulkan',str(library),'--out',str(out),'--runtime-commit-trace']),patch('subprocess.run') as build,contextlib.redirect_stderr(io.StringIO()):
  try:main()
  except SystemExit as error:assert error.code==2
  else:raise AssertionError('runtime trace without UI trace accepted')
  build.assert_not_called()
 with patch.object(sys,'argv',['metal','--vulkan',str(library),'--out',str(out),'--ui-latency','--runtime-commit-trace']),patch('subprocess.run') as build:
  main()
  assert build.call_args.args[0].count('--runtime-commit-trace')==1
 (metal/'NativeVulkanMetalPlugin.mm').write_text('old frontend\n')
 with patch.object(sys,'argv',['metal','--vulkan',str(library),'--out',str(out),'--ui-latency']),patch('subprocess.run') as build:
  try:main()
  except SystemExit as error:assert 'requires a NativeMetal frontend' in str(error)
  else:raise AssertionError('old NativeMetal accepted for latency diagnostics')
  build.assert_not_called()
print('PASS actual Metal command preparation: 512 independent flag combinations; all 5 pool sizes alone/combined, exact pool16 constructor, pool16+async+skip-bridge, default absent/inherited unchanged, stale pool input unused, 10 invalid pool forms rejected before generation/build; OUT cleanup survival, skip-bridge preserved, strict board async gate; no GPU/build execution')
