#!/usr/bin/env python3
"""Exercise the build wrapper's paths, seed checks and emitted compile flags."""
import json, os, shutil, subprocess, tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='rtc-build-wrapper-') as d:
 p=Path(d); source=p/'unixnu'; device=p/'device'; temp=p/'seed'; out=p/'custom-output'; bin=p/'bin'
 for x in (source/'drivers/NativeMsdc',device/'scripts',temp/'owner-data',bin): x.mkdir(parents=True)
 shutil.copy(root/'scripts/a04br3-verify-workflow-seed.py',device/'scripts')
 (temp/'data.dbr').write_bytes(b'data');(temp/'owner-data/owner-data-32.cdr').write_bytes(b'seed')
 uid='12345678-1234-4234-8234-123456789abc';(temp/'owner-data/seed.json').write_text(json.dumps({'volume_uuid':uid}))
 (bin/'git').write_text('''#!/bin/bash
if [[ "$*" == *"ls-tree"* ]]; then echo "160000 commit 66d40654ae43ef27090a6d6e1f552be6ea10d24b drivers/NativeMsdc"; elif [[ "$*" == *"status"* ]]; then if [[ "${DIRTY_MSDC:-0}" = 1 && "$*" == *"drivers/NativeMsdc"* ]]; then echo " M NativeMsdc.cpp"; elif [[ "${EXPECT_EXCLUDED:-0}" = 1 && "$*" != *"-C"* && "$*" != *":(exclude)device"* ]]; then echo "?? device/"; fi; elif [[ "$*" == *"drivers/NativeMsdc"* ]]; then echo 66d40654ae43ef27090a6d6e1f552be6ea10d24b; elif [[ "$*" == *"-C"* ]]; then echo device-commit; else echo 22e49b19f9651e8b0e81c3fb3764d8d3a72c7ff4; fi
''')
 (bin/'nm').write_text('''#!/bin/bash
for s in native_msdc_irq_mask_failures native_msdc_irq_stats native_msdc_worker_stats native_cs_trace_report; do echo "00000000 T _$s"; done
''')
 (source/'unixnu').write_text('''#!/bin/bash
set -eu
if [ "$1" = build ]; then
 test "$XNU_MSDC_IRQ_WAIT:$XNU_MSDC_IRQ_WORKER:$XNU_ROOT_TIMING:$XNU_IO_OVERLAP_TRACE" = 1:1:1:1
 t="$UNIXNU_OUT/a04br3/tree/build"; mkdir -p "$t/native-msdc" "$t/a04br3-board" "$UNIXNU_OUT/a04br3/kernel"
 echo -- -DNATIVE_MSDC_IRQ_WAIT=1 > "$t/native-msdc/compile-command.json"
 echo -- -DA04BR3_RTC_REF=1 > "$t/a04br3-board/A04BR3AutoReset.command.json"
 touch "$t/native-msdc/libNativeMsdc.a" "$UNIXNU_OUT/a04br3/kernel/kernel.macho"
fi
''')
 for f in (bin/'git',bin/'nm',source/'unixnu'): f.chmod(0o755)
 env=dict(os.environ, PATH=str(bin)+os.pathsep+os.environ['PATH'], UNIXNU_ROOT=str(source), DEVICE_DIR=str(device), RUNNER_TEMP=str(temp), UNIXNU_OUT=str(out), A04BR3_DEVICE_COMMIT='device-commit', XNU_OWNER_DATA_VOLUME_UUID=uid)
 script=str(root/'scripts/a04br3-rtc-ref-incremental.sh')
 subprocess.run(['bash',script],cwd=p,env=env,check=True,stdout=subprocess.DEVNULL)
 assert (out/'a04br3/kernel/kernel.macho').is_file()
 nested=source/'device';shutil.copytree(device,nested);env['DEVICE_DIR']=str(nested);env['EXPECT_EXCLUDED']='1'
 subprocess.run(['bash',script],cwd=p,env=env,check=True,stdout=subprocess.DEVNULL)
 env['DEVICE_DIR']=str(device);env.pop('EXPECT_EXCLUDED')
 env['XNU_OWNER_DATA_VOLUME_UUID']='12345678-1234-4234-8234-123456789abd'
 assert subprocess.run(['bash',script],cwd=p,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
 env['XNU_OWNER_DATA_VOLUME_UUID']=uid;env['DIRTY_MSDC']='1'
 assert subprocess.run(['bash',script],cwd=p,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
 env.pop('DIRTY_MSDC');(temp/'data.dbr').unlink()
 assert subprocess.run(['bash',script],cwd=p,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
print('PASS RTC build wrapper: explicit root, custom output, flags, seed mismatch and missing seed')
