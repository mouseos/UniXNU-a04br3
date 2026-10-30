#!/usr/bin/env python3
import hashlib,importlib.util,json,subprocess,tempfile,unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'summarize-memory-observation.py';s=importlib.util.spec_from_file_location('memory',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
VM='a04br3 perf vm pagesize=4096 free=10 active=20 inactive=30 wired=40 compressor=5 speculative=4 throttled=0 pageable_internal=2 pageable_external=3 mode=2 active_compressor=1 active_swap=0\n'
USAGE='a04br3_mem_copy phase=vm_usage query_begin_ns=10 query_end_ns=20 page_size=4096 free_pages=10 speculative_pages=4 active_pages=20 inactive_pages=30 wired_pages=40 cache_freshness=unknown non_atomic=1\n'
COPY='a04br3_mem_copy synthetic=1 threads=1 size_bytes=65536 start_monotonic_ns=100 end_monotonic_ns=200 elapsed_ns=100 logical_bytes=65536 verified=1\n'
def raw(s):return m.ANCHOR+b'\n'+s.encode()
class Tests(unittest.TestCase):
 def test_values_not_added(self):
  x=m.analyze(raw(VM+'a04br3 perf sample ns=300 tasks=2\n'+USAGE+'a04br3_mem_copy phase=vm_usage_extended compressor_pages=2 external_pages=3 internal_pages=4 non_atomic=1\n'+COPY),'a'*64)
  self.assertEqual(x['vm_usage'][0]['free_excluding_speculative_bytes'],6*4096)
  self.assertEqual(x['kernel_vm'][0]['following_heartbeat_ns'],300)
  self.assertEqual(x['copies'][0]['logical_mib_per_s'],625000.0)
  self.assertEqual(x['vm_usage'][0]['extended']['bytes_by_field']['compressor_pages'],8192)
 def test_boot_boundary(self):
  for data in (b'',b'prefix'+raw(''),raw('')+m.ANCHOR):
   with self.assertRaises(ValueError):m.analyze(data,'a'*64)
 def test_missing_or_bad_sample(self):
  for text in (VM,USAGE.replace('speculative_pages=4','speculative_pages=11'),COPY.replace('elapsed_ns=100','elapsed_ns=101'),COPY.replace('verified=1','verified=0'),'a04br3_mem_copy phase=vm_usage_extended compressor_pages=2 external_pages=3 internal_pages=4 non_atomic=1\n'):
   with self.assertRaises(ValueError):m.analyze(raw(text),'a'*64)
 def test_skip_is_not_copy(self):
  x=m.analyze(raw('a04br3_mem_copy phase=skip reason=insufficient_free_pages\n'),'a'*64)
  self.assertEqual(len(x['skips']),1);self.assertEqual(x['copies'],[])
 def test_image_identity_required(self):
  with self.assertRaises(ValueError):m.analyze(raw(''),'not-sha')
class ProtocolTests(unittest.TestCase):
 def run_cli(self, text, expected, size=None, boot_run=False):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'boot';data=raw(text);path.write_bytes(data)
   args=['python3',str(p),str(path),'--image-sha256','a'*64,'--expected-raw-sha256',hashlib.sha256(data).hexdigest(),'--expect',expected]
   if size:args+=['--size-bytes',str(size)]
   if boot_run:args+=['--boot-run']
   return subprocess.run(args,text=True,capture_output=True)
 def test_boot_run_waiting_record(self):
  wait='a04br3_mem_copy phase=waiting delay_seconds=180 profile=low_impact_64k\n'
  body='a04br3_mem_copy phase=begin kind=copy size_bytes=65536\na04br3_mem_copy phase=skip reason=insufficient_free_pages\na04br3_mem_copy phase=end kind=copy status=skip\n'
  r=self.run_cli(wait+body,'copy',65536,True)
  self.assertEqual(r.returncode,2,r.stderr)
  self.assertEqual(json.loads(r.stdout)['assessment']['status'],'skip')
  for bad in (body,wait+wait+body,wait.replace('180','179')+body,wait.replace('64k','1m')+body):
   self.assertEqual(self.run_cli(bad,'copy',65536,True).returncode,1)
  self.assertEqual(self.run_cli(wait+body,'copy',65536).returncode,1)
  self.assertEqual(self.run_cli(wait+body,'observe',None,True).returncode,1)
 def test_failure_and_missing_cli(self):
  for text in ('', 'a04br3_mem_copy phase=error reason=invalid_elapsed\n', 'a04br3_mem_copy phase=begin kind=copy size_bytes=65536\n', 'a04br3_mem_copy synthetic=1 threads=1 size_bytes=65536'):
   r=self.run_cli(text,'copy',65536);self.assertNotEqual(r.returncode,0,(text,r.stdout))
 def test_success_and_skips(self):
  extended='a04br3_mem_copy phase=vm_usage_extended status=unavailable\n'
  activity='a04br3_mem_copy phase=vm_activity monotonic_ns=20 query_begin_ns=10 query_end_ns=20 cache_freshness=unknown page_size=4096 pageins=0 pageouts=0 reactivations=0 faults=0 purgeable_pages=0 purges=0 non_atomic=1\na04br3_mem_copy phase=vm_activity_extended status=unavailable\n'
  usage=USAGE.replace('free_pages=10','free_pages=10000')
  check='a04br3_mem_copy phase=memory_check reported_free_bytes=40960000 speculative_bytes=16384 free_bytes=40943616 threshold_bytes=33685504 cache_freshness=unknown\n'
  prep='a04br3_mem_copy synthetic=1 phase=prepare size_bytes=65536 monotonic_ns=1\n'
  for kind,size,body in [('observe',0,activity+usage+extended),('copy',65536,prep+activity+usage+extended+check+COPY)]:
   data=f'a04br3_mem_copy phase=begin kind={kind} size_bytes={size}\n'+body+f'a04br3_mem_copy phase=end kind={kind} status=ok\n'
   r=self.run_cli(data,kind,size if kind=='copy' else None);self.assertEqual(r.returncode,0,r.stderr)
   self.assertEqual(json.loads(r.stdout)['assessment']['status'],'success')
   if kind=='copy':
    waiting='a04br3_mem_copy phase=waiting delay_seconds=180 profile=low_impact_64k\n'
    r=self.run_cli(waiting+data,kind,size,True);self.assertEqual(r.returncode,0,r.stderr)
    self.assertEqual(json.loads(r.stdout)['assessment']['status'],'success')
  data='a04br3_mem_copy phase=begin kind=copy size_bytes=65536\na04br3_mem_copy phase=skip reason=insufficient_free_pages\na04br3_mem_copy phase=end kind=copy status=skip\n'
  r=self.run_cli(data,'copy',65536);self.assertEqual(r.returncode,2);self.assertEqual(json.loads(r.stdout)['assessment']['status'],'skip')
 def test_incomplete_and_contradiction(self):
  prefix='a04br3_mem_copy phase=begin kind=observe size_bytes=0\n'
  suffix='a04br3_mem_copy phase=end kind=observe status=ok\n'
  for text in (prefix+suffix,prefix+USAGE+suffix,prefix+USAGE+'a04br3_mem_copy phase=error reason=oops\n'+suffix,prefix+USAGE+'a04br3_mem_copy phase=vm_usage_extended status=unavailable junk\n'+suffix):
   self.assertNotEqual(self.run_cli(text,'observe').returncode,0)
 def test_kernel_only(self):
  self.assertNotEqual(self.run_cli('','kernel-vm').returncode,0)
  self.assertEqual(self.run_cli(VM+'a04br3 perf sample ns=300 tasks=2\n','kernel-vm').returncode,0)
  self.assertNotEqual(self.run_cli(VM+'a04br3 perf sample ns=300 tasks=2\n','kernel-vm',boot_run=True).returncode,0)
 def test_capture_evidence(self):
  sha=lambda x:hashlib.sha256(x).hexdigest()
  data=raw(VM+'a04br3 perf sample ns=300 tasks=2\n');prefix=b'old\n';snap=prefix+data+m.ANCHOR+b'\n';log=b"Sending 'boot' (129680 KB) OKAY [  3.828s]\nWriting 'boot' OKAY [  6.861s]\nFinished. Total time: 10.818s\n";image='a'*64
  evidence='flash.log sha256='+sha(log)
  flash={'uart_offset':10,'flash_completed_uart_offset':12,'boot_sha256':image,'flash_success':True,'flash_success_evidence':evidence}
  fb=json.dumps(flash).encode();cap={'flash_record_sha256':sha(fb),'snapshot_sha256':sha(snap),'flash_boot_sha256':image,'flash_success_claimed':True,'flash_success_evidence':evidence,'checkpoint_verified':True,'first_anchor_after_flash':True,'offset_crosses_preloader_anchor':False,'boots_truncated':False,'flash_offset':10,'flash_completed_uart_offset':12,'snapshot_end_offset':10+len(snap),'prefix_bytes_before_first_preloader':len(prefix),'boots':[{'index':1,'start_offset':10+len(prefix),'end_offset':10+len(prefix)+len(data),'closed_by_next_preloader':True,'bytes':len(data),'sha256':sha(data)},{'start_offset':10+len(prefix)+len(data)}]}
  with tempfile.TemporaryDirectory() as tmp:
   paths=[Path(tmp)/str(i) for i in range(4)]
   for path,content in zip(paths,(json.dumps(cap).encode(),fb,snap,log)):path.write_bytes(content)
   self.assertEqual(m.capture_evidence(data,image,*paths)['next_boot_start_offset'],cap['boots'][0]['end_offset'])
   send,write,finish=log.splitlines(keepends=True)
   bad_logs=[send+send+finish,write+write+finish,write+send+finish,
             send+finish,send+write,send+write+finish+send,
             send.replace(b"'boot'",b"'recovery'")+write+finish,
             send+write.replace(b"'boot'",b"'recovery'")+finish,
             send.replace(b'OKAY',b'FAILED')+write+finish,
             send+write.replace(b'OKAY',b'FAILED')+finish,
             send+write+finish+b'FAILED (remote error)\n',
             send+write+finish+b'Warning: unexpected\n',
             send+write+finish.rstrip(b'\n'),
             send.replace(b'129680',b'0')+write+finish,
             send.replace(b'OKAY',b'OKAY junk')+write+finish,
             send+write+finish.replace(b'10.818s',b'10.818s junk')]
   for bad in bad_logs:
    with self.subTest(log=bad):
     # Rebind both hashes so rejection tests transcript validation, not hashing.
     evidence='flash.log sha256='+sha(bad);flash['flash_success_evidence']=evidence
     fb=json.dumps(flash).encode();cap['flash_record_sha256']=sha(fb);cap['flash_success_evidence']=evidence
     paths[0].write_text(json.dumps(cap));paths[1].write_bytes(fb);paths[3].write_bytes(bad)
     with self.assertRaises(ValueError):m.capture_evidence(data,image,*paths)
     if bad==send+send+finish:
      boot=Path(tmp)/'boot';boot.write_bytes(data)
      result=subprocess.run(['python3',str(p),str(boot),'--image-sha256',image,'--expected-raw-sha256',sha(data),'--expect','kernel-vm','--capture-json',str(paths[0]),'--flash-json',str(paths[1]),'--snapshot',str(paths[2]),'--flash-log',str(paths[3])],capture_output=True,text=True)
      self.assertNotEqual(result.returncode,0);self.assertIn('flash log must contain',result.stderr)
   paths[3].write_bytes(log);flash['flash_success_evidence']='flash.log sha256='+sha(log)
   fb=json.dumps(flash).encode();cap['flash_record_sha256']=sha(fb);cap['flash_success_evidence']=flash['flash_success_evidence']
   paths[0].write_text(json.dumps(cap));paths[1].write_bytes(fb)
   paths[2].write_bytes(snap+b'x')
   with self.assertRaises(ValueError):m.capture_evidence(data,image,*paths)
if __name__=='__main__':unittest.main()
