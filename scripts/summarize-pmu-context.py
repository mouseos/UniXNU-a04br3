#!/usr/bin/env python3
"""Join opt-in PMU context snapshots to a separately boot-verified timeline.

Times are CNTVCT ticks; comparison assumes a common counter across CPUs. The
input timeline must have been validated against this same first-boot raw file.
PLL fields are raw, load fields are non-atomic cumulative 32-bit ticks; neither
proves what happened between brackets. No CPU voltage or temperature is inferred.
"""
import argparse,csv,hashlib,json,re,sys
from pathlib import Path

BASE=re.compile(r'a04br3 cpu-context round=(\d+) cpu=(\d+) phase=(\d+) begin=(\d+) end=(\d+) clock=(\d+) load=(\d+) rail=unknown temp=unknown')
CLK=re.compile(r'a04br3 cpu-context-clk round=(\d+) cpu=(\d+) phase=(\d+) infra=([0-9a-fA-F]+) dcm=([0-9a-fA-F]+) con0=([0-9a-fA-F]+) con1=([0-9a-fA-F]+) pll_pwr=([0-9a-fA-F]+)')
LOAD=re.compile(r'a04br3 cpu-context-load round=(\d+) cpu=(\d+) phase=(\d+) user=(\d+) system=(\d+) idle=(\d+) nice=(\d+) state=(\d+)/(\d+) ticks32=1')

def join_context(raw, timeline, skips=()):
 issues=[];snaps={}
 for pat,kind,names in ((BASE,'base',('begin','end','clock','load')),(CLK,'clock',('infra','dcm','con0','con1','pll_pwr')),(LOAD,'load',('user','system','idle','nice','state_before','state_after'))):
  for m in pat.finditer(raw):
   g=m.groups();key=tuple(map(int,g[:3]));v={n:(x.lower() if kind=='clock' else int(x)) for n,x in zip(names,g[3:])}
   if key[2] not in (0,1):issues.append('invalid phase '+str(key));continue
   slot=snaps.setdefault(key,{})
   if kind in slot:issues.append('duplicate '+kind+' '+str(key))
   else:slot[kind]=v
 records=[];keys=set();skipkeys={}
 for skip in skips:
  key=tuple(skip['attempt']) if skip.get('attempt') is not None else None
  if key is None:issues.append('skip without attempt');continue
  if key in skipkeys:issues.append('duplicate skip '+str(key))
  skipkeys[key]=skip
 for entry in timeline:
  key=(entry['round'],entry['cpu']);
  if key in keys:issues.append('duplicate measurement '+str(key))
  keys.add(key);item={'measurement':entry,'before':snaps.get((*key,0)),'after':snaps.get((*key,1)),'rail':'unknown','temperature':'unknown'}
  if entry['status']==0:
   for phase in (0,1):
    snap=snaps.get((*key,phase));label=str((*key,phase))
    if not snap or 'base' not in snap:issues.append('missing context '+label);continue
    base=snap['base']
    if base['end']<base['begin']:issues.append('context timer reversed '+label)
    for kind in ('clock','load'):
     if bool(base[kind])!=(kind in snap):issues.append('context availability mismatch '+kind+' '+label)
    if phase==0 and base['end']>entry['begin']:issues.append('before context overlaps window '+label)
    if phase==1 and base['begin']<entry['end']:issues.append('after context overlaps window '+label)
  records.append(item)
 for key,skip in skipkeys.items():
  if key in keys:issues.append('measurement and skip overlap '+str(key));continue
  before=snaps.get((*key,0));after=snaps.get((*key,1))
  if after:issues.append('post context on skipped attempt '+str(key))
  records.append({'attempt':list(key),'skip':skip,'before':before,'after':after,'rail':'unknown','temperature':'unknown'})
 for round_,cpu,phase in snaps:
  if (round_,cpu) not in keys and (round_,cpu) not in skipkeys:issues.append('context without measurement or skip '+str((round_,cpu,phase)))
 return {'records':records,'issues':issues,'context_count':len(snaps),'limits':['non-atomic snapshots','PLL values are raw, not a measurement of CPU frequency or voltage','load ticks are cumulative and not simultaneous with PMU active intervals','temperature and CPU voltage unavailable','context logging perturbs scheduling','counter comparison assumes synchronized CNTVCT across CPUs']}

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('raw',type=Path);ap.add_argument('timeline_json',type=Path);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
 timeline=json.loads(args.timeline_json.read_text());raw=args.raw.read_bytes();n=timeline['snapshot_bytes']
 if len(raw)<n or hashlib.sha256(raw[:n]).hexdigest()!=timeline['snapshot_sha256']:raise ValueError('raw differs from verified timeline snapshot')
 result=join_context(raw[:timeline['first_boot_bytes']].decode('utf8','replace'),timeline['records'],timeline.get('skips',[]));result['source_sha256']=timeline['snapshot_sha256'];result['boot_sha256']=timeline['boot_sha256'];args.output.write_text(json.dumps(result,indent=2)+'\n');print('context snapshots',result['context_count'],'issues',len(result['issues']));return 1 if result['issues'] else 0
if __name__=='__main__':sys.exit(main())
