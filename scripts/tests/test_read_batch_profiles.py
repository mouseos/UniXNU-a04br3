#!/usr/bin/env python3
import importlib.util
from pathlib import Path

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('profiles', root / 'scripts/a04br3-read-batch-profiles.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
source = 'serial=1 pl=1 ecb=1 mb=2 a04br3-gio=6 native-io-trace=1'
result = module.profiles(source)
assert result['batch'] == result['baseline'] + ' native-cs-read-batch=1'
assert result['baseline'].endswith(' native-root-timing=1')
assert all(x not in result['baseline'].split() for x in module.OMIT)
for invalid in (source.replace('mb=2', ''), source+' mb=2', source+' native-cs-coalesce=1',
                source+' native-cs-read-batch=0', source+' native-root-timing=1', source+' x='+('a'*1024)):
    try:
        module.profiles(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(invalid[:80])
try:
    module.profiles(source+' 非ASCII')
except UnicodeEncodeError:
    pass
else:
    raise AssertionError('non-ASCII accepted')
print('PASS matched profiles: only batch selector differs, strict diagnostics, coalesce disabled and argument length bounded')
