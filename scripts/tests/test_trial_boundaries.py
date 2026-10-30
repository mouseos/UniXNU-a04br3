import hashlib, importlib.util, tempfile, unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'a04br3_trial_boundaries.py'
s=importlib.util.spec_from_file_location('boundaries',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Boundaries(unittest.TestCase):
 def test_continuity(self):
  b={'invocation':'a','main_pid':1,'restarts':0,'dev':1,'inode':2,'size':10,'active':'active'}
  self.assertEqual(m.continuity(b,{**b,'size':15})['status'],'continuous_identity')
  for key,value in [('invocation','b'),('restarts',1),('inode',3),('size',9),('active','failed')]:
   with self.assertRaises(ValueError):m.continuity(b,{**b,key:value})
 def test_old_crash_and_next_reason(self):
  old=b'panic: old\n';a=m.ANCHOR+b'\nBOOT_REASON: 4\n';b=m.ANCHOR+b'\nBOOT_REASON: 3\n';snapshot=old+a+b
  starts=[len(old),len(old)+len(a)];parts=[a,b]
  cap={'snapshot_sha256':hashlib.sha256(snapshot).hexdigest(),'checkpoint_verified':True,'offset_crosses_preloader_anchor':False,'boots_truncated':False,'boots':[{'index':i+1,'start_offset':100+starts[i],'end_offset':100+starts[i]+len(x),'sha256':hashlib.sha256(x).hexdigest(),'closed_by_next_preloader':i==0} for i,x in enumerate(parts)]}
  flash={'uart_offset':100,'flash_completed_uart_offset':100+len(old)}
  out=m.classify(snapshot,cap,flash)
  self.assertTrue(out['trial_exception_free']);self.assertEqual(len(out['old_prefix']['panic']),1);self.assertEqual(out['boots'][0]['next_boot_reasons'],[3])
  cap['boots'][0]['sha256']='0'*64
  with self.assertRaises(ValueError):m.classify(snapshot,cap,flash)
 def test_after_flash_crash_is_ambiguous(self):
  pre=b'panic: late\n';a=m.ANCHOR+b'\n';snap=pre+a+a
  cap={'snapshot_sha256':hashlib.sha256(snap).hexdigest(),'checkpoint_verified':True,'offset_crosses_preloader_anchor':False,'boots_truncated':False,'boots':[{'index':i+1,'start_offset':len(pre)+i*len(a),'end_offset':len(pre)+(i+1)*len(a),'sha256':hashlib.sha256(a).hexdigest(),'closed_by_next_preloader':i==0} for i in range(2)]}
  self.assertFalse(m.classify(snap,cap,{'uart_offset':0,'flash_completed_uart_offset':0})['trial_exception_free'])
 def test_service_output_must_match(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'uart';path.write_bytes(b'abc')
   def run(args,text):
    return ('ActiveState=active\nMainPID=1\nInvocationID=abc\nNRestarts=0\n' if 'show' in args else 'StandardOutput=append:'+str(path)+'\n')
   self.assertEqual(m.service_snapshot(path,'unit',run)['size'],3)
   def wrong(args,text):
    return ('ActiveState=active\nMainPID=1\nInvocationID=abc\nNRestarts=0\n' if 'show' in args else 'StandardOutput=append:/wrong\n')
   with self.assertRaises(ValueError):m.service_snapshot(path,'unit',wrong)
if __name__=='__main__':unittest.main()
