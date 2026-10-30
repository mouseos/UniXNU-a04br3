#!/usr/bin/env python3
import importlib.util,unittest
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
if __name__=='__main__':unittest.main()
