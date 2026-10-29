#!/usr/bin/env python3
"""Summarize VM counters in a saved, SHA-identified single boot; no device access."""
import argparse,hashlib,json,re,sys
from pathlib import Path
ANCHOR=b'[pmic6392_init] Preloader Start'
REASON_MARK=re.compile(rb'(?im)^[ \t]*(?:\[[ \t]*[0-9]+[ \t]*\][ \t]*)?BOOT_REASON[ \t]*[:=]')
REASON=re.compile(rb'(?im)^[ \t]*(?:\[[ \t]*[0-9]+[ \t]*\][ \t]*)?BOOT_REASON[ \t]*[:=][ \t]*(0[xX][0-9a-fA-F]{1,16}|[0-9]{1,20})[ \t\r]*\n')
VM=re.compile(rb'a04br3 perf vm pagesize=(\d+) free=(\d+) active=(\d+) inactive=(\d+) wired=(\d+) compressor=(\d+) speculative=(\d+) throttled=(\d+) pageable_internal=(\d+) pageable_external=(\d+) mode=(-?\d+) active_compressor=(\d+) active_swap=(\d+)')
SAMPLE=re.compile(rb'a04br3 perf sample ns=(\d+) tasks=(\d+)')
FIELDS=('pagesize','free','active','inactive','wired','compressor','speculative','throttled','pageable_internal','pageable_external','mode','active_compressor','active_swap')
def parse(data,expected):
 if not re.fullmatch(r'[0-9a-f]{64}',expected) or hashlib.sha256(data).hexdigest()!=expected:raise ValueError('source SHA-256 mismatch')
 if not data.startswith(ANCHOR) or data.count(ANCHOR)!=1 or data.count(b'[BLDR] Build Time:')!=1:raise ValueError('single preloader and BLDR required')
 lines=data.split(b'\n');pending=None;out=[];last_time=-1;first_vm=data.find(b'a04br3 perf vm')
 reasons=list(REASON.finditer(data));marks=list(REASON_MARK.finditer(data));bldr=data.find(b'[BLDR] Build Time:')
 if first_vm<0 or bldr<0 or bldr>=first_vm or not reasons or len(reasons)!=len(marks) or any(m.start()<=bldr or m.start()>=first_vm for m in reasons) or len({int(m.group(1),0) if m.group(1).lower().startswith(b'0x') else int(m.group(1)) for m in reasons})!=1:raise ValueError('invalid preloader reason or BLDR ordering')
 offset=0
 for i,line in enumerate(lines):
  terminated=i<len(lines)-1;clean=line.rstrip(b'\r')
  if pending is not None and not clean.startswith(b'a04br3 perf sample ns='):raise ValueError('VM and sample timestamp are not adjacent')
  if b'a04br3 perf vm' in line:
   if not terminated or pending is not None:raise ValueError('unterminated or unpaired VM row')
   match=VM.fullmatch(clean)
   if not match:raise ValueError('malformed or interleaved VM row')
   values=dict(zip(FIELDS,map(int,match.groups())))
   if values['pagesize']<=0 or values['active_compressor'] not in (0,1) or values['active_swap'] not in (0,1):raise ValueError('invalid VM fields')
   pending={'vm_offset':offset,'pages':values}
  elif b'a04br3 perf sample ns=' in line:
   match=SAMPLE.fullmatch(clean)
   if not terminated or not match or pending is None:raise ValueError('unpaired or malformed sample timestamp')
   t,n=map(int,match.groups())
   if t<=last_time:raise ValueError('non-monotonic sample timestamps')
   pending.update(sample_offset=offset,subsequent_sample_ns=t,tasks=n);out.append(pending);pending=None;last_time=t
  offset+=len(line)+1
 if pending is not None:raise ValueError('missing timestamp after VM row')
 if not out:raise ValueError('no VM rows')
 return {'source_sha256':expected,'samples':out,'limits':['Counters are non-atomic page observations; fields may overlap and must not be summed into used RAM.','The subsequent perf sample timestamp is a proxy, not the VM read time.','UART loss, cache state and disk-full-interval completeness are unproven.','No total DRAM traffic or copy benchmark is measured by these VM rows.']}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--sha256',required=True);a=p.parse_args()
 with a.source.open('rb') as f:data=f.read(128*1024*1024+1)
 if len(data)>128*1024*1024:raise ValueError('source exceeds limit')
 print(json.dumps(parse(data,a.sha256),indent=2))
if __name__=='__main__':
 try:main()
 except (ValueError,OSError) as e:print('vm summary:',e,file=sys.stderr);sys.exit(2)
