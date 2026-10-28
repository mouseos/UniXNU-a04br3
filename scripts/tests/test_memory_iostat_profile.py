#!/usr/bin/env python3
"""Verify the actual payload wrapper's arguments using UniXNU's validator."""
import os
from pathlib import Path
import re
import sys
import tomllib

root = Path(__file__).resolve().parents[2]
unixnu = Path(os.environ.get('UNIXNU_ROOT', Path.cwd())).resolve()
sys.path.insert(0, str(unixnu/'scripts'))
from unixnu.payload import check_args, check_confirmation_args, kernel_arguments

text = (root/'scripts/a04br3-memory-payload.sh').read_text()
extra = re.findall(r'--kernel-arg\s+([^\s\\]+)', text)
omitted = re.findall(r'--omit-kernel-arg(?:=|\s+)([^\s\\]+)', text)
assert {'a04br3-membench=1', 'native-msdc-iostat=1', 'a04br3-home-perf=1',
        'a04br3-proclog=10', 'native-msdc-irq=1', 'sheng-fb-gpu=1'} <= set(extra)
board = tomllib.loads((root/'board.toml').read_text())['payload']
line = check_args(kernel_arguments(board['kernel_args'], extra, omitted))
check_confirmation_args(line, board.get('install', {}).get('confirm_args', []))
print('PASS: memory, CPU and MSDC profile; complete command line bytes =', len(line.encode('ascii')))
