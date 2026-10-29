#!/usr/bin/env python3
"""Summarize an identified, single-boot a04br3 UART capture as JSON lines."""
import argparse
import hashlib
import json
import re
import sys

header = re.compile(r'a04br3 msdc io sample seq=(\d+) begin_ns=(\d+) end_ns=(\d+) non_atomic=1\Z')
transfer = re.compile(r'a04br3 msdc transfer seq=(\d+) mode=(dma_read|pio_read|write) size=(le4k|4k_16k|gt16k) calls=(\d+) ok_bytes=(\d+) errors=(\d+) elapsed_ns=(\d+)\Z')
offer = re.compile(r'a04br3 msdc offered seq=(\d+) mode=(read|write) size=(le4k|4k_16k|gt16k) calls=(\d+) bytes=(\d+)\Z')
queue = re.compile(r'a04br3 msdc queue seq=(\d+) started=(\d+) wait_ns=(\d+) max_wait_ns=(\d+)\Z')
end_marker = re.compile(r'a04br3 msdc io end seq=(\d+) rows=(\d+)\Z')
transport_keys = [('transfer',m,b) for m in ('dma_read','pio_read','write') for b in ('le4k','4k_16k','gt16k')]
offer_keys = [('offered',m,b) for m in ('read','write') for b in ('le4k','4k_16k','gt16k')]
expected = transport_keys + offer_keys + [('queue','','')]

anchor = b'[pmic6392_init] Preloader Start'
bldr = b'[BLDR] Build Time:'
reason = re.compile(rb'(?i)\bBOOT_REASON[ \t]*[:=]')
sample_marker = b'a04br3 msdc io sample seq='
record_marker = 'a04br3 msdc '
# Captures may have no prefix, or one numeric UART/kernel timestamp.
uart_prefix = re.compile(r'(?:\[[ \t]*[0-9]+(?:\.[0-9]+)?\][ \t]*)?\Z')

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def verify_source(data, source_hash, offset, boot_id):
    if not re.fullmatch(r'[0-9a-f]{64}', source_hash) or sha256(data) != source_hash:
        raise ValueError('source SHA-256 mismatch')
    if offset < 0 or data.find(anchor) != offset or data.count(anchor) != 1:
        raise ValueError('boot start offset must identify the only preloader anchor in the source')
    if data.count(bldr) != 1 or data.find(bldr) < offset:
        raise ValueError('missing or repeated BLDR boot boundary')
    segment = data[offset:]
    if not re.fullmatch(r'[0-9a-f]{64}', boot_id) or sha256(segment) != boot_id:
        raise ValueError('boot segment SHA-256 mismatch')
    first_sample = data.find(sample_marker)
    if first_sample < 0 or first_sample < data.find(bldr):
        raise ValueError('missing sample after boot anchor')
    reasons = list(reason.finditer(data))
    if not reasons or any(x.start() < offset or x.start() >= first_sample for x in reasons):
        raise ValueError('missing boot reason or boot boundary after sampling began')
    return segment

def uart_lines(segment):
    # UART framing is LF, optionally preceded by exactly one CR. Python's
    # splitlines() also recognizes VT, FF, CR and Unicode separators; those
    # must never split or silently truncate a diagnostic record.
    parts = segment.split(b'\n')
    marker = record_marker.encode('ascii')
    for index, raw in enumerate(parts):
        if marker in raw:
            if index == len(parts) - 1:
                raise ValueError('unterminated MSDC UART record')
            if raw.endswith(b'\r'):
                raw = raw[:-1]
            if any((byte < 32 and byte != 9) or byte == 127 for byte in raw):
                raise ValueError('unexpected control character in MSDC UART record')
            try:
                line = raw.decode('ascii')
            except UnicodeDecodeError as exc:
                raise ValueError('non-ASCII character in MSDC UART record') from exc
            if '\t' in line[line.index(record_marker):]:
                raise ValueError('unexpected control character in MSDC UART record')
            yield line
        else:
            # Other UART lines can contain binary boot noise. They are not
            # interpreted as MSDC records, and LF remains the only delimiter.
            yield raw.decode('utf-8', errors='replace')

