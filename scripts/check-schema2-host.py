#!/usr/bin/env python3
"""Run the A04BR3 schema2 host contract; never builds an SDK image or accesses a device."""
import argparse
import hashlib
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


def source_identities(roots):
    result = {}
    for name, root in roots.items():
        status = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain=v1',
                                          '--untracked-files=all', '--ignore-submodules=none'], text=True)
        overrides = []
        for filename in ('local.toml', 'unixnu.local.toml'):
            path = root / filename
            if path.exists() or path.is_symlink():
                overrides.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()
                                  if path.is_file() else None})
        result[name] = {'path': str(root), 'revision': revision(root),
                        'dirty': bool(status), 'status_porcelain': status,
                        'local_configurations': overrides}
    return result


def source_refusal(identities, expected_tools):
    if identities['tools']['revision'] != expected_tools:
        return 'tools HEAD differs from migration/inputs.toml paired_tools_revision'
    overrides = [name for name, value in identities.items() if value['local_configurations']]
    if overrides:
        return 'local source/build configuration is not allowed in qualification: ' + ', '.join(overrides)
    dirty = [name for name, value in identities.items() if value['dirty']]
    return 'dirty source repositories: ' + ', '.join(dirty) if dirty else None


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
        command = [py, str(tests / (name + '.py'))]
        if name == 'test_schema2_host_runner':
            command += ['--unixnu', str(tools), '--soc', str(soc)]
        checks.append((name, command))
    checks.append(('flash-validation', [py, '-m', 'unittest', 'discover', '-s', str(tests), '-p', 'test_flash_validation.py']))
    for name in ('test_a04br3_ssh_profile', 'test_kernel_output_root', 'test_adt_migration_overrides', 'test_payload_volume'):
        checks.append((name, [py, str(tools / 'scripts/tests' / (name + '.py'))]))
    checks.append(('build-driver', [py, str(tools / 'scripts/check-unixnu.py')]))
    return checks


def execute(checks, tools, out, report, roots=None):
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
        if code and roots is not None:
            report['sources_after'] = source_identities(roots)
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        if code:
            print('FAIL ' + name + ': ' + str(logfile), file=sys.stderr)
            return 1
    if roots is not None:
        after = source_identities(roots)
        report['sources_after'] = after
        if after != report['sources']:
            report['status'] = 'failed'
            report['error'] = 'source revision or Git status changed during host checks'
            report_path.write_text(json.dumps(report, indent=2) + '\n')
            print(report['error'], file=sys.stderr)
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
        roots = {'tools': tools, 'device': DEVICE, 'soc': soc}
        identities = source_identities(roots)
        expected_tools = tomllib.loads((DEVICE / 'migration/inputs.toml').read_text())['paired_tools_revision']
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    report = {'schema_version': 1, 'qualification': 'host contracts only; SDK and hardware not tested',
              'sources': identities, 'paired_tools_revision': expected_tools,
              'orchestration_revision': os.environ.get('GITHUB_SHA')}
    refusal = source_refusal(identities, expected_tools)
    if refusal:
        out.mkdir(parents=True, exist_ok=True)
        report.update(status='refused', error=refusal, checks=[])
        (out / 'host-results.json').write_text(json.dumps(report, indent=2) + '\n')
        print(refusal, file=sys.stderr)
        return 1
    return execute(plan(tools, soc, out), tools, out, report, roots)


if __name__ == '__main__':
    raise SystemExit(main())
