import pathlib,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
class Tests(unittest.TestCase):
 def test_read_only_and_unavailable(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);(p/'sys').mkdir();(p/'sys/sysctl.h').write_text('#include <stddef.h>\nint sysctlbyname(const char *, void *, size_t *, void *, size_t);\n')
   (p/'stub.c').write_text('''#include <stddef.h>\n#include <stdint.h>\n#include <string.h>\n#include <errno.h>\n#include <stdlib.h>\nint sysctlbyname(const char*n,void*o,size_t*l,void*v,size_t z){if(v||z)abort();if(getenv("ALL_ZERO")){*(uint32_t*)o=0;*l=4;return 0;}if(getenv("SENTINEL")){*(uint32_t*)o=UINT32_MAX;*l=4;return 0;}if(strstr(n,"_pressure")){errno=2;return -1;}if(strstr(n,"_critical")){*l=2;return 0;}*(uint32_t*)o=42;*l=4;return 0;}\n''')
   exe=p/'test';subprocess.run(['cc','-D__APPLE__','-I',d,str(ROOT/'tools/a04br3-memory-pressure-observe.c'),str(p/'stub.c'),'-o',str(exe)],check=True)
   r=subprocess.run([str(exe),'--sample'],capture_output=True,text=True);self.assertEqual(r.returncode,1);self.assertEqual(r.stdout.count('status=ok'),2);self.assertIn('status=unavailable errno=2',r.stdout);self.assertIn('status=invalid length=2',r.stdout);self.assertIn('unavailable=2',r.stdout)
   r=subprocess.run([str(exe)],capture_output=True,text=True);self.assertEqual(r.returncode,2);self.assertFalse(r.stdout)
   import os
   r=subprocess.run([str(exe),'--sample'],capture_output=True,text=True,env={**os.environ,'ALL_ZERO':'1'});self.assertEqual(r.returncode,0);self.assertEqual(r.stdout.count('status=ok value=0'),4)
   self.assertEqual([x.split('name=')[1].split()[0] for x in r.stdout.splitlines()[:4]],['kern.memorystatus_level','kern.memorystatus_available_pages','kern.memorystatus_available_pages_pressure','kern.memorystatus_available_pages_critical'])
   r=subprocess.run([str(exe),'--sample'],capture_output=True,text=True,env={**os.environ,'SENTINEL':'1'});self.assertEqual(r.returncode,1);self.assertEqual(r.stdout.count('status=invalid'),4)
if __name__=='__main__':unittest.main()
