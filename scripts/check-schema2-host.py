#!/usr/bin/env python3
"""Run the A04BR3 schema2 host contract; never builds an SDK image or accesses a device."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

DEVICE = Path(__file__).resolve().parents[1]


def revision(root):
    return subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()


def check_paths(tools, soc, out):
    for root in (tools, DEVICE, soc):
        if out == root or root in out.parents:
            raise ValueError('--out must be outside the tools, device and SoC source directories')
    if tomllib.loads((DEVICE / 'board.toml').read_text()).get('schema_version') != 2:
        raise ValueError('this check requires the schema2 device configuration')
    if tomllib.loads((soc / 'soc.toml').read_text()).get('schema_version') != 2:
        raise ValueError('this check requires a schema2 SoC source')
    sources = tomllib.loads((DEVICE / 'feeds.toml').read_text())['source']
    expected = next(s['commit'] for s in sources if s['kind'] == 'soc' and s['name'] == 'mt8167')
    if revision(soc) != expected:
        raise ValueError('SoC HEAD differs from the fixed device feed')
    if subprocess.check_output(['git', '-C', str(soc), 'status', '--porcelain'], text=True).strip():
        raise ValueError('SoC source must be clean')
    missing = [name for name in ('git', 'dtc', 'g++', 'clang', 'mformat', 'mcopy', 'mmd', 'mtype') if not shutil.which(name)]
    if missing:
        raise ValueError('missing host tools: ' + ', '.join(missing))


def plan(tools, soc, out):
    py = sys.executable
    tests = DEVICE / 'scripts/tests'
    tree = out / 'contract/a04br3/ipados-17.7.11-21H461/a04br3/tree'
    checks = [
        ('contract', [py, str(tests / 'check_schema2_contract.py'), '--unixnu', str(tools), '--soc', str(soc), '--out', str(out / 'contract')]),
        ('bindings', [py, str(tests / 'check_schema2_build_bindings.py'), '--tree', str(tree)]),
        ('porting-record', [py, str(tools / 'scripts/porting-record.py'), 'check', '--device', str(DEVICE)]),
    ]
    for name in ('test_schema2_host_runner', 'check_schema2_prebuild_layout', 'test_a04br3_pre_build_seed_reuse',
                 'test_a04br3_autoreset_panic', 'test_a04br3_charger',
                 'test_a04br3_charger_handoff_api', 'test_a04br3_gauge', 'check_pvr_buffer_rows'):
        checks.append((name, [py, str(tests / (name + '.py'))]))
    checks.append(('flash-validation', [py, '-m', 'unittest', 'discover', '-s', str(tests), '-p', 'test_flash_validation.py']))
    for name in ('test_a04br3_ssh_profile', 'test_kernel_output_root', 'test_adt_migration_overrides', 'test_payload_volume'):
        checks.append((name, [py, str(tools / 'scripts/tests' / (name + '.py'))]))
    checks.append(('build-driver', [py, str(tools / 'scripts/check-unixnu.py')]))
    return checks


def execute(checks, tools, out, report):
    """Persist each real command's result, and stop at the first failing command."""
    logs = out / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    report_path = out / 'host-results.json'
    report['checks'] = []
    report['status'] = 'running'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    env = dict(os.environ)
    # The actual contracts use assertions. An inherited optimization setting
    # must not turn their checks into an apparent successful run.
    env.pop('PYTHONOPTIMIZE', None)
    for name, command in checks:
        logfile = logs / (name + '.log')
        print('CHECK ' + name, flush=True)
        with logfile.open('wb') as stream:
            try:
                result = subprocess.run(command, cwd=tools, env=env, stdout=stream, stderr=subprocess.STDOUT)
                code = result.returncode
            except OSError as exc:
                stream.write(str(exc).encode())
                code = 127
        report['checks'].append({'name': name, 'command': command, 'returncode': code, 'log': str(logfile)})
        report['status'] = 'failed' if code else 'running'
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        if code:
            print('FAIL ' + name + ': ' + str(logfile), file=sys.stderr)
            return 1
    report['status'] = 'passed'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print('PASS host contracts only: ' + str(report_path))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unixnu', type=Path, required=True)
    parser.add_argument('--soc', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    tools, soc, out = (p.resolve() for p in (args.unixnu, args.soc, args.out))
    try:
        check_paths(tools, soc, out)
        if out.exists() and any(out.iterdir()):
            raise ValueError('--out must be new or empty; preserve earlier evidence separately')
        identities = {}
        for name, root in (('tools', tools), ('device', DEVICE), ('soc', soc)):
            identities[name] = {'path': str(root), 'revision': revision(root),
                                'dirty': bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True).strip())}
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    report = {'schema_version': 1, 'qualification': 'host contracts only; SDK and hardware not tested', 'sources': identities}
    return execute(plan(tools, soc, out), tools, out, report)


if __name__ == '__main__':
    raise SystemExit(main())
