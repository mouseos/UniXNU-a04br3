import hashlib, importlib.util, pathlib, unittest
p=pathlib.Path(__file__).resolve().parents[1]/'summarize-pmu-near-clock.py'
s=importlib.util.spec_from_file_location('near',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def line(phase,begin,end):
 return f'a04br3 cpu-near-clock round=0 cpu=0 phase={phase} ok=1 safe=1 begin={begin} end={end} infra=00000004 dcm=00000000 con0=00000011 con1=0009a000 pwr=00000005\n'
def run(raw):
 b=raw.encode();t={'snapshot_sha256':hashlib.sha256(b).hexdigest(),'records':[{'round':0,'cpu':0,'status':0,'begin':10,'end':100,'min_ticks':80}]};return m.summarize(b,t)
class NearTests(unittest.TestCase):
 def test_valid(self):self.assertEqual(run(line(0,11,12)+line(1,90,91))['issues'],[])
 def test_missing(self):self.assertTrue(run(line(0,11,12))['issues'])
 def test_order(self):self.assertTrue(run(line(0,11,12)+line(1,101,102))['issues'])
 def test_duplicate(self):self.assertTrue(run(line(0,11,12)*2+line(1,90,91))['issues'])
if __name__=='__main__':unittest.main()
