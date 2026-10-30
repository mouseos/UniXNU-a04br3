import fcntl, importlib.util, json, subprocess, sys, tempfile, unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'a04br3_trial_guardian.py';s=importlib.util.spec_from_file_location('guardian',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Guardian(unittest.TestCase):
 def test_interruption_and_retry(self):
  with tempfile.TemporaryDirectory() as t:
   state=Path(t)/'state';lock=Path(t)/'lock';m.write_state(state,{'state':'armed','stage':'off'})
   with lock.open('a') as f:
    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
    self.assertEqual(m.recover_if_abandoned(state,lock,lambda s:True),'active')
   self.assertEqual(m.recover_if_abandoned(state,lock,lambda s:False),'retry')
   self.assertEqual(json.loads(state.read_text())['state'],'armed')
   self.assertEqual(m.recover_if_abandoned(state,lock,lambda s:True),'restored')
   self.assertEqual(json.loads(state.read_text())['recovery_boot'],'unverified')
   self.assertEqual(m.recover_if_abandoned(state,lock,lambda s:self.fail('duplicate restore')),'restored')
 def test_invalid_state_fails_closed(self):
  with tempfile.TemporaryDirectory() as t:
   state=Path(t)/'state';m.write_state(state,{'state':'corrupt'})
   with self.assertRaises(ValueError):m.recover_if_abandoned(state,Path(t)/'lock',lambda s:True)
 def test_killed_controller_and_restart(self):
  with tempfile.TemporaryDirectory() as t:
   state=Path(t)/'state';lock=Path(t)/'lock';m.write_state(state,{'state':'armed'})
   code='import fcntl,sys,time; f=open(sys.argv[1],"a");fcntl.flock(f,fcntl.LOCK_EX);print("ready",flush=True);time.sleep(300)'
   child=subprocess.Popen([sys.executable,'-c',code,str(lock)],stdout=subprocess.PIPE,text=True)
   self.assertEqual(child.stdout.readline().strip(),'ready')
   self.assertEqual(m.recover_if_abandoned(state,lock,lambda s:True),'active')
   child.kill();child.wait();child.stdout.close()
   self.assertEqual(m.recover_if_abandoned(state,lock,lambda s:True),'restored')
if __name__=='__main__':unittest.main()
