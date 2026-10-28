#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2]
script=root/'scripts/summarize-msdc-io.py'
modes=('dma_read','pio_read','write'); bins=('le4k','4k_16k','gt16k')
anchor=b'[pmic6392_init] Preloader Start'
prefix=b'garbled uart prefix\x00\xff\n'+anchor+b'...\n[BLDR] Build Time: 20200720\nBOOT_REASON: 4\n[18] BOOT_REASON: 4\n'
def sample(seq,t,n,skip=None,end=True):
 lines=[f'a04br3 msdc io sample seq={seq} begin_ns={t} end_ns={t+2} non_atomic=1']
 for mode in modes:
  for bin in bins:
   if skip==('transfer',mode,bin):continue
   lines.append(f'a04br3 msdc transfer seq={seq} mode={mode} size={bin} calls={n} ok_bytes={n*512} errors=0 elapsed_ns={n*10}')
 for mode in ('read','write'):
  for bin in bins:
   if skip==('offered',mode,bin):continue
   lines.append(f'a04br3 msdc offered seq={seq} mode={mode} size={bin} calls={n} bytes={n*1024}')
 if skip!=('queue','',''):
  lines.append(f'a04br3 msdc queue seq={seq} started={n} wait_ns={n*20} max_wait_ns={n*5}')
 if end:lines.append(f'a04br3 msdc io end seq={seq} rows=16')
 return ('\n'.join(lines)+'\n').encode()
