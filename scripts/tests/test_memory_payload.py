#!/usr/bin/env python3
"""Run the build/payload wrappers with fake tools and injected failures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
UUID = '12345678-1234-5678-9ABC-123456789ABC'
OLD = '87654321-1234-5678-9ABC-123456789ABC'


def run_case(initial_uuid, fault=None, out_mode=None, rerun=False):
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        runner, device, source, bindir = [base / n for n in ('runner', 'device', 'source', 'bin')]
        for p in (runner, device/'scripts', source, bindir): p.mkdir(parents=True)
        (device/'unixnu.lock').write_text('[source.xnu]\ncommit = "0123456789abcdef0123456789abcdef01234567"\n')
        out = source / ('custom output' if out_mode else 'out')
        (out/'a04br3/tree/refer/xnu').mkdir(parents=True)
        (out/'a04br3/tree/.cache/xnu-work').mkdir(parents=True)
        (out/'a04br3/kernel').mkdir(parents=True)
        (out/'a04br3/kernel/kernel.macho').write_text('old kernel ' + OLD)
        if out_mode:
            (source/'out/a04br3/kernel').mkdir(parents=True)
            (source/'out/a04br3/kernel/kernel.macho').write_text('conflicting default kernel')
        for name in ('a04br3-memory-build.sh', 'a04br3-memory-payload.sh', 'a04br3-memory-manifest.py'):
            shutil.copy(ROOT/'scripts'/name, device/'scripts'/name)
        (device/'scripts/a04br3-pre-build.sh').write_text(r'''#!/bin/bash
set -eu
mkdir -p "$RUNNER_TEMP/owner-data"
printf '{"volume_uuid":"12345678-1234-5678-9abc-123456789abc"}\n' > "$RUNNER_TEMP/owner-data/seed.json"
printf 'export XNU_OWNER_DATA_VOLUME_UUID=12345678-1234-5678-9ABC-123456789ABC\n' > "$RUNNER_TEMP/a04br3-data.env"
printf seed > "$RUNNER_TEMP/data.dbr"
printf trust > "$RUNNER_TEMP/trustcache"
''')
        (source/'unixnu').write_text(r'''#!/bin/bash
set -eu
printf '%s uuid=%s\n' "$*" "${XNU_OWNER_DATA_VOLUME_UUID:-unset}" >> "$TEST_LOG"
mkdir -p "$TEST_OUT_DIR/a04br3/kernel"
if [ "$1" = defconfig ]; then echo 'CONFIG_TEST=y' > "$TEST_OUT_DIR/a04br3/.config"; fi
if [ "$1 $2" = 'build kernel' ]; then
  [ "$TEST_FAULT" != kernel_failure ] || exit 1
  if [ "$TEST_FAULT" = wrong_kernel_uuid ]; then printf '%s' OLD > "$TEST_OUT_DIR/a04br3/kernel/kernel.macho"
  else printf 'kernel uuid=%s' "$XNU_OWNER_DATA_VOLUME_UUID" > "$TEST_OUT_DIR/a04br3/kernel/kernel.macho"; fi
  if [ "$TEST_FAULT" = alter_seed ]; then printf altered > "$RUNNER_TEMP/data.dbr"; fi
  if [ "$TEST_FAULT" = alter_trust ]; then printf altered > "$RUNNER_TEMP/trustcache"; fi
  if [ "$TEST_FAULT" = alter_config ]; then printf altered > "$TEST_OUT_DIR/a04br3/.config"; fi
  if [ "$TEST_FAULT" = alter_source ]; then touch "$TEST_GIT_DIRTY"; fi
  if [ "$TEST_FAULT" = alter_lock ]; then printf altered >> "$DEVICE_DIR/unixnu.lock"; fi
fi
''')
        (source/'unixnu').chmod(0o755)
        (bindir/'git').write_text('''#!/bin/sh
case "$3" in
 rev-parse) echo 0123456789abcdef0123456789abcdef01234567;;
 status) if [ -f "$TEST_GIT_DIRTY" ]; then echo " M source.c"; fi;;
 diff|submodule|ls-files) :;;
 *) exit 2;;
esac
''')
        (bindir/'git').chmod(0o755)
        env = dict(os.environ, PATH=str(bindir)+os.pathsep+os.environ['PATH'], RUNNER_TEMP=str(runner),
                   TEST_LOG=str(base/'calls'), TEST_GIT_DIRTY=str(base/'dirty'), TEST_FAULT=fault or '',
                   TEST_OUT_DIR=str(out), XNU_OWNER_DATA_VOLUME_UUID=initial_uuid)
        env.pop('UNIXNU_OUT', None)
        if out_mode == 'absolute': env['UNIXNU_OUT'] = str(out)
        if out_mode == 'relative': env['UNIXNU_OUT'] = 'custom output'
        if out_mode == 'expanded':
            env['TEST_OUT_PARENT'] = str(source)
            env['UNIXNU_OUT'] = '$TEST_OUT_PARENT/custom output'
        # Simulate a stale marker from an earlier successful run.
        (runner/'a04br3-memory-inputs.json').write_text('{}')
        command = ['bash', str(device/'scripts/a04br3-memory-build.sh'), '--enable-memory-bench']
        result = subprocess.run(command, cwd=source, env=env, capture_output=True, text=True)
        calls = (base/'calls').read_text()
        if fault:
            assert result.returncode != 0, result.stdout + result.stderr
            assert 'build payload' not in calls
            assert not (runner/'a04br3-memory-inputs.json').exists()
            return
        assert result.returncode == 0, result.stdout + result.stderr
        assert f'build kernel -D {device} uuid={UUID}' in calls
        assert f'build payload -D {device}' in calls
        record = json.loads((runner/'a04br3-memory-inputs.json').read_text())
        assert record['seed_uuid'] == UUID and len(record['kernel_sha256']) == 64
        assert record['kernel_path'] == str(out/'a04br3/kernel/kernel.macho')
        if out_mode: assert (source/'out/a04br3/kernel/kernel.macho').read_text() == 'conflicting default kernel'
        assert len(record['flags_sha256']) == 64 and record['source']['commit']
        if rerun:
            previous = (runner/'a04br3-memory-inputs.json').read_bytes()
            again = subprocess.run(command, cwd=source, env=env, capture_output=True, text=True)
            assert again.returncode != 0 and 'Existing seed inputs' in again.stderr
            assert (runner/'a04br3-memory-inputs.json').read_bytes() == previous
            reuse = dict(env, A04BR3_REUSE_DATA_SEED='1')
            repeated = subprocess.run(command, cwd=source, env=reuse, capture_output=True, text=True)
            assert repeated.returncode == 0, repeated.stderr
            # Subsequent verification must reflect the changed build flag.
            env = reuse
        # A separate invocation must use the same kernel-affecting environment.
        verify_env = dict(env, DEVICE_DIR=str(device), XNU_OWNER_DATA_VOLUME_UUID=UUID,
                          A04BR3_MEMORY_BENCH='1', A04BR3_POWERVR_PROBE='1',
                          A04BR3_POWERVR_METAL='1', A04BR3_POWERVR_METAL_MODE='publish')
        verify = ['python3', str(device/'scripts/a04br3-memory-manifest.py'), 'verify']
        assert subprocess.run(verify, cwd=source, env=verify_env, capture_output=True).returncode == 0
        for path in (runner/'data.dbr', runner/'trustcache', out/'a04br3/.config', out/'a04br3/kernel/kernel.macho'):
            old = path.read_bytes(); path.write_bytes(old+b'x')
            assert subprocess.run(verify, cwd=source, env=verify_env, capture_output=True).returncode != 0, path
            path.write_bytes(old)
        changed = dict(verify_env, XNU_IRQ_PC_STATS='1')
        assert subprocess.run(verify, cwd=source, env=changed, capture_output=True).returncode != 0
        changed = dict(verify_env, XNU_OWNER_DATA_VOLUME_UUID=OLD)
        assert subprocess.run(verify, cwd=source, env=changed, capture_output=True).returncode != 0

for initial in ('', OLD):
    run_case(initial)
run_case(OLD, rerun=True)
for mode in ('absolute', 'relative', 'expanded'):
    run_case(OLD, out_mode=mode)
for fault in ('kernel_failure', 'wrong_kernel_uuid', 'alter_seed', 'alter_trust', 'alter_config', 'alter_source', 'alter_lock'):
    run_case(OLD, fault)
print('PASS: clean/stale parent UUID, kernel and input integrity, flags and failure injection')

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    probe = base / 'probe'
    probe.mkdir()
    (probe / 'powervrprobed').write_bytes(b'probe')
    bench = base / 'bench'
    bench.write_bytes(b'signed benchmark')
    command = ['python3', str(ROOT/'scripts/a04br3-seed-powervr-probe.py'),
               '--probe-dir', str(probe), '--memory-bench', str(bench), '--dry-run']
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    assert '/private/var/ngpu/bin/a04br3-memory-copy-bench' in result.stdout
    bench.unlink()
    assert subprocess.run(command, capture_output=True).returncode != 0
print('PASS: optional benchmark staging and missing executable refusal')
