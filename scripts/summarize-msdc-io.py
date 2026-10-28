#!/usr/bin/env python3
"""Summarize a single-boot UART log with opt-in MSDC I/O samples as JSON lines."""
import argparse
import json
import re
import sys

header = re.compile(r'a04br3 msdc io sample begin_ns=(\d+) end_ns=(\d+) non_atomic=1')
row = re.compile(r'a04br3 msdc transfer mode=(dma_read|pio_read|write) size=(le4k|4k_16k|gt16k) calls=(\d+) ok_bytes=(\d+) errors=(\d+) elapsed_ns=(\d+)')
expected = {(m, b) for m in ('dma_read','pio_read','write') for b in ('le4k','4k_16k','gt16k')}

def samples(lines):
    current = None
    for line in lines:
        h = header.search(line)
        if h:
            if current is not None:
                yield current
            current = {'begin':int(h[1]), 'end':int(h[2]), 'rows':{}, 'invalid':False}
            continue
        r = row.search(line)
        if r and current is not None:
            key = (r[1], r[2])
            if key in current['rows']:
                current['invalid'] = True
            current['rows'][key] = tuple(int(r[i]) for i in range(3, 7))
    if current is not None:
        yield current

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('uart_log', help='one boot only; do not concatenate reboots')
    args = parser.parse_args()
    previous = None
    last_time = None
    with open(args.uart_log, encoding='utf-8', errors='replace') as stream:
        for sample in samples(stream):
            begin, end, rows = sample['begin'], sample['end'], sample['rows']
            if end < begin or (last_time is not None and begin <= last_time):
                raise ValueError('non-monotonic sample timestamps: supply one complete boot per file')
            last_time = begin
            if sample['invalid'] or set(rows) != expected:
                print('incomplete or duplicate sample; interval discarded', file=sys.stderr)
                previous = None
                continue
            if previous is not None:
                dt = begin - previous['begin']
                deltas = {key: tuple(a-b for a,b in zip(rows[key], previous['rows'][key])) for key in expected}
                if any(v < 0 for values in deltas.values() for v in values):
                    raise ValueError('counter decreased: reboot, reset or mixed source; split the log')
                for mode, size in sorted(expected):
                    calls, byte_count, errors, elapsed = deltas[(mode,size)]
                    print(json.dumps({'begin_ns':previous['begin'], 'end_ns':begin, 'mode':mode, 'size':size,
                        'calls':calls, 'ok_bytes':byte_count, 'errors':errors, 'transport_ns':elapsed,
                        'observed_bytes_per_second':byte_count * 1_000_000_000 / dt}, separators=(',',':')))
            previous = sample

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        print(f'msdc iostat: {exc}', file=sys.stderr)
        sys.exit(2)
