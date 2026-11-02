#!/usr/bin/env python3
"""Decode the a04br3 persistent boot log (package/A04BR3BootLog.c) into one evidence directory per boot snapshot.

Inputs:
  * a UART capture that contains the firmware reader's blocks (uefi-a04br3 A04BR3BootLogReaderDxe):
        A04BLOG-BEGIN <128-byte header in hex>
        A04BLOG <base64 of the message bytes>   (repeated)
        A04BLOG-END
    and "A04BLOG-NONE <reason>" when the reader found nothing;
  * or a raw copy of the 8 MiB window (--raw FILE): 8 slots of 1 MiB, each a 4 KiB header block and the message bytes.
Every header is checked (magic, version 2, header CRC) and the message bytes against log_crc32 before anything is written;
--force also saves a snapshot whose log CRC fails (marked in header.json). Output: OUT/boot<counter>-seq<sequence>/
{boot.log,header.json}; an existing directory with different content is never overwritten.
"""
import argparse
import base64
import binascii
import json
from pathlib import Path
import struct
import sys

MAGIC = b'A04BLOG1'
HEADER = struct.Struct('<8sIIQQQIIIIII40s20sI')
FIELDS = ('magic', 'version', 'header_bytes', 'boot_counter', 'sequence', 'uptime_ns', 'log_bytes', 'msgbuf_size',
          'log_crc32', 'flags', 'window_blocks', 'slot', 'kernel_uuid', 'reserved', 'header_crc32')
SLOT_BYTES, SLOTS, BLOCK = 1 << 20, 8, 4096
assert HEADER.size == 128


def parse_header(raw):
    if len(raw) < HEADER.size:
        raise ValueError('header shorter than 128 bytes')
    values = dict(zip(FIELDS, HEADER.unpack_from(raw)))
    if values['magic'] != MAGIC or values['version'] != 2 or values['header_bytes'] != 128:
        raise ValueError('not an A04BLOG1 version 2 header')
    if binascii.crc32(raw[:124]) & 0xffffffff != values['header_crc32']:
        raise ValueError('header CRC mismatch')
    values['magic'] = MAGIC.decode()
    values['kernel_uuid'] = values['kernel_uuid'].split(b'\0')[0].decode('ascii', 'replace')
    values.pop('reserved')
    values['flag_names'] = [name for bit, name in ((1, 'final'), (2, 'panic'), (4, 'write_error')) if values['flags'] & bit]
    return values


def save(out, header, data, force=False):
    if len(data) < header['log_bytes']:
        raise ValueError(f"log has {len(data)} of {header['log_bytes']} bytes")
    data = data[:header['log_bytes']]
    header['log_crc_ok'] = binascii.crc32(data) & 0xffffffff == header['log_crc32']
    if not header['log_crc_ok'] and not force:
        raise ValueError(f"boot {header['boot_counter']} sequence {header['sequence']}: log CRC mismatch")
    target = out / f"boot{header['boot_counter']:06d}-seq{header['sequence']:06d}"
    if (target / 'boot.log').is_file():
        if (target / 'boot.log').read_bytes() == data:
            return target
        target = target.with_name(target.name + f"-{binascii.crc32(data) & 0xffffffff:08x}")
    target.mkdir(parents=True, exist_ok=True)
    (target / 'boot.log').write_bytes(data)
    (target / 'header.json').write_text(json.dumps(header, indent=2) + '\n')
    return target


def from_uart(text):
    header, chunks, blocks, notes = None, [], [], []
    for line in text.splitlines():
        at = line.find('A04BLOG')
        if at < 0:
            continue
        line = line[at:].strip()
        if line.startswith('A04BLOG-BEGIN '):
            if header is not None:
                notes.append('a block without A04BLOG-END was dropped')
            header, chunks = bytes.fromhex(line.split()[1]), []
        elif line.startswith('A04BLOG-END'):
            if header is not None:
                blocks.append((header, b''.join(chunks)))
            header = None
        elif line.startswith('A04BLOG-NONE'):
            notes.append(line)
        elif line.startswith('A04BLOG ') and header is not None:
            chunks.append(base64.b64decode(line.split()[1]))
    if header is not None:
        notes.append('a block without A04BLOG-END was dropped')
    return blocks, notes


def from_raw(data):
    blocks = []
    for slot in range(SLOTS):
        base = slot * SLOT_BYTES
        if len(data) >= base + BLOCK and data[base:base + 8] == MAGIC:
            blocks.append((data[base:base + 128], data[base + BLOCK:base + SLOT_BYTES]))
    return blocks, []


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('input', type=Path, help='UART capture, or the raw window with --raw')
    parser.add_argument('--raw', action='store_true', help='the input is a raw copy of the 8 MiB window')
    parser.add_argument('--force', action='store_true', help='also save snapshots whose log CRC fails')
    parser.add_argument('--out', type=Path, default=Path('out/logs'))
    args = parser.parse_args()
    data = args.input.read_bytes()
    blocks, notes = from_raw(data) if args.raw else from_uart(data.decode('utf-8', 'replace'))
    for note in notes:
        print(note, file=sys.stderr)
    if not blocks:
        sys.exit('no boot log snapshot found')
    failed = 0
    for raw_header, payload in blocks:
        try:
            header = parse_header(raw_header)
            target = save(args.out, header, payload, args.force)
            print(f"{target}: boot {header['boot_counter']} sequence {header['sequence']} slot {header['slot']}, "
                  f"{header['log_bytes']} bytes, kernel {header['kernel_uuid']}, flags {header['flag_names']}")
        except ValueError as error:
            failed += 1
            print(f'rejected snapshot: {error}', file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()

