#!/usr/bin/env python3
"""Check an a04br3 boot image against what the stock LK needs before it is written or booted.

The stock LK (CT3-01.06.000) stops in a loop, before the firmware can set the fastboot flag, when the image is not an
Android boot image v0 with 2048-byte pages, when the kernel lacks the MediaTek KERNEL header, the gzip stream or the
appended device tree, or when the ramdisk lacks the ROOTFS header. Such an image would need mtkclient to recover, so the
flash and unattended scripts refuse it. Exit status 0 when the image passes.
"""
import argparse
import struct
import sys
import zlib
from pathlib import Path

MTK_MAGIC = 0x58881688
LIMIT = 128 << 20          # boot partition and LK max-download-size


def fail(message):
    raise SystemExit(f'boot image check: {message}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    data = parser.parse_args().image.read_bytes()
    if len(data) < 2048:
        fail('truncated Android boot header')
    if len(data) > LIMIT:
        fail(f'{len(data)} bytes exceed 128 MiB')
    if data[:8] != b'ANDROID!':
        fail('no ANDROID! magic')
    kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, _, _, tags_addr, page = struct.unpack_from('<8I', data, 8)
    if page != 2048 or kernel_addr != 0x40080000 or ramdisk_addr != 0x60000000 or tags_addr != 0x4E000000:
        fail(f'header page {page} kernel {kernel_addr:#x} ramdisk {ramdisk_addr:#x} tags {tags_addr:#x}')
    if not data[64:64 + 512].startswith(b'bootopt=64S3,32N2,64N2'):
        fail('command line does not start with bootopt=64S3,32N2,64N2')
    if kernel_size < 512 or page + kernel_size > len(data):
        fail('truncated kernel payload')
    kernel = data[page:page + kernel_size]
    magic, size = struct.unpack_from('<II', kernel, 0)
    if magic != MTK_MAGIC or kernel[8:14] != b'KERNEL' or size + 512 != kernel_size:
        fail('kernel lacks the MediaTek KERNEL header')
    stream = zlib.decompressobj(31)
    body = stream.decompress(kernel[512:])
    if not stream.eof or not body:
        fail('kernel payload is not one gzip stream')
    if b'\xd0\x0d\xfe\xed' not in stream.unused_data[:4096]:
        fail('no device tree after the gzip stream')
    ramdisk_offset = page + (kernel_size + page - 1) // page * page
    if ramdisk_size < 512 or ramdisk_offset + ramdisk_size > len(data):
        fail('truncated ramdisk payload')
    ramdisk = data[ramdisk_offset:ramdisk_offset + ramdisk_size]
    magic, size = struct.unpack_from('<II', ramdisk, 0)
    if magic != MTK_MAGIC or ramdisk[8:14] != b'ROOTFS' or size + 512 != ramdisk_size or size > 0x08000000:
        fail('ramdisk lacks the ROOTFS header or exceeds the 128 MiB Boot Ramdisk region')
    print(f'boot image check: ok ({len(data)} bytes, firmware {len(body)} bytes, payload {size} bytes)')


if __name__ == '__main__':
    main()
