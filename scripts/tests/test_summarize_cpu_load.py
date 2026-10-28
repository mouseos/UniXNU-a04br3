#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2];script=root/'scripts/summarize-cpu-load.py'
boot=b'[pmic6392_init] Preloader Start\r\n[BLDR] Build Time: 20200720\r\nBOOT_REASON: 4\r\n[18] BOOT_REASON: 4\r\n'
def frame(begin,ticks,states=None):
 states=states or [(4,4)]*4
 lines=[]
 for i in range(4):
  u,s,idle,nice=ticks[i];before,after=states[i]
  lines.append(f'a04br3 cpu-load cpu={i} at_ns={begin+i*100000} user={u} system={s} idle={idle} nice={nice} state_before={before} state_after={after} ticks32=1 non_atomic=1')
 lines.append(f'a04br3 cpu-load sample_begin_ns={begin} sample_end_ns={begin+500000} cpus=4 extra=0 non_atomic=1')
 return ('\r\n'.join(lines)+'\r\n').encode()
old=[(0,100,200,0)]*4
new=[(0,200,1100,0)]*4
with tempfile.TemporaryDirectory(prefix='cpu-load-') as d:
 d=Path(d);capture=d/'capture';source=d/'uart';meta=d/'flash.json';offset=43
 def run(data,raw=None,overrides=None):
  capture.write_bytes(data);source.write_bytes(b'x'*offset+(data if raw is None else raw)+b'continued')
  m={'uart_offset_before_reset':1,'uart_offset_before_flash':2,'uart_offset_before_reboot':offset,'manifest':{'boot_sha256':'a'*64}}
  if overrides:m.update(overrides)
  meta.write_text(json.dumps(m))
  return subprocess.run(['python3',str(script),'--capture',str(capture),'--flash-json',str(meta),'--uart-source',str(source)],capture_output=True,text=True)
 def rejected(data,why,**kwargs):
  p=run(data,**kwargs);assert p.returncode==2 and not p.stdout and why in p.stderr,p.stderr
 good=boot+frame(1_000_000_000,old)+frame(11_000_000_000,new)
 p=run(good);assert p.returncode==0,p.stderr
 rows=[json.loads(x) for x in p.stdout.splitlines()];assert len(rows)==1
 r=rows[0];assert len(r['cores'])==4 and all(c['non_idle_percent']==10 and c['duration_ns']==10_000_000_000 for c in r['cores'])
 assert r['combined_non_idle_percent']==10 and r['capture_sha256']==hashlib.sha256(good).hexdigest()
 assert r['parser_sha256']==hashlib.sha256(script.read_bytes()).hexdigest()
 assert r['uart_reboot_offset']==offset and r['source_prefix_end_offset']==offset+len(good)
 assert r['source_prefix_sha256']==hashlib.sha256(b'x'*offset+good).hexdigest()
 # A true 32-bit wrap yields a small, bounded delta; a reset producing a huge delta is refused.
 before=[(0xfffffffe,100,200,0)]*4;after=[(3,110,300,0)]*4
 p=run(boot+frame(1_000_000_000,before)+frame(11_000_000_000,after));assert p.returncode==0,p.stderr
 r=json.loads(p.stdout);assert all(c['wrapped_components']==1 and c['non_idle_ticks']==15 and c['idle_ticks']==100 for c in r['cores'])
 rejected(boot+frame(1_000_000_000,[(1000,100,200,0)]*4)+frame(11_000_000_000,[(2,110,300,0)]*4),'implausible')
 rejected(good, 'capture differs',raw=good[:-1]+b'X')
 rejected(good, 'invalid flash UART offsets',overrides={'uart_offset_before_reboot':0})
 rejected(boot+frame(1_000_000_000,old)+frame(11_000_000_000,new,[(4,4),(4,4),(0,0),(4,4)]),'not online')
 # IDLE to RUNNING is online at both reads, not proof of continuity between them.
 p=run(boot+frame(1_000_000_000,old)+frame(11_000_000_000,new,[(4,6)]*4));assert p.returncode==0,p.stderr
 rejected(good.replace(b'cpu=2 at_ns=',b'cpu=3 at_ns=',1),'out-of-order CPU')
 rejected(good.replace(b'cpu=1 at_ns=1000100000',b'cpu=1 at_ns=1000000000',1),'timestamps outside')
 rejected(good.replace(b'cpu=1 at_ns=1000100000',b'cpu=1 at_ns=999999999',1),'timestamps outside')
 rejected(good.replace(b'cpus=4 extra=0',b'cpus=3 extra=0',1),'incomplete CPU')
 rejected(good.replace(b'cpus=4 extra=0',b'cpus=4 extra=1',1),'incomplete CPU')
 rejected(good.replace(b'ticks32=1 non_atomic=1',b'ticks32=1 non_atomic=1INVALID',1),'malformed CPU')
 rejected(good.replace(b'idle=200 nice=0',b'idle=4294967296 nice=0',1),'32-bit range')
 rejected(boot+frame(1_000_000_000,old)+frame(15_000_000_000,new),'outside 9')
 rejected(boot+frame(1_000_000_000,old)+frame(11_000_000_000,old),'zero or implausible')
 rejected(good+frame(21_000_000_000,old),'implausible') # late failure emits no earlier JSON
 rejected(good+boot+frame(21_000_000_000,new),'mixed boot')
 rejected(good+b'BOOT_REASON: 4\r\n','boot reason')
 rejected(good.replace(b'nice=0 state_before',b'nice=0\x0bINVALID state_before',1),'control character')
 rejected(good.rstrip(b'\r\n'),'unterminated CPU')
 joined=good.replace(b'non_atomic=1\r\na04br3 cpu-load cpu=1',b'non_atomic=1a04br3 cpu-load cpu=1',1)
 rejected(joined,'joined CPU')
 # No changes to unrelated source suffix are required, but the selected span must match exactly.
print('PASS CPU 10s source/offset identity, state, wrap, incomplete/mixed frames and all-or-nothing output')
