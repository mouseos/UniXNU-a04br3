#!/usr/bin/env python3
"""Compare the a04br3 GPT (scripts/a04br3-gpt.json, written by make-a04br3-gpt.py) with the stock table of a full eMMC
user-area dump (for example the CT3 service image CT3-01.06.000.bin); no device access, nothing written to the dump.

Reads only the primary header and entry array of the dump (LBA 1 and the entry LBAs), checks both CRCs and the backup
header at the last LBA, then reports for every stock partition whether the a04br3 table keeps it at the same place,
moves or resizes it, or removes it, and checks the a04br3 table itself (geometry, overlaps, bounds, both copies).
usage: check-a04br3-gpt.py --stock DUMP [--json OUT] [--markdown OUT]
"""
import argparse
import importlib.util
import json
from pathlib import Path
import struct
import uuid
import zlib

HERE = Path(__file__).resolve().parent
HEADER = '<8sIIIIQQQQ16sQIII'
# Android-only partitions that iPadOS does not use (README "Partition layout"); every other stock partition stays where
# the stock bootloaders and the board code expect it. logo (the LK splash) must stay.
REMOVED = {'factory', 'metadata', 'system', 'cache'}
MUST_KEEP_IN_PLACE = {'proinfo', 'nvram', 'protect1', 'protect2', 'persist', 'seccfg', 'lk', 'lk2', 'recovery', 'para',
                      'logo', 'expdb', 'tee1', 'tee2', 'kb', 'dkb', 'flashinfo'}


