#!/usr/bin/env python3
"""Decode the a04br3 persistent boot log (package/A04BR3BootLog.c) into one evidence directory per boot.

Inputs:
  * a UART capture that contains the firmware reader's blocks (uefi-a04br3, A04BR3_LOG_READER):
        A04BLOG-BEGIN <header hex>
        A04BLOG <base64 of the message bytes>   (repeated)
        A04BLOG-END
  * or a raw copy of the window (--raw FILE: block 0 header, blocks 1.. message bytes).
Every header is checked (magic, version, header CRC) and the message bytes against log_crc32 before anything is written.
Output: OUT/<boot_id>-<sequence>/{boot.log,header.json}. Nothing is written for a block that fails a check.
"""
import argparse
import base64
import binascii
import json
from pathlib import Path
import struct
import sys

MAGIC = b'A04BLOG1'
FIELDS = ('magic', 'version', 'header_bytes', 'boot_id', 'sequence', 'uptime_ns', 'log_bytes', 'msgbuf_size',
          'log_crc32', 'flags', 'window_blocks', 'header_crc32')


def parse_header(raw):
    if len(raw) < 64:
        raise ValueError('header shorter than 64 bytes')
    values = dict(zip(FIELDS, struct.unpack_from('<8sIIQQQIIIIII', raw)))
    if values['magic'] != MAGIC or values['version'] != 1 or values['header_bytes'] != 64:
        raise ValueError('not an A04BLOG1 version 1 header')
    if binascii.crc32(raw[:60]) & 0xffffffff != values['header_crc32']:
        raise ValueError('header CRC mismatch')
    values['magic'] = MAGIC.decode()
    return values


def save(out, header, data):
    if len(data) < header['log_bytes']:
        raise ValueError(f"log has {len(data)} of {header['log_bytes']} bytes")
    data = data[:header['log_bytes']]
    if binascii.crc32(data) & 0xffffffff != header['log_crc32']:
        raise ValueError('log CRC mismatch')
    target = out / f"{header['boot_id']:016x}-{header['sequence']:06d}"
    target.mkdir(parents=True, exist_ok=True)
    (target / 'boot.log').write_bytes(data)
    (target / 'header.json').write_text(json.dumps(header, indent=2) + '\n')
    return target


def from_uart(text):
    header, chunks, blocks = None, [], []
    for line in text.splitlines():
        line = line.strip()
        at = line.find('A04BLOG')
        if at < 0:
            continue
        line = line[at:]
        if line.startswith('A04BLOG-BEGIN '):
            header, chunks = bytes.fromhex(line.split()[1]), []
        elif line.startswith('A04BLOG-END') and header is not None:
            blocks.append((header, b''.join(chunks)))
            header = None
        elif line.startswith('A04BLOG ') and header is not None:
            chunks.append(base64.b64decode(line.split()[1]))
    return blocks


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('input', type=Path, help='UART capture, or the raw window with --raw')
    parser.add_argument('--raw', action='store_true', help='the input is a raw copy of the window')
    parser.add_argument('--out', type=Path, default=Path('out/logs'))
    args = parser.parse_args()
    data = args.input.read_bytes()
    blocks = [(data[:64], data[4096:])] if args.raw else from_uart(data.decode('utf-8', 'replace'))
    if not blocks:
        sys.exit('no boot log block found')
    failed = 0
    for raw_header, payload in blocks:
        try:
            header = parse_header(raw_header)
            print(f"{save(args.out, header, payload)}: boot {header['boot_id']:016x} sequence {header['sequence']}, "
                  f"{header['log_bytes']} bytes")
        except ValueError as error:
            failed += 1
            print(f'rejected block: {error}', file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()

