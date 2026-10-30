import importlib.util
import unittest
from pathlib import Path

path = Path(__file__).resolve().parents[1] / 'a04br3_trial_evidence.py'
spec = importlib.util.spec_from_file_location('trial_evidence', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BASE = '''a04br3: memory bench spawn 0 (pid 42)
a04br3_mem_copy phase=waiting delay_seconds=180 profile=low_impact_64k
a04br3_mem_copy phase=begin kind=copy size_bytes=65536
a04br3_mem_copy synthetic=1 phase=prepare size_bytes=65536 monotonic_ns=100
a04br3_mem_copy phase=vm_activity query_begin_ns=101 query_end_ns=102
a04br3_mem_copy phase=vm_usage query_begin_ns=101 query_end_ns=102
a04br3_mem_copy phase=memory_check reported_free_bytes=50000000 speculative_bytes=0 free_bytes=50000000 threshold_bytes=33685504
'''
COPY = 'a04br3_mem_copy synthetic=1 threads=1 size_bytes=65536 start_monotonic_ns=200 end_monotonic_ns=300 elapsed_ns=100 logical_bytes=65536 verified=1\na04br3_mem_copy phase=end kind=copy status=ok\n'
SKIP = 'a04br3_mem_copy phase=skip reason=insufficient_free_pages\na04br3_mem_copy phase=end kind=copy status=skip\n'

class TestEvidence(unittest.TestCase):
    def test_off(self):
        self.assertTrue(m.analyze_benchmark('unrelated', 'off')['valid'])
        self.assertFalse(m.analyze_benchmark(BASE, 'off')['valid'])
    def test_completed(self):
        r = m.analyze_benchmark(BASE + COPY, 'on')
        self.assertTrue(r['valid']); self.assertEqual(r['logical_bytes'], 65536)
        self.assertIn('not DRAM', r['limitation'])
    def test_skip(self):
        self.assertEqual(m.analyze_benchmark(BASE.replace('50000000', '1000') + SKIP, 'on')['outcome'], 'skip')
    def test_failures(self):
        for raw in (BASE, BASE + COPY.replace('elapsed_ns=100', 'elapsed_ns=101'), BASE + COPY.replace('verified=1','verified=0'), BASE + SKIP, BASE + SKIP.replace('insufficient_free_pages','vm_statistics_unavailable'), BASE + COPY + COPY, BASE.replace('spawn 0','spawn 5') + COPY, BASE.replace('query_end_ns=102', 'query_end_ns=99', 1) + COPY):
            self.assertFalse(m.analyze_benchmark(raw, 'on')['valid'])

if __name__ == '__main__': unittest.main()
