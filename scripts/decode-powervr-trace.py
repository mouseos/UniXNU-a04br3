#!/usr/bin/env python3
"""Decode "powervr trace NNN:" UART rows of package/powervr/NativePowerVR.cpp with the format table of the Linux header
package/powervr/linux/pvr_rogue_fwif_sf.h. Each record: id (ROGUE_FW_LOG_CREATESFID), 64-bit timestamp, parameters.
usage: decode-powervr-trace.py <UART log>"""
import re, sys
from pathlib import Path
src = (Path(__file__).resolve().parents[1] / 'package/powervr/linux/pvr_rogue_fwif_sf.h').read_text()
groups = re.findall(r'ROGUE_FW_GROUP_(\w+)', src[src.index('enum rogue_fw_log_sfgroups'):src.index('};', src.index('enum rogue_fw_log_sfgroups'))])
gid = {g: i for i, g in enumerate(groups)}
fmt = {}
for m in re.finditer(r'ROGUE_FW_LOG_CREATESFID\((\d+),\s*ROGUE_FW_GROUP_(\w+),\s*(\d+)\),\s*((?:"(?:[^"\\]|\\.)*"\s*)+)', src):
    text = ''.join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(4)))
    sid = int(m.group(1)) | (gid[m.group(2)] << 12) | (int(m.group(3)) << 16) | 0x70000000
    fmt[sid] = (int(m.group(3)), text)
words = []
for line in open(sys.argv[1], errors='replace'):
    m = re.search(r'powervr trace (\d+): ((?:[0-9a-f]{8} ?){8})', line)
    if m:
        base = int(m.group(1))
        if base == 0: words = []
        words += [int(x, 16) for x in m.group(2).split()]
i = 0
while i < len(words):
    w = words[i]
    if (w & 0xfff00000) == 0x70000000 and w in fmt:
        n, text = fmt[w]
        ts = words[i + 1] | (words[i + 2] << 32) if i + 2 < len(words) else 0
        args = words[i + 3:i + 3 + n]
        try:
            out = re.sub(r'%(\d*)ll?[xd]', '%x', text.replace('%llx', '%x'))
            print(f'{ts:>12x} ' + (out % tuple(args) if len(args) == n else text + f' {args}'))
        except (TypeError, ValueError):
            print(f'{ts:>12x} {text} {args}')
        i += 3 + n
    else:
        i += 1