def digest(b):return hashlib.sha256(b).hexdigest()
with tempfile.TemporaryDirectory(prefix='iostat-summary-') as d:
 path=Path(d)/'uart'
 def run(data,offset=None,source_hash=None,boot_id=None,require_cache=False):
  path.write_bytes(data);offset=data.find(anchor) if offset is None else offset
  source_hash=digest(data) if source_hash is None else source_hash
  boot_id=digest(data[offset:]) if boot_id is None else boot_id
  return subprocess.run(['python3',str(script),str(path),'--source-sha256',source_hash,
                         '--boot-start-offset',str(offset),'--boot-id',boot_id]+(['--require-cache'] if require_cache else []),capture_output=True,text=True)
 def rejected(data,why):
  p=run(data);assert p.returncode==2 and not p.stdout and why in p.stderr,p.stderr
 good=prefix+sample(1,100,1)+sample(2,1_000_000_100,3)
 p=run(good);assert p.returncode==0,p.stderr
 rows=[json.loads(x) for x in p.stdout.splitlines()];assert len(rows)==16
 tx=[r for r in rows if r['kind']=='transfer'];offer=[r for r in rows if r['kind']=='offered'];q=rows[-1]
 assert len(tx)==9 and len(offer)==6 and all(r['calls']==2 and r['ok_bytes']==1024 and r['observed_bytes_per_second']==1024 and r['activity_observed'] for r in tx)
 assert all(r['calls']==2 and r['bytes']==2048 and r['activity_observed'] for r in offer)
 assert q['kind']=='queue' and q['started']==2 and q['wait_ns']==40 and q['max_wait_ns_cumulative']==15 and q['activity_observed']
 assert all(r['source_sha256']==digest(good) and r['boot_segment_sha256']==digest(good[good.find(anchor):]) and r['boot_start_offset']==good.find(anchor) for r in rows)
 # New optional cache snapshots share the frame timestamps and sequence.
 def with_cache(data, seq, hits, misses, reused):
  return data.replace(f'a04br3 msdc io end seq={seq}'.encode(),
   f'a04br3 msdc cache sample seq={seq} hits={hits} misses={misses} reused_bytes={reused}\na04br3 msdc io end seq={seq}'.encode())
 ca=with_cache(sample(1,100,1),1,1,2,4096);cb=with_cache(sample(2,200,2),2,4,7,12288)
 p=run(good,require_cache=True);assert p.returncode==2 and 'missing cache sample' in p.stderr
 p=run(prefix+ca+cb,require_cache=True);assert p.returncode==0,p.stderr
 cache=[json.loads(x) for x in p.stdout.splitlines() if json.loads(x)['kind']=='cache'];assert len(cache)==1 and (cache[0]['hits'],cache[0]['misses'],cache[0]['reused_bytes'])==(3,5,8192)
 rejected(prefix+ca+sample(2,200,2),'missing cache sample')
 rejected(prefix+ca+with_cache(sample(2,200,2),2,0,2,4096),'cache counter decreased')
 rejected(prefix+ca+cb.replace(b'cache sample seq=2',b'cache sample seq=1'),'mixed')
 rejected(prefix+ca+cb.replace(b'reused_bytes=12288',b'reused_bytes=bad'),'malformed')
 # A complete idle interval is valid and reports inactive, zero rates, and a cumulative queue maximum.
 p=run(prefix+sample(1,100,1)+sample(2,200,1));assert p.returncode==0,p.stderr
 idle=[json.loads(x) for x in p.stdout.splitlines()];assert len(idle)==16 and not any(r['activity_observed'] for r in idle)
 assert all(r['observed_bytes_per_second']==0 for r in idle if r['kind']=='transfer') and idle[-1]['max_wait_ns_cumulative']==5
 # Prefixes such as UART timestamps are allowed; suffixes are not.
 prefixed=prefix+b'\n'.join(b'[123] '+line for line in (sample(1,100,1)+sample(2,200,2)).splitlines())+b'\n'
 p=run(prefixed);assert p.returncode==0,p.stderr
 for token in (b'elapsed_ns=10', b'rows=16', b'bytes=1024', b'max_wait_ns=5', b'non_atomic=1'):
  corrupted=good.replace(token,token+b'INVALID',1)
  rejected(corrupted,'malformed')
 rejected(good.replace(b'elapsed_ns=10',b'elapsed_ns=19INVALID',1),'malformed')
 rejected(good.replace(b'rows=16',b'rows=9INVALID',1),'malformed')
 rejected(prefix+sample(1,100,1)+sample(2,200,2,('transfer','write','gt16k')),'out-of-order')
 rejected(prefix+sample(1,100,1)+sample(2,200,2,('offered','read','le4k')),'out-of-order')
 rejected(prefix+sample(1,100,1)+sample(2,200,2,('queue','','')),'incomplete sample')
 rejected(prefix+sample(1,100,1)+sample(2,99,2),'non-monotonic')
 rejected(prefix+sample(1,100,1)+sample(2,101,2),'overlapping')
 rejected(prefix+sample(1,100,1)+sample(2,100,2),'non-monotonic')
 rejected(prefix+sample(1,100,3)+sample(2,200,2),'counter decreased')
 rejected(prefix+sample(1,100,1)+sample(2,200,2)+sample(3,300,1),'counter decreased')
 rejected(prefix+sample(1,100,1,end=False)+sample(2,200,2),'missing sample end')
 rejected(prefix+sample(1,100,1)+sample(3,200,2),'sample sequence')
 lost_header=b'\n'.join(sample(2,200,2).splitlines()[1:9])+b'\n'
 rejected(prefix+sample(1,100,1,('transfer','write','gt16k'),False)+lost_header,'out-of-order')
 rejected(prefix+sample(1,100,1)+sample(2,200,2,end=False),'missing sample end')
 wrong_offer=sample(2,200,2).replace(b'offered seq=2 mode=read size=le4k',b'offered seq=3 mode=read size=le4k')
 rejected(prefix+sample(1,100,1)+wrong_offer,'mixed sample row')
 wrong_queue=sample(2,200,2).replace(b'queue seq=2',b'queue seq=1')
 rejected(prefix+sample(1,100,1)+wrong_queue,'mixed sample row')
 mixed=good+anchor+b'...\n[BLDR] Build Time: second\nBOOT_REASON: 4\n'+sample(3,2_000_000_100,5)
 rejected(mixed,'only preloader anchor')
 rejected(good+b'BOOT_REASON: 4\n'+sample(3,2_000_000_100,5),'boot boundary after sampling')
 rejected(good+b'[BLDR] Build Time: second\n','repeated BLDR')
 rejected(good+b'a04br3 msdc io sample seq=bad\n','malformed sample')
 rejected(good+b'a04br3 msdc transfer seq=bad\n','malformed or unavailable transfer')
 p=run(good,source_hash='0'*64);assert p.returncode==2 and not p.stdout and 'source SHA-256' in p.stderr
 p=run(good,offset=good.find(anchor)+1);assert p.returncode==2 and not p.stdout and 'boot start offset' in p.stderr
 p=run(good,boot_id='0'*64);assert p.returncode==2 and not p.stdout and 'segment SHA-256' in p.stderr
 p=run(sample(1,100,1)+sample(2,200,2),offset=0);assert p.returncode==2 and not p.stdout and 'preloader anchor' in p.stderr
print('PASS strict frame suffix, sequence, ordered transfer/offer/queue, idle, identity and mixed input')
