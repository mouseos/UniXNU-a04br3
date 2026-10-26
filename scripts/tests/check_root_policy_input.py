#!/usr/bin/env python3
"""公開固定 policy 入力で取得器の拒否経路を検査する（実機なし）。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--policy-cache', type=Path, required=True)
parser.add_argument('--userdata-report', type=Path, required=True)
args = parser.parse_args()
script = Path(__file__).resolve().parents[1] / 'fetch-a04br3-root-policy.py'
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    good = json.loads(args.userdata_report.read_text())
    def run(report, label, success, source=args.policy_cache):
        path = root / (label + '.json')
        path.write_text(json.dumps(report))
        out = root / label
        result = subprocess.run([sys.executable, str(script), '--source', str(source),
                                 '--userdata-report', str(path), '--out', str(out)],
                                capture_output=True, text=True)
        assert (result.returncode == 0) == success, result.stderr
        assert out.exists() == success
    run(good, 'matching', True)
    wrong = json.loads(json.dumps(good)); wrong['image_bytes'] += 512
    run(wrong, 'wrong-length', False)
    wrong = json.loads(json.dumps(good)); wrong['windows'][0]['sha256'] = '0' * 64
    run(wrong, 'wrong-window', False)
    wrong = json.loads(json.dumps(good)); wrong['windows'].append(dict(wrong['windows'][0]))
    run(wrong, 'duplicate-window', False)
    broken = root / 'broken'; broken.mkdir()
    (broken / 'rootpolicy.img').write_bytes(b'corrupt')
    run(good, 'wrong-input', False, broken)
    # Existing directories are never replaced, even with valid inputs.
    result = subprocess.run([sys.executable, str(script), '--source', str(args.policy_cache),
                             '--userdata-report', str(args.userdata_report), '--out', str(root / 'matching')],
                            capture_output=True, text=True)
    assert result.returncode != 0
print('PASS fixed policy input: matching OS/anchor, wrong length/window/blob, no partial output or overwrite')
