#!/usr/bin/env python3
"""Test the actual Metal wrapper gate without an Apple toolchain."""
from pathlib import Path
from unittest.mock import patch
import contextlib,io,os,runpy,subprocess,sys,tempfile
root=Path(__file__).resolve().parents[2]
module=runpy.run_path(str(root/'scripts/build-a04br3-metal-plugin.py'))
main=module['main']
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'scripts').mkdir();(p/'scripts/build-vulkan-metal-plugin.py').touch()
 board=p/'package/powervr/user/NativePowerVRMetalBoard.mm';board.parent.mkdir(parents=True);board.touch()
 library=p/'driver';library.touch();out=p/'out/plugin';main.__globals__['ROOT']=p
 for enabled in (False,True):
  commands=[]
  def build(command,check):
   assert check;commands.append(command)
   extra=[Path(command[i+1]) for i,x in enumerate(command) if x=='--board-source']
   assert len(extra)==1+int(enabled)
   if enabled:
    text=extra[-1].read_text();assert 'setenv("PVR_PIPELINE_TIMING", "1", 1)' in text
    assert 'SIPF1_DUMP' not in text and 'SHADER_CAPTURE' not in text
  argv=['build','--vulkan',str(library),'--out',str(out)]+(['--pipeline-timing'] if enabled else [])
  with patch.object(sys,'argv',argv),patch('subprocess.run',side_effect=build):main()
  assert len(commands)==1
script=(root/'scripts/a04br3-pre-build.sh').read_text()
assert 'A04BR3_POWERVR_PIPELINE_TIMING:-0' in script and 'METAL_DIAGNOSTICS+=(--pipeline-timing)' in script
assert 'must be 0 or 1' in script
print('PASS: actual plugin wrapper default-off, opt-in constructor and pre-build gate')
