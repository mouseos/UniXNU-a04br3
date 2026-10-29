#!/usr/bin/env python3
"""Validate read-only clock samples against a boot-verified PMU timeline.

These samples perturb execution, are non-atomic, and do not prove the clock
remained unchanged between reads. Timer and PMU ownership are not calibrated.
"""
import argparse, hashlib, json, re, sys
from pathlib import Path

PATTERN=re.compile(r'a04br3 cpu-near-clock round=(\d+) cpu=(\d+) phase=(\d+) ok=(\d+) safe=(\d+) begin=(\d+) end=(\d+) infra=([0-9a-f]+) dcm=([0-9a-f]+) con0=([0-9a-f]+) con1=([0-9a-f]+) pwr=([0-9a-f]+)(?!\S)')

def summarize(raw, timeline):
    issues=[]; entries={}; rows=[]
    if hashlib.sha256(raw).hexdigest()!=timeline.get('snapshot_sha256'): raise ValueError('raw/timeline hash mismatch')
    if timeline.get('issues'): raise ValueError('timeline has unresolved issues')
    for line in raw.decode('utf-8','replace').splitlines():
        if 'a04br3 cpu-near-clock ' not in line: continue
        m=PATTERN.search(line)
        if not m: issues.append('malformed near-clock record'); continue
        a=m.groups(); key=tuple(map(int,a[:3])); ok=int(a[3]); safe=int(a[4]); begin=int(a[5]); end=int(a[6]); values=a[7:]
        if key[2] not in (0,1) or ok not in (0,1) or safe not in (0,1): issues.append(f'invalid phase/availability {key}'); continue
        if key in entries: issues.append(f'duplicate {key}'); continue
        entries[key]={'ok':ok,'safe':safe,'begin':begin,'end':end,'raw':dict(zip(('infra','dcm','con0','con1','pwr'),values))}
    expected=set()
    for w in timeline['records']:
        if w['status']!=0: continue
        key=(w['round'],w['cpu']); expected.update((key+(0,),key+(1,)))
        b=entries.get(key+(0,)); e=entries.get(key+(1,))
        if not b or not e or b['ok']!=1 or e['ok']!=1 or b['safe']!=1 or e['safe']!=1: issues.append(f'missing/unavailable {key}'); continue
        if not (w['begin']<=b['begin']<=b['end']<=e['begin']<=e['end']<=w['end']): issues.append(f'outside/order {key}')
        rows.append({'round':key[0],'cpu':key[1],'before':b,'after':e,'different':b['raw']!=e['raw'],'begin_duration_ticks':b['end']-b['begin'],'end_duration_ticks':e['end']-e['begin'],'overhead_fraction_upper':((b['end']-b['begin'])+(e['end']-e['begin']))/w['min_ticks']})
    for key in entries.keys()-expected:
        if not any(w['round']==key[0] and w['cpu']==key[1] for w in timeline['records']): issues.append(f'orphan {key}')
    return {'source_sha256':hashlib.sha256(raw).hexdigest(),'boot_sha256':timeline.get('boot_sha256'),'records':rows,'issues':issues,'limits':['read-only MMIO perturbs the measured interval','timestamps use uncalibrated CNTVCT','snapshots non-atomic; no proof of intervening stability','higher EL and debug PMU ownership unverified']}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('raw',type=Path);ap.add_argument('timeline',type=Path);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    result=summarize(a.raw.read_bytes(),json.loads(a.timeline.read_text()))
    with a.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print('near-clock pairs',len(result['records']),'issues',len(result['issues']))
    return bool(result['issues'])
if __name__=='__main__':sys.exit(main())