def read_table(stream, lba, sector=512):
    stream.seek(lba * sector)
    raw = stream.read(sector)
    fields = struct.unpack_from(HEADER, raw)
    if fields[0] != b'EFI PART':
        raise SystemExit(f'no GPT header at LBA {lba}')
    size = fields[2]
    copy = bytearray(raw[:size]); struct.pack_into('<I', copy, 16, 0)
    if zlib.crc32(copy) != fields[3]:
        raise SystemExit(f'header CRC mismatch at LBA {lba}')
    stream.seek(fields[10] * sector)
    entries = stream.read(fields[11] * fields[12])
    if zlib.crc32(entries) != fields[13]:
        raise SystemExit(f'entry array CRC mismatch for the header at LBA {lba}')
    parts = []
    for i in range(fields[11]):
        e = entries[i * fields[12]:(i + 1) * fields[12]]
        if e[:16] == bytes(16):
            continue
        first, last, attributes = struct.unpack_from('<QQQ', e, 32)
        parts.append({'name': e[56:128].decode('utf-16le').rstrip('\0'), 'first_lba': first, 'sectors': last - first + 1,
                      'attributes': attributes, 'type_guid': str(uuid.UUID(bytes_le=e[:16])),
                      'unique_guid': str(uuid.UUID(bytes_le=e[16:32]))})
    return {'current': fields[5], 'backup': fields[6], 'first_usable': fields[7], 'last_usable': fields[8],
            'disk_guid': str(uuid.UUID(bytes_le=fields[9])), 'entry_lba': fields[10], 'entry_count': fields[11],
            'entry_bytes': fields[12], 'partitions': parts, 'entries_crc': fields[13]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--layout', type=Path, default=HERE / 'a04br3-gpt.json')
    parser.add_argument('--json', type=Path)
    parser.add_argument('--markdown', type=Path)
    args = parser.parse_args()
    layout = json.loads(args.layout.read_text())
    sector = layout['sector_bytes']
    disk_bytes = args.stock.stat().st_size
    if disk_bytes % sector or disk_bytes // sector != layout['disk_sectors']:
        raise SystemExit(f'dump size {disk_bytes} does not match disk_sectors {layout["disk_sectors"]}')
    with open(args.stock, 'rb') as stream:
        stock = read_table(stream, 1, sector)
        backup = read_table(stream, layout['disk_sectors'] - 1, sector)
    if backup['entries_crc'] != stock['entries_crc'] or backup['current'] != stock['backup']:
        raise SystemExit('stock backup GPT differs from the primary')
    spec = importlib.util.spec_from_file_location('gpt', HERE / 'make-a04br3-gpt.py')
    gpt = importlib.util.module_from_spec(spec); spec.loader.exec_module(gpt)
    primary, secondary, backup_offset = gpt.build(layout)   # raises on overlap, bounds or CRC errors
    new_header = struct.unpack_from(HEADER, primary, sector)
    if (new_header[7], new_header[8]) != (stock['first_usable'], stock['last_usable']):
        raise SystemExit('usable LBA range differs from the stock table')
    if backup_offset + len(secondary) != disk_bytes:
        raise SystemExit('secondary GPT does not end at the last LBA')
    ours = {p['name']: p for p in layout['partitions']}
    rows, errors = [], []
    for p in stock['partitions']:
        n = ours.get(p['name'])
        if n is None:
            state = 'removed'
            if p['name'] not in REMOVED:
                errors.append(f'{p["name"]}: removed but not an Android-only partition')
        elif (n['first_lba'], n['sectors']) == (p['first_lba'], p['sectors']):
            state = 'kept'
        else:
            state = 'moved/resized'
            if p['name'] in MUST_KEEP_IN_PLACE:
                errors.append(f'{p["name"]}: must stay in place')
        if n is not None and (n['type_guid'], n['unique_guid'], n['attributes']) != (p['type_guid'], p['unique_guid'], p['attributes']):
            errors.append(f'{p["name"]}: GUID or attributes changed')
        rows.append({'name': p['name'], 'stock_first_lba': p['first_lba'], 'stock_sectors': p['sectors'], 'state': state,
                     'a04br3_first_lba': n['first_lba'] if n else None, 'a04br3_sectors': n['sectors'] if n else None})
    for name in ours:
        if name not in {p['name'] for p in stock['partitions']}:
            errors.append(f'{name}: not in the stock table')
    if 'logo' not in ours:
        errors.append('logo must be kept')
    used = sorted((p['first_lba'], p['first_lba'] + p['sectors'] - 1, p['name']) for p in layout['partitions'])
    gaps = [(a[1] + 1, b[0] - 1) for a, b in zip(used, used[1:]) if b[0] > a[1] + 1]
    report = {'stock': {k: v for k, v in stock.items() if k != 'partitions'}, 'rows': rows, 'errors': errors,
              'a04br3': {'secondary_byte_offset': backup_offset, 'unallocated_ranges_lba': gaps}}
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + '\n')
    if args.markdown:
        mib = lambda s: f'{s * sector / 1048576:g}'
        lines = ['| partition | stock first LBA | stock MiB | a04br3 first LBA | a04br3 MiB | change |', '| --- | --- | --- | --- | --- | --- |']
        for r in rows:
            lines.append(f"| {r['name']} | {r['stock_first_lba']} | {mib(r['stock_sectors'])} | "
                         f"{r['a04br3_first_lba'] if r['a04br3_first_lba'] is not None else '-'} | "
                         f"{mib(r['a04br3_sectors']) if r['a04br3_sectors'] else '-'} | {r['state']} |")
        args.markdown.write_text('\n'.join(lines) + '\n')
    for r in rows:
        print(f"{r['name']:10} {r['state']:14} stock {r['stock_first_lba']:>9}+{r['stock_sectors']:<9} a04br3 "
              f"{r['a04br3_first_lba'] if r['a04br3_first_lba'] is not None else '-':>9}+{r['a04br3_sectors'] or '-'}")
    print('unallocated LBA ranges:', gaps)
    if errors:
        raise SystemExit('FAIL: ' + '; '.join(errors))
    print('PASS: a04br3 GPT matches the stock table except the documented changes; both copies verified')


if __name__ == '__main__':
    main()
