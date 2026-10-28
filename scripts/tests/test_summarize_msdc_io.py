#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2]
script=root/'scripts/summarize-msdc-io.py'
modes=('dma_read','pio_read','write'); bins=('le4k','4k_16k','gt16k')
anchor=b'[pmic6392_init] Preloader Start'
prefix=b'garbled uart prefix\x00\xff\n'+anchor+b'...\n[BLDR] Build Time: 20200720\nBOOT_REASON: 4\n[18] BOOT_REASON: 4\n'
def sample(seq,t,n,skip=False,end=True):
 lines=[f'a04br3 msdc io sample seq={seq} begin_ns={t} end_ns={t+2} non_atomic=1']
 for mode in modes:
  for bin in bins:
   if skip and (mode,bin)==('write','gt16k'):continue
   lines.append(f'a04br3 msdc transfer seq={seq} mode={mode} size={bin} calls={n} ok_bytes={n*512} errors=0 elapsed_ns={n*10}')
 if end:lines.append(f'a04br3 msdc io end seq={seq} rows=9')
 return ('\n'.join(lines)+'\n').encode()
def digest(b):return hashlib.sha256(b).hexdigest()
with tempfile.TemporaryDirectory(prefix='iostat-summary-') as d:
 path=Path(d)/'uart'
 def run(data,offset=None,source_hash=None,boot_id=None):
  path.write_bytes(data);offset=data.find(anchor) if offset is None else offset
  source_hash=digest(data) if source_hash is None else source_hash
  boot_id=digest(data[offset:]) if boot_id is None else boot_id
  return subprocess.run(['python3',str(script),str(path),'--source-sha256',source_hash,
                         '--boot-start-offset',str(offset),'--boot-id',boot_id],capture_output=True,text=True)
 def rejected(data,why):
  p=run(data);assert p.returncode==2 and not p.stdout and why in p.stderr,p.stderr
 good=prefix+sample(1,100,1)+sample(2,1_000_000_100,3)
 p=run(good);assert p.returncode==0,p.stderr
 rows=[json.loads(x) for x in p.stdout.splitlines()];assert len(rows)==9
 assert all(r['calls']==2 and r['ok_bytes']==1024 and r['observed_bytes_per_second']==1024 and r['from_seq']==1 and r['to_seq']==2 for r in rows)
 assert all(r['source_sha256']==digest(good) and r['boot_segment_sha256']==digest(good[good.find(anchor):]) and r['boot_start_offset']==good.find(anchor) for r in rows)
 rejected(prefix+sample(1,100,1)+sample(2,200,2,True)+sample(3,300,3),'incomplete sample')
 rejected(prefix+sample(1,100,1)+sample(2,99,2),'non-monotonic')
 rejected(prefix+sample(1,100,1)+sample(2,101,2),'overlapping')
 rejected(prefix+sample(1,100,1)+sample(2,100,2),'non-monotonic')
 rejected(prefix+sample(1,100,1)+sample(2,200,1),'no transfer call delta')
 rejected(prefix+sample(1,100,3)+sample(2,200,2),'counter decreased')
 rejected(prefix+sample(1,100,1)+sample(2,200,2)+sample(3,300,1),'counter decreased')
 rejected(prefix+sample(1,100,1,end=False)+sample(2,200,2),'missing sample end')
 rejected(prefix+sample(1,100,1)+sample(3,200,2),'sample sequence')
 # Reproduce the lost header + first eight rows mixing into an earlier incomplete sample.
 lost_header=b'\n'.join(sample(2,200,2).splitlines()[1:9])+b'\n'
 rejected(prefix+sample(1,100,1,skip=True,end=False)+lost_header,'mixed transfer row')
 rejected(prefix+sample(1,100,1)+sample(2,200,2,end=False),'missing sample end')
 mixed=good+anchor+b'...\n[BLDR] Build Time: second\nBOOT_REASON: 4\n'+sample(3,2_000_000_100,5)
 rejected(mixed,'only preloader anchor')
 rejected(good+b'BOOT_REASON: 4\n'+sample(3,2_000_000_100,5),'boot boundary after sampling')
 rejected(good+b'[BLDR] Build Time: second\n','repeated BLDR')
 rejected(good+b'a04br3 msdc io sample seq=bad\n','malformed sample')
 rejected(good+b'a04br3 msdc transfer seq=bad\n','malformed transfer')
 p=run(good,source_hash='0'*64);assert p.returncode==2 and not p.stdout and 'source SHA-256' in p.stderr
 p=run(good,offset=good.find(anchor)+1);assert p.returncode==2 and not p.stdout and 'boot start offset' in p.stderr
 p=run(good,boot_id='0'*64);assert p.returncode==2 and not p.stdout and 'segment SHA-256' in p.stderr
 p=run(sample(1,100,1)+sample(2,200,2),offset=0);assert p.returncode==2 and not p.stdout and 'preloader anchor' in p.stderr
print('PASS identity, sequence, ordered/end rows, missing-header mixing, zero delta and time overlap')
