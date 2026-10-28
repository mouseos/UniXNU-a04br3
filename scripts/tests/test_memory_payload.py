#!/usr/bin/env python3
"""Run the build/payload wrappers with fake tools and injected failures."""
import json
import os
import shlex
import sys
import time
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
        for name in ('a04br3-memory-build.sh', 'a04br3-memory-payload.sh', 'a04br3-memory-manifest.py', 'a04br3_tree_scripts.py', 'a04br3-memory-publish.py'):
            shutil.copy(ROOT/'scripts'/name, device/'scripts'/name)
        (device/'scripts/a04br3-pre-build.sh').write_text(r'''#!/bin/bash
set -eu
mkdir -p "$RUNNER_TEMP/owner-data"
printf '{"volume_uuid":"12345678-1234-5678-9abc-123456789abc"}\n' > "$RUNNER_TEMP/owner-data/seed.json"
printf 'export XNU_OWNER_DATA_VOLUME_UUID=12345678-1234-5678-9ABC-123456789ABC\n' > "$RUNNER_TEMP/a04br3-data.env"
[ "${TEST_FAULT:-}" != prebuild_failure ] || { printf partial > "$RUNNER_TEMP/trustcache"; exit 1; }
printf seed > "$RUNNER_TEMP/data.dbr"
printf baseline > "$RUNNER_TEMP/data-32.dbr"
printf trust > "$RUNNER_TEMP/trustcache"
''')
        (source/'unixnu').write_text(r'''#!/bin/bash
set -eu
printf '%s uuid=%s\n' "$*" "${XNU_OWNER_DATA_VOLUME_UUID:-unset}" >> "$TEST_LOG"
mkdir -p "$TEST_OUT_DIR/a04br3/kernel"
if [ "$1" = tree ]; then
  mkdir -p "$TEST_OUT_DIR/a04br3/tree/scripts"
  cp -R "$DEVICE_DIR/scripts/." "$TEST_OUT_DIR/a04br3/tree/scripts/"
  if [ "$TEST_FAULT" = stale_tree ]; then
    python3 - "$TEST_OUT_DIR/a04br3/tree/scripts/a04br3-memory-build.sh" <<'PY_TAMPER'
import os, pathlib, sys, time
p=pathlib.Path(sys.argv[1]); b=p.read_bytes(); p.write_bytes(bytes([b[0]^1])+b[1:]); t=time.time()+60; os.utime(p,(t,t))
PY_TAMPER
  fi
fi
if [ "$1" = defconfig ]; then echo 'CONFIG_TEST=y' > "$TEST_OUT_DIR/a04br3/.config"; fi
if [ "$1 $2" = 'build payload' ]; then
  [ "$TEST_FAULT" != payload_failure ] || exit 1
  mkdir -p "$TEST_OUT_DIR/a04br3/payload"
  python3 - "$RUNNER_TEMP" "$TEST_OUT_DIR/a04br3/payload" "$TEST_FAULT" <<'PY_REPORT'
import hashlib,json,pathlib,sys
r,p=map(pathlib.Path,sys.argv[1:3]); image=b'test payload'; (p/'payload.img').write_bytes(image)
sha=lambda b:hashlib.sha256(b).hexdigest()
report={'kernel_sha256':sha((r/'kernel.macho').read_bytes()),'trust_cache_sha256':sha((r/'trustcache').read_bytes()),'payload_sha256':sha(image),'payload_bytes':len(image)}
if sys.argv[3]=='payload_mismatch': report['payload_sha256']='0'*64
(p/'payload.json').write_text(json.dumps(report))
PY_REPORT
fi
if [ "$1 $2" = 'build kernel' ]; then
  [ "$TEST_FAULT" != kernel_failure ] || exit 1
  if [ "$TEST_FAULT" = hold_kernel ]; then
    touch "$TEST_HOLD_READY"
    while [ ! -e "$TEST_HOLD_RELEASE" ]; do sleep 0.05; done
  fi
  if [ "$TEST_FAULT" = wrong_kernel_uuid ]; then printf '%s' OLD > "$TEST_OUT_DIR/a04br3/kernel/kernel.macho"
  else printf 'kernel uuid=%s' "$XNU_OWNER_DATA_VOLUME_UUID" > "$TEST_OUT_DIR/a04br3/kernel/kernel.macho"; fi
  if [ "$TEST_FAULT" = alter_seed ]; then printf altered > "$RUNNER_TEMP/data.dbr"; fi
  if [ "$TEST_FAULT" = alter_trust ]; then printf altered > "$RUNNER_TEMP/trustcache"; fi
  if [ "$TEST_FAULT" = alter_config ]; then printf altered > "$TEST_OUT_DIR/a04br3/.config"; fi
  if [ "$TEST_FAULT" = alter_source ]; then touch "$TEST_GIT_DIRTY"; fi
  if [ "$TEST_FAULT" = alter_lock ]; then printf altered >> "$DEVICE_DIR/unixnu.lock"; fi
  if [ "$TEST_FAULT" = alter_tree_script ]; then printf altered >> "$TEST_OUT_DIR/a04br3/tree/scripts/a04br3-memory-build.sh"; fi
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
        (bindir/'python3').write_text('#!/bin/bash\n'
            'if [[ "$1" = */a04br3-memory-publish.py && "${2:-}" = publish && "${TEST_FAULT:-}" = publish_after ]]; then\n'
            + shlex.quote(sys.executable) + ' "$@" || exit $?\nexit 71\nfi\nexec '
            + shlex.quote(sys.executable) + ' "$@"\n')
        (bindir/'python3').chmod(0o755)
        env = dict(os.environ, PATH=str(bindir)+os.pathsep+os.environ['PATH'], RUNNER_TEMP=str(runner),
                   TEST_LOG=str(base/'calls'), TEST_GIT_DIRTY=str(base/'dirty'), TEST_FAULT=fault or '',
                   TEST_OUT_DIR=str(out), TEST_HOLD_READY=str(base/'hold-ready'),
                   TEST_HOLD_RELEASE=str(base/'hold-release'), XNU_OWNER_DATA_VOLUME_UUID=initial_uuid)
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
            assert (runner/'a04br3-memory-inputs.json').read_text() == '{}'
            assert not (runner/'current').exists()
            assert not list(runner.glob('.a04br3-memory-run.*'))
            return
        assert result.returncode == 0, result.stdout + result.stderr
        assert f'build kernel -D {device} uuid={UUID}' in calls
        assert f'build payload -D {device}' in calls
        payload_call = next(line for line in calls.splitlines() if line.startswith('build payload '))
        assert 'native-msdc-iostat=1' in payload_call and 'a04br3-membench=1' in payload_call
        assert 'sheng-fb-gpu=1' in payload_call
        active = (runner/'current').resolve()
        assert active.is_dir()
        assert (active/'payload.img').read_bytes() == b'test payload'
        assert (active/'payload.json').is_file()
        record = json.loads((active/'a04br3-memory-inputs.json').read_text())
        assert record['seed_uuid'] == UUID and len(record['kernel_sha256']) == 64
        assert record['kernel_path'] == str(out/'a04br3/kernel/kernel.macho')
        if out_mode: assert (source/'out/a04br3/kernel/kernel.macho').read_text() == 'conflicting default kernel'
        assert len(record['flags_sha256']) == 64 and record['source']['commit']
        if rerun:
            previous = (active/'a04br3-memory-inputs.json').read_bytes()
            previous_trust = (active/'trustcache').read_bytes()
            previous_kernel = (active/'kernel.macho').read_bytes()
            again = subprocess.run(command, cwd=source, env=env, capture_output=True, text=True)
            assert again.returncode != 0 and 'Existing seed inputs' in again.stderr
            assert (active/'a04br3-memory-inputs.json').read_bytes() == previous
            assert (active/'trustcache').read_bytes() == previous_trust
            reuse = dict(env, A04BR3_REUSE_DATA_SEED='1')
            for injected in ('prebuild_failure', 'kernel_failure', 'payload_failure', 'payload_mismatch', 'stale_tree', 'dirty_preflight'):
                failing = dict(reuse, TEST_FAULT=injected)
                if injected == 'dirty_preflight': (base/'dirty').touch()
                failed = subprocess.run(command, cwd=source, env=failing, capture_output=True, text=True)
                if injected == 'dirty_preflight': (base/'dirty').unlink()
                assert failed.returncode != 0, injected
                assert (runner/'current').resolve() == active, injected
                assert (active/'a04br3-memory-inputs.json').read_bytes() == previous, injected
                assert (active/'trustcache').read_bytes() == previous_trust, injected
                assert (active/'kernel.macho').read_bytes() == previous_kernel, injected
                assert len(list(runner.glob('.a04br3-memory-run.*'))) == 1, injected
            # A real concurrent run must not enter the shared output while the
            # first process holds its lock.
            (out/'a04br3/tree/scripts/a04br3-memory-build.sh').unlink(missing_ok=True)
            held_env = dict(reuse, TEST_FAULT='hold_kernel')
            held = subprocess.Popen(command, cwd=source, env=held_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 20
                while not (base/'hold-ready').exists() and time.monotonic() < deadline:
                    if held.poll() is not None: break
                    time.sleep(0.05)
                assert (base/'hold-ready').exists(), 'first run did not acquire lock'
                concurrent = subprocess.run(command, cwd=source, env=reuse, capture_output=True, text=True)
                assert concurrent.returncode != 0 and 'output is locked' in concurrent.stderr
            finally:
                (base/'hold-release').touch()
                held.wait(timeout=20)
            assert held.returncode == 0
            active = (runner/'current').resolve()
            previous = (active/'a04br3-memory-inputs.json').read_bytes()
            previous_trust = (active/'trustcache').read_bytes()
            # Inject a process failure after os.replace has made the new run
            # visible; cleanup must not delete that published run.
            failed = subprocess.run(command, cwd=source, env=dict(reuse, TEST_FAULT='publish_after'), capture_output=True, text=True)
            assert failed.returncode != 0
            published = (runner/'current').resolve()
            assert published != active and published.is_dir()
            assert (published/'payload.img').read_bytes() == b'test payload'
            assert (published/'a04br3-memory-inputs.json').is_file()
            assert (active/'a04br3-memory-inputs.json').read_bytes() == previous
            assert (active/'trustcache').read_bytes() == previous_trust
            assert not (out/'.a04br3-memory-build.lock').exists()
            # The deliberately altered tree is repaired before the successful retry.
            (out/'a04br3/tree/scripts/a04br3-memory-build.sh').unlink(missing_ok=True)
            repeated = subprocess.run(command, cwd=source, env=reuse, capture_output=True, text=True)
            assert repeated.returncode == 0, repeated.stderr
            # Subsequent verification must reflect the changed build flag.
            env = reuse
            active = (runner/'current').resolve()
        # A separate invocation must use the same kernel-affecting environment.
        verify_env = dict(env, DEVICE_DIR=str(device), RUNNER_TEMP=str(active),
                          A04BR3_MEMORY_OUTPUT_ROOT=str(runner), A04BR3_MEMORY_STAGED='1', XNU_OWNER_DATA_VOLUME_UUID=UUID,
                          A04BR3_MEMORY_BENCH='1', A04BR3_POWERVR_PROBE='1',
                          A04BR3_POWERVR_METAL='1', A04BR3_POWERVR_METAL_MODE='publish')
        verify = ['python3', str(device/'scripts/a04br3-memory-manifest.py'), 'verify']
        assert subprocess.run(verify, cwd=source, env=verify_env, capture_output=True).returncode == 0
        for path in (active/'data.dbr', active/'trustcache', out/'a04br3/.config', active/'kernel.macho'):
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
for fault in ('kernel_failure', 'wrong_kernel_uuid', 'alter_seed', 'alter_trust', 'alter_config', 'alter_source', 'alter_lock', 'alter_tree_script'):
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
