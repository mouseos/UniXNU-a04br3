import hashlib,importlib.util,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'summarize-a04br3-vm.py';s=importlib.util.spec_from_file_location('vm',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def h(b):return hashlib.sha256(b).hexdigest()
head=m.ANCHOR+b'\n[BLDR] Build Time: fixture\nBOOT_REASON: 4\n[18] BOOT_REASON: 4\ncmdline: boot_reason=4 androidboot.bootreason=wdt\n'
vm=b'a04br3 perf vm pagesize=4096 free=241896 active=65 inactive=19241 wired=77906 compressor=0 speculative=4315 throttled=0 pageable_internal=1 pageable_external=23620 mode=2 active_compressor=1 active_swap=0\r\n'
def sample(t):return b'a04br3 perf sample ns='+str(t).encode()+b' tasks=2\n'
class Tests(unittest.TestCase):
 def test_real_format_and_clock_proxy(self):
  data=head+vm+sample(123)+vm+sample(456);r=m.parse(data,h(data));self.assertEqual(len(r['samples']),2);self.assertEqual(r['samples'][0]['pages']['free'],241896);self.assertEqual(r['samples'][0]['subsequent_sample_ns'],123);self.assertIn('proxy',r['limits'][1])
 def test_hash_and_boot_boundary(self):
  data=head+vm+sample(10)
  with self.assertRaises(ValueError):m.parse(data,'0'*64)
  for raw in (data+m.ANCHOR,head.replace(b'BOOT_REASON: 4\n[18] BOOT_REASON: 4\n',b'')+vm+sample(10)):
   with self.assertRaises(ValueError):m.parse(raw,h(raw))
 def test_missing_duplicate_malformed_and_clock(self):
  for raw in (head+vm,head+vm+vm+sample(1),head+sample(1),head+vm.replace(b'free=241896',b'free=oops')+sample(1),head+vm+sample(10)+vm+sample(9),head+vm+sample(10)[:-1]):
   with self.subTest(raw=raw),self.assertRaises(ValueError):m.parse(raw,h(raw))
if __name__=='__main__':unittest.main()
