#!/usr/bin/env python3
"""Generate matched, opt-in root timing profiles from the recorded 7fb args."""
import argparse
import json

OMIT = ('ecb=1', 'mb=2', 'a04br3-gio=6', 'native-io-trace=1')
ADDED = ('native-root-timing=1', 'native-cs-read-batch=1')


def profiles(line, measurement='signature'):
    if measurement not in ('signature', 'iostat'):
        raise ValueError('unknown measurement')
    line.encode('ascii')
    args = line.split()
    keys = [arg.split('=', 1)[0] for arg in args]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate kernel argument keys')
    if any(args.count(arg) != 1 or keys.count(arg.split('=', 1)[0]) != 1 for arg in OMIT):
        raise ValueError('expected exactly one of each recorded diagnostic argument')
    forbidden = ('native-root-timing', 'native-msdc-iostat', 'native-cs-read-batch', 'native-cs-coalesce')
    if any(arg.split('=', 1)[0] in forbidden for arg in args):
        raise ValueError('timing, batch, and coalescing must be absent from source arguments')
    if measurement == 'iostat' and (args.count('a04br3-home-perf=1') != 1 or args.count('pl=1') != 1 or keys.count('a04br3-home-perf') != 1 or keys.count('pl') != 1 or 'a04br3-proclog' in keys):
        raise ValueError('iostat needs the existing home-perf and ten-second reporter gates')
    diagnostic = ADDED[0] if measurement == 'signature' else 'native-msdc-iostat=1'
    common = [arg for arg in args if arg not in OMIT] + [diagnostic]
    result = {'baseline': ' '.join(common), 'batch': ' '.join(common + [ADDED[1]])}
    if any(len(value.encode('ascii')) > 1023 for value in result.values()):
        raise ValueError('kernel arguments exceed the 1023-byte limit excluding NUL')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--measurement', choices=('signature','iostat'), default='signature')
    parser.add_argument('manifest', help='manifest containing the recorded kernel_args')
    args = parser.parse_args()
    with open(args.manifest, encoding='utf-8') as source:
        print(json.dumps(profiles(json.load(source)['kernel_args'], args.measurement), indent=2))


if __name__ == '__main__':
    main()
