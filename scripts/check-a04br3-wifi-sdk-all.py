#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Opt-in compile-only SDK audit: continue all WiFi C/C++ units, never link/run."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

OBJECTS = Path('build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837')

def load_build_rules(device, tree):
    # Extract only pure production flag/command/source rules. Importing the board
    # builder would compile other drivers and fetch firmware as a side effect.
    board = ast.parse((device / 'scripts/build-a04br3-board.py').read_text())
    wifi = ast.parse((device / 'scripts/a04br3_wifi_build.py').read_text())
    selected = [n for n in board.body if
                (isinstance(n, ast.FunctionDef) and n.name == 'command_for') or
                (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                 ('CXX_FLAGS', 'C_FLAGS') for t in n.targets))]
    selected += [n for n in wifi.body if isinstance(n, ast.FunctionDef) and n.name == 'source_files']
    names = {n.name if isinstance(n, ast.FunctionDef) else n.targets[0].id for n in selected}
    if names != {'command_for', 'CXX_FLAGS', 'C_FLAGS', 'source_files'}:
        raise ValueError('production build rule shape changed; update this diagnostic')
    namespace = {'os': os, 'ROOT': tree}
    exec(compile(ast.Module(body=selected, type_ignores=[]), 'production-wifi-rules', 'exec'), namespace)
    build_wifi = next(n for n in wifi.body if isinstance(n, ast.FunctionDef) and n.name == 'build_wifi')
    c_lists = []
    for node in ast.walk(build_wifi):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == 'path':
            try:
                values = ast.literal_eval(node.iter)
            except (ValueError, TypeError):
                continue
            if isinstance(values, tuple) and values and all(isinstance(v, str) and v.endswith('.c') for v in values):
                c_lists.append(values)
    if len(c_lists) != 1:
        raise ValueError('production C source list changed; update this diagnostic')
    assignments = [n for n in build_wifi.body if isinstance(n, ast.Assign) and
                   any(isinstance(t, ast.Name) and t.id == 'flags' for t in n.targets)]
    c_flags = [n for n in ast.walk(build_wifi) if isinstance(n, ast.BinOp) and
               isinstance(n.left, ast.Name) and n.left.id == 'c_flags']
    if len(assignments) != 1 or len(c_flags) != 1:
        raise ValueError('production WiFi flag rules changed; update this diagnostic')
    namespace['wifi_cxx_flags'] = assignments[0].value
    namespace['wifi_c_flags'] = c_flags[0]
    return namespace, c_lists[0]


def read_record(tree, relative):
    record = json.loads((tree / OBJECTS / relative).read_text().strip().rstrip(','))
    if not Path(record['directory']).resolve().is_relative_to(tree):
        raise ValueError('compiler record cwd escapes the selected tree (stale absolute paths)')
    if not isinstance(record['arguments'], list) or record['file'] not in record['arguments']:
        raise ValueError('invalid compiler record')
    return record


def audit(tree, device, output):
    tree, device, output = tree.resolve(), device.resolve(), output.absolute()
    if os.environ.get('A04BR3_WIFI') != '1':
        raise ValueError('diagnostic requires explicit A04BR3_WIFI=1')
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError('output has a symlink component')
    output = output.resolve()  # Normalize '..' before all boundary checks.
    if output.exists() or not output.parent.is_dir():
        raise ValueError('output must be absent with an existing parent')
    # Keep all audit writes out of the selected source and normal build tree.
    for root in (tree, device):
        if root == output or root in output.parents or output in root.parents:
            raise ValueError('diagnostic output overlaps a source/build input')
    rules, c_sources = load_build_rules(device, tree)
    records = {'cxx': read_record(tree, 'iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'),
               'c': read_record(tree, 'bsd/DEVELOPMENT/kern_exec.o.json')}
    source = device / 'package/wifi'
    include = output / 'include/IOKit/network'
    flag_context = {'cxx_flags': rules['CXX_FLAGS'], 'c_flags': rules['C_FLAGS'],
                    'includes': include, 'source': source}
    cxx_flags = eval(compile(ast.Expression(rules['wifi_cxx_flags']), 'wifi-cxx-flags', 'eval'), flag_context)
    c_flags = eval(compile(ast.Expression(rules['wifi_c_flags']), 'wifi-c-flags', 'eval'), flag_context)
    work = [(p, 'cxx', cxx_flags) for p in rules['source_files'](source)]
    work += [(source / 'bearssl/src' / p, 'c', c_flags) for p in c_sources]
    for path, kind, flags in work:
        if not path.is_file():
            raise ValueError('missing production source: ' + str(path))
    output.mkdir()
    include.mkdir(parents=True)
    for header in (source / 'network/apple').glob('*.h'):
        (include / header.name).symlink_to(header.resolve())
    rows = []
    aggregate = output / 'diagnostics.log'
    with aggregate.open('w') as log:
        for index, (path, kind, flags) in enumerate(work):
            prefix = output / f'{index:03d}-{path.stem}'
            obj = prefix.with_suffix('.o')
            command = rules['command_for'](records[kind], path, obj) + flags + ['-ferror-limit=0']
            # No diagnostic side-files may point back into the ordinary build.
            # Production command_for strips -MF/-MT/-MJ; disable implicit .d too.
            command = [x for x in command if x not in ('-MD', '-MMD', '-MP')]
            dangerous = ('-dependency-file', '-serialize-diagnostics', '--serialize-diagnostics',
                         '-save-temps', '-fprofile', '-ftime-trace')
            if any(any(x.startswith(p) for p in dangerous) for x in command):
                raise ValueError('unsupported compiler side-output option')
            prefix.with_suffix('.command.json').write_text(json.dumps(command, indent=2) + '\n')
            try:
                result = subprocess.run(command, cwd=records[kind]['directory'],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace')
                stdout, stderr, code = result.stdout, result.stderr, result.returncode
                if code == 0 and not obj.is_file():
                    code, stderr = 1, stderr + '\nerror: compiler returned success without object\n'
            except OSError as error:
                stdout, stderr, code = '', str(error), 127
            prefix.with_suffix('.stdout.log').write_text(stdout)
            prefix.with_suffix('.stderr.log').write_text(stderr)
            row = {'source': str(path), 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                   'returncode': code, 'command': str(prefix.with_suffix('.command.json')),
                   'stderr': str(prefix.with_suffix('.stderr.log')),
                   'warning_groups': re.findall(r'\[(-W[^\]]+)\]', stderr)}
            rows.append(row)
            log.write(f'\n=== {path} (exit {code}) ===\n' + stdout + stderr + '\n')
            log.flush()
            print(f'{index + 1}/{len(work)} exit={code} {path.name}', flush=True)
    report = {'compile_only': True, 'linked': False, 'firmware_downloaded': False,
              'tree': str(tree), 'device': str(device), 'records': records,
              'results': rows, 'failed': sum(row['returncode'] != 0 for row in rows)}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"Complete: {len(rows)} units, {report['failed']} failed; {aggregate}")
    return int(report['failed'] != 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', type=Path, required=True)
    parser.add_argument('--device', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    return audit(args.tree, args.device, args.output)

if __name__ == '__main__':
    raise SystemExit(main())
