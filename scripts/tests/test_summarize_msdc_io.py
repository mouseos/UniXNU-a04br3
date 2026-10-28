#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2]
script=root/'scripts/summarize-msdc-io.py'
modes=('dma_read','pio_read','write'); bins=('le4k','4k_16k','gt16k')
def sample(t,n,skip=False):
 lines=[f'a04br3 msdc io sample begin_ns={t} end_ns={t+2} non_atomic=1']
 for mode in modes:
  for bin in bins:
   if skip and (mode,bin)==('write','gt16k'):continue
   lines.append(f'a04br3 msdc transfer mode={mode} size={bin} calls={n} ok_bytes={n*512} errors=0 elapsed_ns={n*10}')
 return '\n'.join(lines)+'\n'
with tempfile.TemporaryDirectory(prefix='iostat-summary-') as d:
 path=Path(d)/'uart';path.write_text(sample(100,1)+sample(1_000_000_100,3))
 p=subprocess.run(['python3',str(script),str(path)],capture_output=True,text=True,check=True)
 rows=[json.loads(x) for x in p.stdout.splitlines()];assert len(rows)==9
 assert all(r['calls']==2 and r['ok_bytes']==1024 and r['observed_bytes_per_second']==1024 for r in rows)
 path.write_text(sample(100,1)+sample(200,2,True)+sample(300,3));p=subprocess.run(['python3',str(script),str(path)],capture_output=True,text=True,check=True)
 assert not p.stdout and 'interval discarded' in p.stderr
 path.write_text(sample(100,1)+sample(99,2));p=subprocess.run(['python3',str(script),str(path)],capture_output=True,text=True)
 assert p.returncode==2 and 'one complete boot' in p.stderr
 path.write_text(sample(100,3)+sample(200,2));p=subprocess.run(['python3',str(script),str(path)],capture_output=True,text=True)
 assert p.returncode==2 and 'counter decreased' in p.stderr
print('PASS single-boot MSDC summary: deltas, throughput, incomplete intervals, reset and time ordering')
