#!/usr/bin/env python3
"""Screenshot of a running a04br3 kernel over UART0 (package/A04BR3AutoReset.cpp, with a04br3-autoreset set): send
"A04BR3!SHOT", collect the "A04BR3SHOT" rows from the UART log, keep the rows whose CRC matches, ask again for missing ones
and write a PNG. The frame buffer is 32 bpp B, G, R, X (LK / uefi-a04br3 GOP).

usage: a04br3-screenshot.py --log <file a running collector appends> [--uart /dev/ttyUSB0] [--tries 3] OUTPUT.png
       a04br3-screenshot.py --uart /dev/ttyUSB0 OUTPUT.png   (reads the port itself; stop other readers)"""
import argparse, base64, binascii, os, struct, subprocess, sys, time, zlib

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument('--uart', default='/dev/ttyUSB0')
p.add_argument('--log', help='UART log file appended by a running collector; without it the port is read directly')
p.add_argument('--tries', type=int, default=3)
p.add_argument('--timeout', type=float, default=180)
p.add_argument('output')
a = p.parse_args()

subprocess.run(['stty', '-F', a.uart, '921600', 'cs8', '-cstopb', '-parenb', 'raw', '-echo', 'clocal'], check=True)
rows, size = {}, None


def collect():
    global size
    if a.log:
        src = open(a.log, 'rb'); src.seek(0, os.SEEK_END)
    else:
        src = open(a.uart, 'rb', buffering=0)
    with open(a.uart, 'wb', buffering=0) as port:
        port.write(b'\nA04BR3!SHOT\n')
    buf, deadline, done = b'', time.time() + a.timeout, False
    while time.time() < deadline and not done:
        chunk = src.read(65536)
        if not chunk:
            time.sleep(0.2); continue
        buf += chunk
        *lines, buf = buf.split(b'\n')
        for raw in lines:
            i = raw.find(b'A04BR3SHOT ')
            if i < 0: continue
            f = raw[i:].strip().split(b' ')
            if f[1] == b'BEGIN' and len(f) >= 5:
                size = (int(f[2]), int(f[3]))
            elif f[1] == b'END':
                done = True
            elif len(f) == 4 and f[1].isdigit():
                y = int(f[1])
                try:
                    coded = base64.b64decode(f[3], validate=True)
                    row = bytearray()
                    for j in range(0, len(coded) - 4, 5):
                        row += coded[j + 1:j + 5] * (coded[j] + 1)
                    if size and len(row) == size[0] * 4 and '%08x' % (binascii.crc32(row) & 0xffffffff) == f[2].decode():
                        rows[y] = bytes(row)
                except (binascii.Error, ValueError):
                    pass
    src.close()


for attempt in range(a.tries):
    collect()
    if size and len(rows) == size[1]: break
    print(f'attempt {attempt + 1}: {len(rows)}/{size[1] if size else "?"} rows', file=sys.stderr)
if not size: sys.exit('no screenshot on UART (a04br3-autoreset not set, or the kernel is not running)')
w, h = size
raw = b''.join(b'\0' + bytes(x for px in range(w) for x in (rows.get(y, b'\0' * w * 4)[px * 4 + 2], rows.get(y, b'\0' * w * 4)[px * 4 + 1], rows.get(y, b'\0' * w * 4)[px * 4])) for y in range(h))
def chunk(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
open(a.output, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))
print(f'{a.output}: {w}x{h}, {len(rows)}/{h} rows')

