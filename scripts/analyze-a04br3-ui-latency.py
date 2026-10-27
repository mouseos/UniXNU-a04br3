#!/usr/bin/env python3
"""Offline same-boot raw-tick timeline. Temporal candidates are not causal links."""
from pathlib import Path
import argparse,hashlib,json,re

def analyze(raw):
    text=raw.decode(errors='replace')
    if text.count('Darwin Kernel Version')!=1:
        raise ValueError('Select exactly one XNU epoch before analysis')
    events=[]; bases=set(); losses=[];kernel_base=None
    for line_no,line in enumerate(text.split('\n'),1):
        km=re.search(r'UIK clock n(\d+) d(\d+)',line)
        if km:kernel_base=tuple(map(int,km.groups()))
        m=re.search(r'UIL p(\d+) clock n(\d+) d(\d+)',line)
        if m:
            _,n,d=map(int,m.groups())
            if not n or not d: raise ValueError('Invalid timebase')
            bases.add((n,d))
        m=re.search(r'(UI[KL])(?: p(\d+))? e(\d+) i([0-9a-f]+) t([0-9a-f]+)',line)
        if m:
            scope,pid,kind,ident,tick=m.groups()
            events.append(dict(scope=scope,pid=int(pid or 0),kind=int(kind),id=int(ident,16),tick=int(tick,16),line=line_no))
        if re.search(r'UI[KL] lost [1-9]',line): losses.append(dict(line=line_no,text=line.strip()))
    if len(bases)!=1: raise ValueError('Need one consistent userspace mach timebase')
    if not events: raise ValueError('No timestamp records')
    n,d=next(iter(bases));events.sort(key=lambda e:(e['tick'],e['line']))
    groups={};intervals=[]
    for e in events:
        key=(e['scope'],e['pid'],e['id']);groups.setdefault(key,{}).setdefault(e['kind'],[]).append(e)
    pairs={'UIK':[(1,3,'input_down_sink'),(2,4,'input_up_sink'),(30,31,'offer_mapping'),(31,32,'delivered_to_complete_request'),(32,33,'legacy_complete_call'),(30,33,'offer_to_legacy_return')],
           'UIL':[(10,12,'render_commit_call'),(10,11,'render_commit_to_callback'),(20,22,'other_commit_call'),(20,21,'other_commit_to_callback')]}
    for (scope,pid,ident),kinds in groups.items():
        for a,b,label in pairs[scope]:
            if len(kinds.get(a,[]))==1 and len(kinds.get(b,[]))==1:
                first,last=kinds[a][0],kinds[b][0]
                if last['tick']<first['tick']:raise ValueError('Nonmonotonic same-operation timestamps')
                intervals.append(dict(label=label,scope=scope,pid=pid,id=ident,ms=(last['tick']-first['tick'])*n/d/1e6,lines=[first['line'],last['line']]))
    # User COMPLETE_SWAP begin/end brackets the actual kernel receive timestamp.
    # Offset is kernel ticks minus user ticks, bounded rather than assumed zero.
    calibration=[]
    for (scope,pid,ident),kinds in groups.items():
        kernel=groups.get(('UIK',0,ident),{})
        if scope=='UIL' and all(len(kinds.get(x,[]))==1 for x in (42,43)) and len(kernel.get(32,[]))==1:
            begin,end=kinds[42][0]['tick'],kinds[43][0]['tick'];received=kernel[32][0]['tick']
            if end>=begin:calibration.append(dict(pid=pid,id=ident,lower=received-end,upper=received-begin))
    lower=max((x['lower'] for x in calibration),default=0)
    upper=min((x['upper'] for x in calibration),default=-1)
    calibrated=kernel_base==(n,d) and bool(calibration) and lower<=upper
    candidates=[]
    for e in events:
        if e['scope']=='UIK' and e['kind'] in (1,2):
            following=next((x for x in events if calibrated and x['scope']=='UIL' and x['kind']==10 and x['tick']>=e['tick']-upper),None)
            candidates.append(dict(input_line=e['line'],next_render_line=following['line'] if following else None,
                temporal_gap_ms_bounds=[(following['tick']-e['tick']+lower)*n/d/1e6,(following['tick']-e['tick']+upper)*n/d/1e6] if following else None,causal_link_proven=False))
    return dict(sha256=hashlib.sha256(raw).hexdigest(),timebase=dict(numer=n,denom=d),events=events,intervals=intervals,
        losses=losses,incomplete_or_dropped=bool(losses),temporal_candidates=candidates,
        clock_calibration=dict(available=calibrated,kernel_base=kernel_base,samples=calibration,offset_tick_bounds=[lower,upper] if calibrated else None),
        panic_observed=bool(re.search(r'panic\(cpu',text)),ui_completion_proven=False,
        note='Raw timestamps order events, not UART output. Missing endpoints stay unmeasured. No input/frame causal ID exists; visual response and task CPU/memory need separate evidence.')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('log',type=Path);a=p.parse_args()
    result=analyze(a.log.read_bytes());result['source']=str(a.log.resolve());print(json.dumps(result,indent=2))
if __name__=='__main__':main()
