#!/usr/bin/env python3
"""Build the original trust cache bundle of the board's IPSW without downloading the whole image.

Reads BuildManifest.plist and the two trust cache IM4Ps from the IPSW url of board.toml with HTTP range requests, places
them where the base script scripts/build-original-system-trust.py expects them (a member-only copy of the IPSW zip and the
StaticTrustCache payload), runs that script unchanged and checks the bundle against [payload] trust_cache_sha256.
Use from the macOS workflow's pre_build:
  python3 "$DEVICE_DIR/scripts/build-a04br3-trust-cache.py" --tree "$PWD" --out "$RUNNER_TEMP/trustcache"
"""
import argparse
import hashlib
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
import zlib
from pathlib import Path

DEVICE = Path(__file__).resolve().parents[1]


def der(data, at):
    tag, size = data[at:at + 2]
    at += 2
    if size & 128:
        count = size & 127
        size = int.from_bytes(data[at:at + count], 'big')
        at += count
    return tag, data[at:at + size], at + size


class Remote:
    def __init__(self, url):
        self.url = url
        head = urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=60)
        self.size = int(head.headers['Content-Length'])
        tail = self.range(self.size - 65536, self.size - 1)
        end = tail.rfind(b'PK\x05\x06')
        cd_size, cd_offset = struct.unpack_from('<II', tail, end + 12)
        if cd_offset == 0xffffffff:
            end64 = tail.rfind(b'PK\x06\x06')
            cd_size, cd_offset = struct.unpack_from('<QQ', tail, end64 + 40)
        directory = self.range(cd_offset, cd_offset + cd_size - 1)
        self.members, at = {}, 0
        while directory[at:at + 4] == b'PK\x01\x02':
            method, = struct.unpack_from('<H', directory, at + 10)
            csize, usize = struct.unpack_from('<II', directory, at + 20)
            name_len, extra_len, comment_len = struct.unpack_from('<HHH', directory, at + 28)
            local, = struct.unpack_from('<I', directory, at + 42)
            name = directory[at + 46:at + 46 + name_len].decode()
            extra = directory[at + 46 + name_len:at + 46 + name_len + extra_len]
            cursor = 0
            while cursor < len(extra):
                header, size = struct.unpack_from('<HH', extra, cursor)
                if header == 1:
                    values, index = struct.unpack_from('<' + 'Q' * (size // 8), extra, cursor + 4), 0
                    if usize == 0xffffffff: usize, index = values[index], index + 1
                    if csize == 0xffffffff: csize, index = values[index], index + 1
                    if local == 0xffffffff: local = values[index]
                cursor += 4 + size
            self.members[name] = (method, csize, local)
            at += 46 + name_len + extra_len + comment_len

    def range(self, first, last):
        request = urllib.request.Request(self.url, headers={'Range': f'bytes={first}-{last}'})
        return urllib.request.urlopen(request, timeout=120).read()

    def read(self, name):
        method, csize, local = self.members[name]
        name_len, extra_len = struct.unpack_from('<HH', self.range(local, local + 29), 26)
        data = self.range(local + 30 + name_len + extra_len, local + 30 + name_len + extra_len + csize - 1)
        return data if method == 0 else zlib.decompress(data, -15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', type=Path, required=True, help='UniXNU checkout (for scripts/build-original-system-trust.py)')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    board = tomllib.loads((DEVICE / 'board.toml').read_text())
    remote = Remote(board['ipsw']['url'])
    names = ['BuildManifest.plist'] + [n for n in remote.members if n.startswith('Firmware/') and n.endswith('.dmg.trustcache')]
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        (work / 'refer').mkdir()
        (work / 'scripts').mkdir()
        ipsw_name = board['ipsw']['url'].rsplit('/', 1)[1]
        with zipfile.ZipFile(work / 'refer' / ipsw_name, 'w') as archive:
            for name in names:
                archive.writestr(name, remote.read(name))
        # The StaticTrustCache member of the board's identity in the BuildManifest (not the order of [ipsw.sha256]).
        import plistlib
        manifest = plistlib.loads(remote.read('BuildManifest.plist'))
        identity = next(i for i in manifest['BuildIdentities'] if i['Info']['DeviceClass'] == board['ipsw']['device_class'])
        static_name = identity['Manifest']['StaticTrustCache']['Info']['Path']
        tag, sequence, _ = der(remote.read(static_name), 0)
        fields, cursor = [], 0
        while cursor < len(sequence):
            kind, data, cursor = der(sequence, cursor)
            fields.append(data)
        if fields[0] != b'IM4P':
            raise SystemExit(f'{static_name}: not an IM4P')
        (work / '.cache/firmware/target-volumes').mkdir(parents=True)
        (work / '.cache/firmware/target-volumes/static-trustcache.bin').write_bytes(fields[3])
        shutil.copy(args.tree / 'scripts/build-original-system-trust.py', work / 'scripts')
        subprocess.run([sys.executable, 'scripts/build-original-system-trust.py'], cwd=work, check=True)
        bundle = (work / 'build/original-static-system-trust/original-static-system.bundle').read_bytes()
    digest = hashlib.sha256(bundle).hexdigest()
    if digest not in board['payload']['trust_cache_sha256']:
        raise SystemExit(f'trust cache bundle {digest} is not pinned in board.toml')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(bundle)
    print(f'trust cache bundle {args.out} sha256 {digest}')


if __name__ == '__main__':
    main()
