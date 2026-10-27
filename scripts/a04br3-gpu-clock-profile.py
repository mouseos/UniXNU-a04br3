#!/usr/bin/env python3
"""既存の描画設定を保持したままGPUクロック比較用の引数を作成する。"""
import argparse
import json
import re
from pathlib import Path

def profile(line, mhz):
    args = line.split()
    if mhz not in (26, 380):
        raise ValueError('クロックは26または380 MHzを指定してください')
    if not args or any(not re.fullmatch(r'[A-Za-z0-9_.\-]+(=[A-Za-z0-9_.,\-]+)?', a) for a in args):
        raise ValueError('boot引数が不正です')
    keys = [a.split('=', 1)[0] for a in args]
    if len(keys) != len(set(keys)):
        raise ValueError('重複するboot引数があります')
    for required in ('offline-legacy-cpu-present=1', 'a04br3-gpu=2', 'a04br3-gpu-metal=1', 'sheng-gpu-addon=1'):
        if required not in args:
            raise ValueError('描画設定が不足しています: ' + required)
    args = [a for a in args if a.split('=', 1)[0] != 'a04br3-gpu-clock']
    extra = 'a04br3-gpu-clock=' + ('0' if mhz == 26 else '380')
    result = ' '.join(args + [extra])
    if len(result.encode('ascii')) > 1023:
        raise ValueError('boot引数が1023 bytesを超えます')
    return {'kernel_args': result, 'extra_kernel_arg': extra, 'requested_mhz': mhz}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mhz', type=int, choices=(26, 380), required=True)
    parser.add_argument('--args-file', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = profile(args.args_file.read_text(encoding='ascii'), args.mhz)
    except (ValueError, UnicodeError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
