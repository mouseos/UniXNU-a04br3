#!/usr/bin/env python3
"""固定 OS/owner anchor 用の公開署名済み policy を取得する。秘密鍵・実機は不要。"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://github.com/mouseos/UniXNU-a04br3/releases/download/schema2-root-policy-21H461-v1/'
PINS = {
    'rootpolicy.img': 'd7ce433019f8950c7f7f6c8490e4e44a1510b8864d5e82f2542566890089cee2',
    'rootpolicy.json': 'e02ea68994437a6234c8bcf8e22d5c8a7e48fdfd918e729e6d154f3d3a6f5437',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--userdata-report', type=Path, required=True,
                        help='unixnu build userdata が生成した userdata-root.json')
    parser.add_argument('--source', type=Path, help='同じ固定ファイルのローカルキャッシュ（省略時は公開 release）')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('--out は新規ディレクトリを指定してください')
    report = json.loads(args.userdata_report.read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.out.parent) as temporary:
        stage = Path(temporary)
        for name, digest in PINS.items():
            target = stage / name
            if args.source:
                shutil.copyfile(args.source / name, target)
            else:
                with urllib.request.urlopen(BASE + name, timeout=120) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                sys.exit('固定 binary input のハッシュ不一致: ' + name)
        policy = json.loads((stage / 'rootpolicy.json').read_text())
        if policy['userdata_offset'] != report['image_bytes']:
            sys.exit('userdata の長さが固定 policy と一致しません')
        expected = {p['window']: (p['image_bytes'], p['image_sha256']) for p in policy['policies']}
        actual = {w['name']: (w['size'], w['sha256']) for w in report['windows']}
        if len(expected) != len(policy['policies']) or len(actual) != len(report['windows']):
            sys.exit('userdata/policy window 名が重複しています')
        if expected != actual:
            sys.exit('userdata window が固定 policy と一致しません')
        spec = importlib.util.spec_from_file_location('owner_manifest', ROOT / 'kits/VerifiedRoot/tools/manifest.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        anchor = hashlib.sha256(module.public_anchor(ROOT / 'package/root-key/owner-public.pem')).hexdigest()
        if anchor != policy['trusted_key_sha256']:
            sys.exit('カーネルの公開 owner anchor が固定 policy と一致しません')
        # Both immutable input hashes, OS windows and current public anchor matched.
        stage.rename(args.out)
    print(args.out)


if __name__ == '__main__':
    main()
