#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2]
script=root/'scripts/summarize-msdc-io.py'
modes=('dma_read','pio_read','write'); bins=('le4k','4k_16k','gt16k')
anchor=b'[pmic6392_init] Preloader Start'
prefix=b'garbled uart prefix\x00\xff\n'+anchor+b'...\n[BLDR] Build Time: 20200720\nBOOT_REASON: 4\n[18] BOOT_REASON: 4\n'
def sample(t,n,skip=False):
 lines=[f'a04br3 msdc io sample begin_ns={t} end_ns={t+2} non_atomic=1']
 for mode in modes:
  for bin in bins:
   if skip and (mode,bin)==('write','gt16k'):continue
   lines.append(f'a04br3 msdc transfer mode={mode} size={bin} calls={n} ok_bytes={n*512} errors=0 elapsed_ns={n*10}')
 return ('\n'.join(lines)+'\n').encode()
def digest(b):return hashlib.sha256(b).hexdigest()
with tempfile.TemporaryDirectory(prefix='iostat-summary-') as d:
 path=Path(d)/'uart'
 def run(data, offset=None, source_hash=None, boot_id=None):
  path.write_bytes(data);offset=data.find(anchor) if offset is None else offset
  source_hash=digest(data) if source_hash is None else source_hash
  boot_id=digest(data[offset:]) if boot_id is None else boot_id
  return subprocess.run(['python3',str(script),str(path),'--source-sha256',source_hash,
                         '--boot-start-offset',str(offset),'--boot-id',boot_id],capture_output=True,text=True)
 good=prefix+sample(100,1)+sample(1_000_000_100,3)
 p=run(good);assert p.returncode==0,p.stderr
 rows=[json.loads(x) for x in p.stdout.splitlines()];assert len(rows)==9
 assert all(r['calls']==2 and r['ok_bytes']==1024 and r['observed_bytes_per_second']==1024 for r in rows)
 assert all(r['source_sha256']==digest(good) and r['boot_segment_sha256']==digest(good[good.find(anchor):]) and r['boot_start_offset']==good.find(anchor) for r in rows)
 p=run(prefix+sample(100,1)+sample(200,2,True)+sample(300,3));assert p.returncode==0 and not p.stdout and 'interval discarded' in p.stderr
 p=run(prefix+sample(100,1)+sample(99,2));assert p.returncode==2 and not p.stdout and 'non-monotonic' in p.stderr
 p=run(prefix+sample(100,3)+sample(200,2));assert p.returncode==2 and not p.stdout and 'counter decreased' in p.stderr
 # The third sample makes prior valid JSON unsafe; whole-file validation must emit nothing.
 p=run(prefix+sample(100,1)+sample(200,2)+sample(300,1));assert p.returncode==2 and not p.stdout
 # Both time and counters increase across reboot; boundary evidence must still reject it.
 mixed=good+anchor+b'...\n[BLDR] Build Time: second\nBOOT_REASON: 4\n'+sample(2_000_000_100,5)
 p=run(mixed);assert p.returncode==2 and not p.stdout and 'only preloader anchor' in p.stderr
 p=run(good+b'BOOT_REASON: 4\n'+sample(2_000_000_100,5));assert p.returncode==2 and not p.stdout and 'boot boundary after sampling' in p.stderr
 p=run(good+b'[BLDR] Build Time: second\n');assert p.returncode==2 and not p.stdout and 'repeated BLDR' in p.stderr
 p=run(good+b'a04br3 msdc io sample begin_ns=bad\n');assert p.returncode==2 and not p.stdout and 'malformed sample' in p.stderr
 p=run(good+b'a04br3 msdc transfer mode=bad\n');assert p.returncode==2 and not p.stdout and 'malformed transfer' in p.stderr
 p=run(good,source_hash='0'*64);assert p.returncode==2 and not p.stdout and 'source SHA-256' in p.stderr
 p=run(good,offset=good.find(anchor)+1);assert p.returncode==2 and not p.stdout and 'boot start offset' in p.stderr
 p=run(good,boot_id='0'*64);assert p.returncode==2 and not p.stdout and 'segment SHA-256' in p.stderr
 p=run(sample(100,1)+sample(200,2),offset=0);assert p.returncode==2 and not p.stdout and 'preloader anchor' in p.stderr
print('PASS boot-anchored source identity, mixed boundaries and all-or-nothing output')
