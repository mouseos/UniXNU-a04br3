#!/usr/bin/env python3
"""Check preservation using actual pinned baseline artifacts when available."""
import os
import json
import hashlib
import shutil
import subprocess
from pathlib import Path
import re
import runpy
import sys
import tempfile
import tomllib

root = Path(__file__).resolve().parents[2]
unixnu = Path(os.environ.get('UNIXNU_ROOT', Path.cwd())).resolve()
sys.path.insert(0, str(unixnu/'scripts'))
from unixnu.payload import check_args, check_confirmation_args, kernel_arguments
mod = runpy.run_path(str(root/'scripts/a04br3-memory-matched-inputs.py'))
helper = runpy.run_path(str(root/'scripts/build-a04br3-powervr-probe.py'))
base = Path(os.environ['A04BR3_BASELINE'])
with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp); bench = work/'bench'; bench.write_bytes(b'test benchmark')
    report = mod['build'](base, bench, '12'*20, work/'out', helper)
    old = helper['unpack_addon']((base/'kernel.macho.gpuaddon').read_bytes())
    new = helper['unpack_addon']((work/'out/gpuaddon').read_bytes())
    assert new[:-1] == old and new[-1] == (mod['PATH'], 0o755, b'test benchmark')
    trust = (work/'out/trustcache').read_bytes()
    assert helper['valid_like_the_loader'](trust) and len(trust) > (base/'kernel.macho.trustcache').stat().st_size
    assert report['seed_unchanged'] and report['gpu_entries_unchanged']
    assert mod['verify'](base, bench, work/'out', helper) == report
    bench.write_bytes(b'changed')
    try: mod['verify'](base, bench, work/'out', helper)
    except ValueError: pass
    else: raise AssertionError('changed benchmark accepted')
text = (root/'scripts/a04br3-memory-matched-payload.sh').read_text()
extra = re.findall(r'--kernel-arg\s+([^\s\\]+)', text)
omitted = re.findall(r'--omit-kernel-arg(?:=|\s+)([^\s\\]+)', text)
board = tomllib.loads((root/'board.toml').read_text())['payload']
line = check_args(kernel_arguments(board['kernel_args'], extra, omitted))
check_confirmation_args(line, board.get('install', {}).get('confirm_args', []))
assert len(line.encode()) == 1023
assert 'vm_compressor=2' in line.split() and 'mb=1' in line.split()
assert '--skip-setup' not in text
assert line.endswith(' mb=1')
assert hashlib.sha256(line[:-5].encode()).hexdigest() == 'f7a62ab6e768d33141681fed399508a49cf0924780370282c57d73c40fad85d0'
with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp); runner=work/'runner'; runner.mkdir(); out=work/'out'; p=out/'a04br3/payload'; p.mkdir(parents=True)
    (out/'a04br3/kernel').mkdir(parents=True)
    (runner/'a04br3-iostat4-baseline').symlink_to(base.resolve(), target_is_directory=True)
    bench=work/'bench'; bench.write_bytes(b'test benchmark')
    matched=mod['build'](base,bench,'12'*20,runner/'a04br3-memory-matched',helper)
    kernel=out/'a04br3/kernel/kernel.macho'; kernel.write_bytes(b'kernel CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36')
    image=p/'payload.img'; image.write_bytes(b'payload')
    sha=lambda x:hashlib.sha256(x.read_bytes()).hexdigest()
    report={'kernel_args':line,'data_ramdisk_sha256':mod['EXPECTED']['scratch-data.img'],
            'gpu_addon_sha256':matched['gpuaddon_sha256'],'trust_cache_sha256':matched['trust_sha256'],
            'kernel_sha256':sha(kernel),'payload_sha256':sha(image),'skip_setup':False}
    (p/'payload.json').write_text(json.dumps(report))
    env=dict(os.environ,RUNNER_TEMP=str(runner),UNIXNU_OUT=str(out))
    cmd=[sys.executable,str(root/'scripts/a04br3-memory-matched-verify.py')]
    subprocess.run(cmd,env=env,check=True,capture_output=True)
    for key,bad in [('data_ramdisk_sha256','0'*64),('gpu_addon_sha256',None),('skip_setup',True),('kernel_args',line.replace('vm_compressor=2','vm_compressor=1'))]:
        altered=dict(report); altered[key]=bad; (p/'payload.json').write_text(json.dumps(altered))
        assert subprocess.run(cmd,env=env,capture_output=True).returncode != 0, key
print('PASS: exact baseline assets, GPU entries, trust, payload rejection and 1023-byte profile')
