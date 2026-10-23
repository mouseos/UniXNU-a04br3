#!/usr/bin/env python3
"""Screenshot of a running a04br3 kernel over UART0 (package/A04BR3AutoReset.cpp, with a04br3-autoreset set): send
"A04BR3!SHOT", collect the "A04BR3SHOT" rows from the UART log, keep the rows whose CRC (row number and pixels) matches,
and write a PNG. A try that misses rows is repeated (a whole new frame each time; the most complete one is kept). The frame
buffer is 32 bpp B, G, R, X (LK / uefi-a04br3 GOP). The kernel stops its reset timer and reset request check while it sends.

usage: a04br3-screenshot.py --log <file a running collector appends> [--uart /dev/ttyUSB0] [--tries 3] OUTPUT.png
       a04br3-screenshot.py --uart /dev/ttyUSB0 OUTPUT.png   (reads the port itself; stop other readers)"""
import argparse, base64, binascii, os, select, struct, subprocess, sys, time, zlib

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument('--uart', default='/dev/ttyUSB0')
p.add_argument('--log', help='UART log file appended by a running collector; without it the port is read directly')
p.add_argument('--tries', type=int, default=3)
p.add_argument('--timeout', type=float, default=180)
p.add_argument('output')
a = p.parse_args()
if not a.log:
    subprocess.run(['stty', '-F', a.uart, '921600', 'cs8', '-cstopb', '-parenb', 'raw', '-echo', 'clocal', 'min', '0', 'time', '2'], check=True)


def collect():
    rows, size = {}, None
    src = open(a.log, 'rb') if a.log else open(a.uart, 'rb', buffering=0)
    if a.log: src.seek(0, os.SEEK_END)
    with open(a.uart, 'wb', buffering=0) as port:
        port.write(b'\nA04BR3!SHOT\n')
    buf, deadline, done = b'', time.time() + a.timeout, False
    while time.time() < deadline and not done:
        if not a.log: select.select([src], [], [], 0.5)
        chunk = src.read(65536)
        if not chunk:
            time.sleep(0.2); continue
        buf += chunk
        *lines, buf = buf.split(b'\n')
        for raw in lines:
            i = raw.find(b'A04BR3SHOT ')
            if i < 0: continue
            f = raw[i:].strip().split(b' ')
            try:
                if f[1] == b'BEGIN' and len(f) >= 5:
                    size = (int(f[2]), int(f[3]))
                elif f[1] == b'END' and size:
                    done = True
                elif len(f) == 4 and f[1].isdigit() and size:
                    y = int(f[1])
                    coded = base64.b64decode(f[3], validate=True)
                    row = bytearray()
                    for j in range(0, len(coded) - 4, 5):
                        row += coded[j + 1:j + 5] * (coded[j] + 1)
                    crc = binascii.crc32(bytes(row), binascii.crc32(struct.pack('<I', y))) & 0xffffffff
                    if y < size[1] and len(row) == size[0] * 4 and '%08x' % crc == f[2].decode():
                        rows[y] = bytes(row)
            except (binascii.Error, ValueError, IndexError, UnicodeDecodeError):
                pass
    src.close()
    return rows, size


best, size = {}, None
for attempt in range(a.tries):
    rows, s = collect()
    if s: size = s
    if len(rows) > len(best): best = rows
    if size and len(best) == size[1]: break
    print(f'try {attempt + 1}: {len(rows)}/{size[1] if size else "?"} rows', file=sys.stderr)
if not size: sys.exit('no screenshot on UART (a04br3-autoreset not set, frame buffer not mapped, or the kernel is not running)')
w, h = size
blank = b'\0' * (w * 4)
out = bytearray()
for y in range(h):
    r = best.get(y, blank)
    rgb = bytearray(w * 3); rgb[0::3] = r[2::4]; rgb[1::3] = r[1::4]; rgb[2::3] = r[0::4]
    out += b'\0' + rgb
def chunk(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
open(a.output, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(bytes(out), 6)) + chunk(b'IEND', b''))
print(f'{a.output}: {w}x{h}, {len(best)}/{h} rows')

