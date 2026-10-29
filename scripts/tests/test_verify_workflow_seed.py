#!/usr/bin/env python3
import importlib.util,json,tempfile
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'a04br3-verify-workflow-seed.py';spec=importlib.util.spec_from_file_location('seed',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as d:
 f=Path(d)/'seed.json';f.write_text(json.dumps({'volume_uuid':'12345678-1234-1234-1234-123456789abc'}));m.verify(f,'12345678-1234-1234-1234-123456789ABC')
 for value in ('00000000-0000-0000-0000-000000000000','not-a-uuid','12345678-1234-1234-1234-123456789abd'):
  try:m.verify(f,value)
  except ValueError:pass
  else:raise AssertionError('invalid UUID accepted')
 f.write_text('{}')
 try:m.verify(f,'12345678-1234-1234-1234-123456789abc')
 except KeyError:pass
 else:raise AssertionError('missing UUID accepted')
print('PASS workflow seed UUID match, invalid, nil, missing and mismatch')
