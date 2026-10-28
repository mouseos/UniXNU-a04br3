#!/usr/bin/env python3
"""実際のpre-buildをmockコマンドで実行する。macOS・SDK・ネット接続不要。"""
from pathlib import Path
import json
import importlib.util
import plistlib
import types
from unittest import mock
import os
import subprocess
import sys
import tempfile

SCRIPT = Path(__file__).resolve().parents[1] / 'a04br3-pre-build.sh'
UUID = '12345678-1234-5678-9ABC-123456789ABC'


def executable(path, text):
    path.write_text(text)
    path.chmod(0o755)


def run_case(reuse, rounds=1, mismatch=False, probe=True, baseline=True, fail_grow=False, fail_round=None, metadata='valid', data=True, fail_seed=False, metal=True, pipeline_diag=None, roa_color=None, memory=None, out_mode=None, fresh_existing=False, timing=None):
    with tempfile.TemporaryDirectory(prefix='pre-build-reuse-') as directory:
        root = Path(directory)
        work, temporary, device, tools = [root / p for p in ('work', 'temp', 'device', 'bin')]
        for path in (work, temporary, device, tools):
            path.mkdir()
        default_tree = work / 'out/a04br3/tree'
        alternate = work / 'custom output'
        tree = (alternate if out_mode else work / 'out') / 'a04br3/tree'
        if out_mode:
            (default_tree / 'scripts/data').mkdir(parents=True)
            (default_tree / 'scripts/data/gpu-metal-vulkan-inputs.json').write_text(json.dumps({'translator': {'commit': 'WRONG-TREE-PIN'}}))
            (default_tree / '.cache/gpu-reference/metal2vulkan/.git').mkdir(parents=True)
        (tree / 'scripts/data').mkdir(parents=True)
        (tree / 'scripts/data/gpu-metal-vulkan-inputs.json').write_text(json.dumps({'translator': {'commit': 'test-pin'}}))
        (tree / '.cache/gpu-reference/metal2vulkan/.git').mkdir(parents=True)
        dmg = root / 'os.dmg'
        dmg.write_bytes(b'mock OS')
        log = root / 'calls'
        log.touch()
        baseline_uuid = '87654321-1234-5678-9ABC-123456789ABC' if mismatch else UUID
        if fresh_existing:
            (temporary/'owner-data').mkdir()
            (temporary/'owner-data/retained').write_bytes(b'preserve')
        if reuse and baseline:
            (temporary / 'data-32.dbr').write_bytes(('baseline:' + baseline_uuid).encode())
        if reuse:
            (temporary / 'owner-data').mkdir()
            # The reuse path must not need the original raw fixture at all.
            if metadata != 'missing':
                value = {'valid': json.dumps({'volume_uuid': UUID.lower()}),
                         'malformed': '{', 'list': '[]', 'numeric': '{"volume_uuid": 42}',
                         'invalid': '{"volume_uuid": "not-a-uuid"}',
                         'nil': '{"volume_uuid": "00000000-0000-0000-0000-000000000000"}'}[metadata]
                (temporary / 'owner-data/seed.json').write_text(value)
            if data:
                (temporary / 'data.dbr').write_bytes(b'' if data == 'empty' else b'existing-seed')
        executable(work / 'unixnu', '''#!/bin/bash
set -eu
printf 'unixnu %s uuid=%s\n' "$*" "${XNU_OWNER_DATA_VOLUME_UUID:-unset}" >> "$MOCK_LOG"
if [ "$1" = ipsw ]; then echo "$MOCK_DMG"; fi
if [ "$1" = build ]; then test "$XNU_OWNER_DATA_VOLUME_UUID" = "$MOCK_UUID"; fi
''')
        executable(tools / 'hdiutil', '''#!/bin/bash
printf 'hdiutil %s\n' "$*" >> "$MOCK_LOG"
''')
        for name in ('git', 'rustup'):
            executable(tools / name, '#!/bin/bash\nprintf "' + name + ' %s\\n" "$*" >> "$MOCK_LOG"\n')
        # UUID validation and pin parsing use real Python; only build/seed scripts are mocked.
        executable(tools / 'python3', '#!' + sys.executable + '\n' + r'''
import os
from pathlib import Path
import sys
if sys.argv[1] in ('-', '-c'):
    os.execv(os.environ['MOCK_PYTHON'], [os.environ['MOCK_PYTHON']] + sys.argv[1:])
args = sys.argv[2:]
name = Path(sys.argv[1]).name
if name in ('prepare-owner-data-fixture.py', 'packed_ramdisk.py', 'seed-data-grow.py', 'build-a04br3-metal-plugin.py'):
    assert Path(sys.argv[1]).resolve().is_relative_to(Path(os.environ['MOCK_TREE']).resolve()), (name, sys.argv[1])
with open(os.environ['MOCK_LOG'], 'a') as log:
    log.write('python ' + name + ' ' + ' '.join(args) + '\n')
def option(key): return Path(args[args.index(key) + 1])
def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
if name == 'prepare-owner-data-fixture.py':
    out = option('--output')
    image = out / 'owner-data-32.cdr'
    if image.exists(): raise SystemExit('fixture would overwrite existing seed')
    write(image, b'raw-seed')
    write(out / 'seed.json', ('{"volume_uuid":"' + os.environ['MOCK_UUID'] + '"}').encode())
elif name == 'packed_ramdisk.py': write(Path(args[-1]), ('baseline:' + os.environ['MOCK_UUID']).encode())
elif name == 'seed-data-grow.py':
    source = Path(args[-2]).read_bytes()
    assert source.startswith(b'baseline:')
    write(Path(args[-1]), b'grown-seed:' + source.split(b':', 1)[1])
    if os.environ.get('MOCK_FAIL_GROW') == '1': raise SystemExit('mock grow failure')
elif name == 'build-a04br3-trust-cache.py': write(option('--out'), b'base-trust')
elif name == 'build-a04br3-powervr-probe.py': write(option('--trust-out'), ('new-owner-trust-' + os.environ['MOCK_GENERATION']).encode())
elif name == 'build-a04br3-metal-plugin.py': write(option('--out') / 'trust.json', ('plugin-' + os.environ['MOCK_GENERATION']).encode())
elif name == 'build-a04br3-memory-bench.py':
    write(option('--out') / 'trust.json', b'memory trust')
    write(option('--out') / 'a04br3-memory-copy-bench', b'signed memory bench')
elif name == 'a04br3-seed-powervr-probe.py':
    if os.environ.get('MOCK_FAIL_SEED') == '1': raise SystemExit('mock reseed failure')
    source, destination = map(Path, args[-2:])
    assert source.read_bytes().startswith(b'grown-seed:')
    assert b'+new-probe-' not in source.read_bytes()
    if '--expected-volume-uuid' in args:
        expected = args[args.index('--expected-volume-uuid') + 1]
        if expected != source.read_bytes().split(b':', 1)[1].decode(): raise SystemExit('Data volume UUID mismatch')
    write(destination, source.read_bytes() + ('+new-probe-' + os.environ['MOCK_GENERATION']).encode())
''')
        env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                   DEVICE_DIR=str(device), RUNNER_TEMP=str(temporary), MOCK_LOG=str(log),
                   MOCK_DMG=str(dmg), MOCK_UUID=UUID, MOCK_ACTUAL_UUID='87654321-1234-5678-9ABC-123456789ABC' if mismatch else UUID, MOCK_PYTHON=sys.executable,
                   GITHUB_ENV=str(root / 'github-env'), MOCK_TREE=str(tree), A04BR3_POWERVR_PROBE='1' if probe else '0',
                   A04BR3_POWERVR_VULKAN='1', A04BR3_POWERVR_METAL='1' if metal else '0',
                   MOCK_FAIL_SEED='1' if fail_seed else '0', MOCK_FAIL_GROW='1' if fail_grow else '0', XNU_OWNER_DATA_VOLUME_UUID='stale')
        env.pop('UNIXNU_OUT', None)
        if out_mode == 'absolute': env['UNIXNU_OUT'] = str(alternate)
        if out_mode == 'relative': env['UNIXNU_OUT'] = 'custom output'
        if out_mode == 'expanded':
            env['BENCH_OUT_PARENT'] = str(work)
            env['UNIXNU_OUT'] = '$BENCH_OUT_PARENT/custom output'
        env.pop('A04BR3_POWERVR_PIPELINE_TIMING', None)
        if timing is not None: env['A04BR3_POWERVR_PIPELINE_TIMING'] = timing
        env.pop('A04BR3_MEMORY_BENCH', None)
        if memory is not None: env['A04BR3_MEMORY_BENCH'] = memory
        if memory == '1': env['A04BR3_MEMORY_STAGED'] = '1'
        if memory == '1': env['A04BR3_POWERVR_METAL_MODE'] = 'publish'
        env.pop('A04BR3_POWERVR_METAL_ROA_COLOR', None)
        if roa_color is not None:
            env['A04BR3_POWERVR_METAL_ROA_COLOR'] = roa_color
        env.pop('A04BR3_POWERVR_METAL_PIPELINE_DIAG', None)
        if pipeline_diag is not None:
            env['A04BR3_POWERVR_METAL_PIPELINE_DIAG'] = pipeline_diag
        env.pop('A04BR3_REUSE_DATA_SEED', None)
        if reuse:
            env['A04BR3_REUSE_DATA_SEED'] = '1'
        # pre-build prepends the real Cargo bin path; an exported shell function
        # keeps rustup mocked even after that PATH change, without changing HOME.
        launcher = 'rustup() { printf "rustup %s\\n" "$*" >> "$MOCK_LOG"; }; export -f rustup; exec bash "$1"'
        for generation in range(1, rounds + 1):
            if generation > 1:
                reuse = True
                env['A04BR3_REUSE_DATA_SEED'] = '1'
                # The metadata UUID is unchanged while the artifact bytes evolve.
                assert json.loads((temporary / 'owner-data/seed.json').read_text())['volume_uuid'].upper() == UUID
            env['MOCK_GENERATION'] = str(generation)
            env['MOCK_FAIL_SEED'] = '1' if fail_seed or generation == fail_round else '0'
            previous_data = (temporary / 'data.dbr').read_bytes() if (temporary / 'data.dbr').exists() else None
            previous_baseline = (temporary / 'data-32.dbr').read_bytes() if (temporary / 'data-32.dbr').exists() else None
            log.write_text('')
            result = subprocess.run(['bash', '-c', launcher, 'mock-pre-build', str(SCRIPT)], cwd=work, env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
            calls = log.read_text()
            assert not list(temporary.glob('a04br3-seed-rebuild.*')), 'temporary seed leaked'
            diagnostic_failure = timing not in (None, '0', '1') or (timing == '1' and not (probe and metal))
            diagnostic_failure |= memory not in (None, '0', '1') or (memory == '1' and not (probe and metal))
            diagnostic_failure |= pipeline_diag not in (None, '0', '1') or (pipeline_diag == '1' and not (metal and probe))
            diagnostic_failure |= roa_color not in (None, '0', '1') or (roa_color == '1' and not (metal and probe))
            failure = diagnostic_failure or fresh_existing or reuse and (metadata != 'valid' or not data or data == 'empty' or not baseline) or fail_seed or generation == fail_round or fail_grow or mismatch or (reuse and not probe)
            assert (result.returncode != 0) == failure, result.stdout
            if reuse:
                for forbidden in ('hdiutil ', 'unixnu ipsw', 'prepare-owner-data-fixture.py',
                                  'a04br3-seed-launch-policy.py', 'packed_ramdisk.py'):
                    assert forbidden not in calls, calls
            elif not failure:
                for required in ('hdiutil attach', 'hdiutil detach', 'prepare-owner-data-fixture.py',
                                 'a04br3-seed-launch-policy.py', 'packed_ramdisk.py', 'seed-data-grow.py'):
                    assert required in calls, calls
            if failure:
                assert 'unixnu build' not in calls, calls
                if not reuse and not diagnostic_failure and not fresh_existing:
                    assert 'hdiutil detach' in calls, calls
                if fresh_existing: assert (temporary/'owner-data/retained').read_bytes() == b'preserve'
                if fresh_existing or diagnostic_failure or reuse and (metadata != 'valid' or not data or data == 'empty' or not probe or not baseline):
                    assert not calls, calls  # 不備はtrust/data変更より前に停止する。
                if reuse and data:
                    assert (temporary / 'data.dbr').read_bytes() == previous_data
                if previous_baseline is not None:
                    assert (temporary / 'data-32.dbr').read_bytes() == previous_baseline
            else:
                assert calls.count('unixnu build kernel') == 1, calls
                assert '--tree ' + str(tree) in calls, calls
                if out_mode:
                    assert 'WRONG-TREE-PIN' not in calls
                    assert 'test-pin' in calls
                    assert json.loads((default_tree / 'scripts/data/gpu-metal-vulkan-inputs.json').read_text())['translator']['commit'] == 'WRONG-TREE-PIN'
                assert 'unixnu build kernel uuid=' + UUID in calls, calls
                if timing == '1': assert '--pipeline-timing' in calls
                if timing == '1' and pipeline_diag == '1': assert '--pipeline-failure-diag' in calls
                for required in ('build-a04br3-trust-cache.py', 'build-pvr-darwin.py',
                                 'build-a04br3-powervr-probe.py', 'a04br3-seed-powervr-probe.py'):
                    assert required in calls, calls
                if reuse:
                    assert '--expected-volume-uuid ' + UUID in calls
                else:
                    assert '--expected-volume-uuid ' + UUID in calls
                if memory == '1':
                    assert 'a04br3_tree_scripts.py --stabilize ' in calls
                    assert 'build-a04br3-memory-bench.py' in calls and '--memory-bench ' in calls
                    assert 'a04br3-memory-bench/trust.json' in calls and '--extra-trust ' in calls
                else:
                    assert 'build-a04br3-memory-bench.py' not in calls and '--memory-bench ' not in calls
                if metal:
                    assert 'build-a04br3-metal-plugin.py' in calls and 'rustup toolchain install' in calls
                    assert (temporary / 'powervr-metal/trust.json').read_text() == 'plugin-' + str(generation)
                    build = next(line for line in calls.splitlines() if line.startswith('python build-a04br3-metal-plugin.py '))
                    assert build.split().count('--pipeline-failure-diag') == (1 if pipeline_diag == '1' else 0), build
                    assert build.split().count('--roa-color') == (1 if roa_color == '1' else 0), build
                    assert '--require-fetch-for-apple3' not in build and '--async-creation' not in build, build
                else:
                    assert 'build-a04br3-metal-plugin.py' not in calls
                assert (temporary / 'data.dbr').read_bytes().endswith(('+new-probe-' + str(generation)).encode())
                expected_data = ('grown-seed:' + UUID + '+new-probe-' + str(generation)).encode()
                assert (temporary / 'data.dbr').read_bytes() == expected_data
                assert (temporary / 'data.dbr').stat().st_size == len(expected_data)
                assert (temporary / 'data-32.dbr').read_bytes() == ('baseline:' + UUID).encode()
                assert calls.count('seed-data-grow.py') == 1
                assert str(temporary / 'data-32.dbr') in calls
                if reuse:
                    assert 'a04br3-seed-rebuild.' in calls
                assert (temporary / 'trustcache').read_bytes() == ('new-owner-trust-' + str(generation)).encode()
                assert (temporary / 'a04br3-data.env').read_text() == 'export XNU_OWNER_DATA_VOLUME_UUID=' + UUID + '\n'
                assert (root / 'github-env').read_text().splitlines()[-1] + '\n' == 'XNU_OWNER_DATA_VOLUME_UUID=' + UUID + '\n'


def test_actual_staging_identity():
    """実際のstaging mainを実行し、diskutil照合より先にguestを書かないことを検証。"""
    source = SCRIPT.with_name('a04br3-seed-powervr-probe.py')
    spec = importlib.util.spec_from_file_location('seed_probe_under_test', source)
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)

    class FirstGuestWrite(Exception):
        pass

    for actual, expected, should_write in ((UUID.lower(), UUID, True),
                                           ('87654321-1234-5678-9ABC-123456789ABC', UUID, False),
                                           (None, UUID, False), ('bad', UUID, False),
                                           (None, None, True)):
        with tempfile.TemporaryDirectory(prefix='seed-identity-') as directory:
            root = Path(directory)
            base, output = root / 'input.dbr', root / 'output.dbr'
            base.write_bytes(b'unchanged-input')
            probe = root / 'probe'
            probe.write_bytes(b'new-probe')
            markers = root / 'markers'
            markers.mkdir()
            calls = []
            packed = types.ModuleType('packed_ramdisk')
            packed.regions = mock.Mock(return_value=(64, [(32, b'NXSB')]))

            def check_output(command):
                calls.append(command)
                if command[0] == 'hdiutil':
                    mount = Path(command[command.index('-mountpoint') + 1])
                    (mount / 'mobile').mkdir()
                    return plistlib.dumps({'system-entities': [{'dev-entry': '/dev/mock'}]})
                assert command[:3] == ['diskutil', 'info', '-plist'], command
                return plistlib.dumps({} if actual is None else {'VolumeUUID': actual})

            def run(command, **kwargs):
                calls.append(command)
                if command[0] == 'sudo':
                    raise FirstGuestWrite()
                assert command == ['hdiutil', 'detach', '/dev/mock']
                return subprocess.CompletedProcess(command, 0)

            argv = [str(source), '--tree', str(root), '--probe-dir', str(root), str(base), str(output)]
            if expected:
                argv[1:1] = ['--expected-volume-uuid', expected]
            with mock.patch.object(stage.sys, 'argv', argv), \
                 mock.patch.object(stage.sys, 'platform', 'darwin'), \
                 mock.patch.object(stage, 'plan', return_value=[(probe, 'bin/probe', '0755')]), \
                 mock.patch.object(stage.tempfile, 'mkdtemp', return_value=str(markers)), \
                 mock.patch.object(stage.subprocess, 'check_output', side_effect=check_output), \
                 mock.patch.object(stage.subprocess, 'run', side_effect=run), \
                 mock.patch.dict(sys.modules, {'packed_ramdisk': packed}), \
                 mock.patch.object(stage.sys, 'path', list(sys.path)):
                try:
                    stage.main()
                except FirstGuestWrite:
                    assert should_write
                except SystemExit as error:
                    assert not should_write and 'UUID mismatch' in str(error)
                else:
                    raise AssertionError('Expected write sentinel or identity rejection')
            packed.regions.assert_called_once()
            assert calls[-1] == ['hdiutil', 'detach', '/dev/mock']
            installs = [c for c in calls if c[0] == 'sudo']
            assert bool(installs) == should_write
            assert any(c[0] == 'diskutil' for c in calls) == bool(expected)
            if installs and expected:
                assert next(i for i,c in enumerate(calls) if c[0] == 'diskutil') < calls.index(installs[0])
            assert base.read_bytes() == b'unchanged-input' and not output.exists()
    print('PASS: actual staging checks mounted UUID before guest install; mismatch/missing/invalid rejected; detach; single unpack; default unchanged')


