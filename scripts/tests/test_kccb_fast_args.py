#!/usr/bin/env python3
"""Compile the actual boot argument selector and validate the fixed pair."""
from pathlib import Path
import importlib.util
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'package/powervr/NativePowerVR.cpp').read_text()
assert 'gKccbFastPoll = a04br3_kccb_fast_args(PE_boot_args());' in source
assert '#include "A04BR3KccbFastArgs.h"' in source
code = r'''
#include <cassert>
#include <cstring>
#include <string>
#include "A04BR3KccbFastArgs.h"
int main() {
  assert(!a04br3_kccb_fast_args(nullptr));
  const char *off[] = {"", "x=1", "a4kf=0", "a04br3-kccb-fast=0", "a4kf", "a4kf=", "a4kf=2", "a4kf=01", "a4kf=0x1", "a4kf=-1", "a4kf=1x", "a4kf=1=0", "a4kf =1", "a4kf=1 a4kf=1", "a4kf=1 a4kf=0", "a4kf=1 a04br3-kccb-fast=1", "a04br3-kccb-fast=1 a4kf=0", "a4kf=1 a04br3-kccb-fast=garbage", "a4kf=1 a4kf", "a04br3-kccb-fast=1 a04br3-kccb-fast=1", "other=a4kf=1", "a4kfx=1"};
  for (auto s: off) assert(!a04br3_kccb_fast_args(s));
  assert(a04br3_kccb_fast_args("a4kf=1"));
  assert(a04br3_kccb_fast_args("a04br3-kccb-fast=1"));
  assert(a04br3_kccb_fast_args(" unrelated=0\ta4kf=1 other=1 "));
  std::string maxline(1016, 'x'); maxline += " a4kf=1";
  assert(maxline.size()==1023 && a04br3_kccb_fast_args(maxline.c_str()));
  std::string longline(1017, 'x'); longline += " a4kf=1";
  assert(!a04br3_kccb_fast_args(longline.c_str()));
  char unterminated[1024]; memset(unterminated, 'x', sizeof(unterminated));
  assert(!a04br3_kccb_fast_args(unterminated));
}
'''
with tempfile.TemporaryDirectory(prefix='kccb-args-') as temp:
    path = Path(temp)
    (path / 'test.cpp').write_text(code)
    subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-fno-pie', '-no-pie', '-I', str(root / 'package/powervr'), str(path / 'test.cpp'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)

spec = importlib.util.spec_from_file_location('profiles', root / 'scripts/a04br3-kccb-fast-profiles.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
line = (root / 'scripts/tests/fixtures/kccb-fast-baseline-args.txt').read_text(encoding='ascii').removesuffix('\n')
pair = module.profiles(line)
assert len(line.encode()) == 1006
assert len(pair['off'].encode()) == len(pair['on'].encode()) == 1013
assert pair['off'] == line + ' a4kf=0' and pair['on'] == line + ' a4kf=1'
for invalid in (line + ' a4kf=1', line + ' a04br3-kccb-fast=1', line + ' ', line.replace('atp=1', 'atp=0'), line + '\n', line + 'é', None):
    try:
        module.profiles(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError('changed baseline accepted')
print('PASS actual boot-argument selector and pinned single-variable profiles; ASan/UBSan')