def samples(lines):
    current = None
    last_seq = 0
    for line in lines:
        occurrences = line.count(record_marker)
        if occurrences > 1:
            raise ValueError('multiple MSDC records joined on one line')
        if occurrences == 1:
            position = line.index(record_marker)
            if not uart_prefix.fullmatch(line[:position]):
                raise ValueError('unrecognized UART prefix before MSDC record')
            line = line[position:]
        h = header.search(line)
        if h:
            if current is not None:
                raise ValueError('missing sample end before next header')
            seq = int(h[1])
            if seq != last_seq + 1:
                raise ValueError('missing, duplicate or out-of-order sample sequence')
            current = {'seq':seq, 'begin':int(h[2]), 'end':int(h[3]), 'rows':{}}
            continue
        if 'a04br3 msdc io sample' in line:
            raise ValueError('malformed sample header')
        entry = None
        for label, pattern in (('transfer',transfer), ('offered',offer), ('queue',queue)):
            match = pattern.search(line)
            if match:
                if label == 'queue':
                    entry = (int(match[1]), (label,'',''), tuple(int(match[i]) for i in range(2,5)))
                else:
                    entry = (int(match[1]), (label,match[2],match[3]), tuple(int(x) for x in match.groups()[3:]))
                break
            if 'a04br3 msdc ' + label + ' ' in line:
                raise ValueError('malformed or unavailable ' + label + ' row')
        if entry:
            seq, key, values = entry
            if current is None or seq != current['seq']:
                raise ValueError('orphan or mixed sample row')
            position = len(current['rows'])
            if position >= len(expected) or key != expected[position]:
                raise ValueError('missing, duplicate or out-of-order sample row')
            current['rows'][key] = values
        e = end_marker.search(line)
        if e:
            if current is None or int(e[1]) != current['seq'] or int(e[2]) != len(expected) or len(current['rows']) != len(expected):
                raise ValueError('invalid or incomplete sample end')
            yield current
            last_seq = current['seq']
            current = None
        elif 'a04br3 msdc io end ' in line:
            raise ValueError('malformed sample end')
    if current is not None:
        raise ValueError('missing sample end at EOF')

def summarize(segment, source_hash, boot_id, offset):
    previous = None
    last_end = None
    output = []
    for sample in samples(uart_lines(segment)):
        begin, end, rows = sample['begin'], sample['end'], sample['rows']
        if end < begin or (last_end is not None and begin < last_end) or (previous is not None and begin <= previous['begin']):
            raise ValueError('overlapping or non-monotonic sample timestamps')
        last_end = end
        if previous is not None:
            dt = begin - previous['begin']
            deltas = {key: tuple(a-b for a,b in zip(rows[key], previous['rows'][key])) for key in expected}
            if any(v < 0 for values in deltas.values() for v in values):
                raise ValueError('counter decreased: reboot, reset or mixed source')
            common = {'source_sha256':source_hash, 'boot_segment_sha256':boot_id,
                      'boot_start_offset':offset, 'from_seq':previous['seq'], 'to_seq':sample['seq'],
                      'begin_ns':previous['begin'], 'end_ns':begin,
                      'from_sample_begin_ns':previous['begin'], 'from_sample_end_ns':previous['end'],
                      'to_sample_begin_ns':begin, 'to_sample_end_ns':end,
                      'sample_non_atomic':True, 'rate_interval_basis':'sample_begin_to_sample_begin'}
            for key in transport_keys:
                calls, byte_count, errors, elapsed = deltas[key]
                output.append({**common, 'kind':'transfer', 'mode':key[1], 'size':key[2],
                    'activity_observed':any((calls,byte_count,errors,elapsed)), 'calls':calls, 'ok_bytes':byte_count,
                    'errors':errors, 'transport_ns':elapsed,
                    'observed_bytes_per_second':byte_count * 1_000_000_000 / dt})
            for key in offer_keys:
                calls, byte_count = deltas[key]
                output.append({**common, 'kind':'offered', 'mode':key[1], 'size':key[2],
                    'activity_observed':any((calls,byte_count)), 'calls':calls, 'bytes':byte_count})
            key = ('queue','','')
            started, wait_ns, max_delta = deltas[key]
            output.append({**common, 'kind':'queue', 'activity_observed':any((started,wait_ns,max_delta)),
                'started':started, 'wait_ns':wait_ns, 'max_wait_ns_cumulative':rows[key][2]})
        previous = sample
    if not output:
        raise ValueError('at least two complete samples are required')
    return output

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('uart_log', help='one complete boot capture, with a preloader anchor')
    parser.add_argument('--source-sha256', required=True, help='SHA-256 of the complete source file')
    parser.add_argument('--boot-start-offset', required=True, type=int, help='byte offset of preloader anchor')
    parser.add_argument('--boot-id', required=True, help='SHA-256 of source bytes from the anchor through EOF')
    args = parser.parse_args()
    with open(args.uart_log, 'rb') as stream:
        data = stream.read()
    segment = verify_source(data, args.source_sha256, args.boot_start_offset, args.boot_id)
    output = summarize(segment, args.source_sha256, args.boot_id, args.boot_start_offset)
    # All source, boundary, timestamp and counter checks precede any JSON output.
    rendered = [json.dumps(x, separators=(',',':'), allow_nan=False) for x in output]
    if rendered:
        sys.stdout.write('\n'.join(rendered) + '\n')

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, OverflowError) as exc:
        print(f'msdc iostat: {exc}', file=sys.stderr)
        sys.exit(2)