def main():
    test_actual_staging_identity()
    run_case(False, roa_color='1')
    run_case(True, roa_color='1')
    run_case(True, roa_color='0')
    run_case(True, roa_color='invalid')
    run_case(True, roa_color='1', metal=False)
    run_case(False, roa_color='1', probe=False)
    run_case(False, roa_color='1', pipeline_diag='1')
    run_case(True, roa_color='1', pipeline_diag='1')
    run_case(False, pipeline_diag='1')
    run_case(True, pipeline_diag='1')
    run_case(True, pipeline_diag='0')
    run_case(True, pipeline_diag='invalid')
    run_case(True, pipeline_diag='1', metal=False)
    run_case(False, pipeline_diag='1', probe=False)
    run_case(False, rounds=3)
    run_case(False, rounds=4, fail_round=4)
    run_case(True)
    run_case(True, metal=False)
    for metadata in ('missing', 'malformed', 'list', 'numeric', 'invalid', 'nil'):
        run_case(True, metadata=metadata)
    run_case(True, baseline=False)
    run_case(True, fail_grow=True)
    run_case(True, mismatch=True)
    run_case(True, probe=False)
    run_case(True, data=False)
    run_case(True, data='empty')
    run_case(False, fail_seed=True)
    run_case(True, fail_seed=True)
    print('PASS: same-workdir initial -> reuse -> reuse; updated probe/plugin/trust; UUID before kernel; fresh baseline each time; bounded size and baseline identity; metadata/Data mismatch and PROBE=0 rejected; input preserved')


if __name__ == '__main__':
    main()

run_case(True, memory='1')
run_case(True, memory='bad')
run_case(True, memory='1', probe=False)
print('PASS: memory benchmark explicit opt-in, staging, trust and failure conditions')
run_case(True, memory='1', metal=False)

for mode in (None, 'absolute', 'relative', 'expanded'):
    run_case(True, memory='1', out_mode=mode)
    run_case(False, memory='1', out_mode=mode)
print('PASS: default and alternate UNIXNU_OUT select one tree despite conflicting default tree')

run_case(False, fresh_existing=True)
print('PASS: fresh seed creation refuses existing inputs before modifying them')

run_case(True, timing="1")
run_case(True, timing="bad")
run_case(True, timing="1", metal=False)
print("PASS: pipeline timing is strict opt-in and requires probe and Metal")

run_case(True, timing="1", pipeline_diag="1")
print("PASS: simultaneous pipeline timing and failure diagnostics both reach plugin builder")
