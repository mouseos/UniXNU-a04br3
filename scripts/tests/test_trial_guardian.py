import fcntl, hashlib, importlib.util, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
p=Path(__file__).resolve().parents[1]/'a04br3_trial_guardian.py';s=importlib.util.spec_from_file_location('guardian',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Guardian(unittest.TestCase):
 def test_conflict_filter(self):
  self.assertFalse(m.conflicts([b'/usr/bin/fastboot',b'devices']))
  self.assertFalse(m.conflicts([b'fastboot',b'-s',b'other',b'flash',b'boot']))
  self.assertTrue(m.conflicts([b'fastboot',b'-s',m.SERIAL.encode(),b'flash',b'boot']))
  self.assertTrue(m.conflicts([b'fastboot',b'flash',b'boot']))
  self.assertTrue(m.conflicts([b'mtk']))
 def test_transcript_rejects_wrong_partition_and_extra_lines(self):
  valid=b"Sending 'boot' (1 KB) OKAY [  1.000s]\nWriting 'boot' OKAY [  1.000s]\nFinished. Total time: 2.000s\n"
  self.assertTrue(m.flash_transcript(valid))
  self.assertFalse(m.flash_transcript(valid.replace(b"Writing 'boot'",b"Writing 'recovery'")))
  self.assertFalse(m.flash_transcript(valid+b'FAILED\n'))
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
 def test_superseded_by_later_boot_write(self):
  ok=b"Sending 'boot' (1 KB) OKAY [  1.000s]\nWriting 'boot' OKAY [  1.000s]\nFinished. Total time: 2.000s\n"
  with tempfile.TemporaryDirectory() as t:
   stage=Path(t)/'off';stage.mkdir();common=Path(t)/'flash.log';common.write_bytes(b'old '+ok)
   st={'common_log_offset':common.stat().st_size,'common_log_inode':common.stat().st_ino}
   self.assertFalse(m.superseded(common,st,stage))
   (stage/'flash.log').write_bytes(ok)
   self.assertFalse(m.superseded(common,st,stage))
   with common.open('ab') as f:f.write(ok)
   self.assertFalse(m.superseded(common,st,stage))
   with common.open('ab') as f:f.write(b'\nWrote /x/boot.img to sector 294912 with sector count 262144.\n')
   self.assertTrue(m.superseded(common,st,stage))
   (stage/'flash.log').unlink()
   self.assertTrue(m.superseded(common,st,stage))
   self.assertFalse(m.superseded(common,{**st,'common_log_inode':st['common_log_inode']+1},stage))
   self.assertFalse(m.superseded(common,{},stage))
 def test_superseded_skips_write(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);safe=root/'port/xnu/out/iostat-ui-stack-stream-20261030/boot.img';safe.parent.mkdir(parents=True);safe.write_bytes(b'fake-image')
   stage=root/'port/gsi/logs/ios-feasibility/memory-ce66352-trial/off';stage.mkdir(parents=True)
   common=root/'port/gsi/logs/flash.log';common.write_bytes(b"Writing 'boot' OKAY [  1.000s]\nWriting 'boot' OKAY [  1.000s]\n")
   state={'stage':str(stage),'trial_id':'a'*32,'safe_sha256':hashlib.sha256(b'fake-image').hexdigest(),'common_log_offset':0,'common_log_inode':common.stat().st_ino,'state':'armed'}
   with patch.object(m,'SAFE_SHA',state['safe_sha256']),patch.object(m.subprocess,'run',side_effect=AssertionError('write over newer image')):
    self.assertEqual(m.restore_device(root,state),'superseded')
    path=root/'state';lock=root/'lock';m.write_state(path,state)
    self.assertEqual(m.recover_if_abandoned(path,lock,lambda s:m.restore_device(root,s)),'restored')
   self.assertEqual(json.loads(path.read_text())['recovery'],'superseded')
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
 def test_fake_fastboot_recovers_and_logs(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);safe=root/'port/xnu/out/iostat-ui-stack-stream-20261030/boot.img';safe.parent.mkdir(parents=True);safe.write_bytes(b'fake-image')
   stage=root/'port/gsi/logs/ios-feasibility/memory-ce66352-trial/off';stage.mkdir(parents=True)
   binary=root/'fastboot';binary.write_text('''#!/usr/bin/env python3
import sys
if sys.argv[1:] == ['devices']: print('0C0U265083 fastboot')
else:
 print("Sending 'boot' (1 KB) OKAY [  1.000s]")
 print("Writing 'boot' OKAY [  1.000s]")
 print('Finished. Total time: 2.000s')
''');binary.chmod(0o755)
   with patch.object(m,'SAFE_SHA',hashlib.sha256(b'fake-image').hexdigest()),patch.dict(os.environ,{'PATH':str(root)+':'+os.environ['PATH']}):
    self.assertTrue(m.restore_device(root,{'stage':str(stage),'trial_id':'a'*32,'safe_sha256':hashlib.sha256(b'fake-image').hexdigest()}))
    with patch.object(m.subprocess,'run',side_effect=AssertionError('duplicate write')):
     self.assertTrue(m.restore_device(root,{'stage':str(stage),'trial_id':'a'*32,'safe_sha256':hashlib.sha256(b'fake-image').hexdigest()}))
   self.assertIn(b"Writing 'boot'",(stage/'guardian-recovery.log').read_bytes())
 def test_published_recovery_prevents_duplicate_write(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);safe=root/'port/xnu/out/iostat-ui-stack-stream-20261030/boot.img';safe.parent.mkdir(parents=True);safe.write_bytes(b'fake-image')
   stage=root/'port/gsi/logs/ios-feasibility/memory-ce66352-trial/off';stage.mkdir(parents=True)
   (stage/'started.json').write_text(json.dumps({'image_sha256':'abc','trial_id':'a'*32}))
   (stage/'fastboot-status.jsonl').write_text(json.dumps({'arguments':['flash','boot',str(safe)],'status':'completed','returncode':0})+'\n')
   (stage/'recovery-flash.log').write_text("Sending 'boot' (1 KB) OKAY [  1.000s]\nWriting 'boot' OKAY [  1.000s]\nFinished. Total time: 2.000s\n")
   loghash=hashlib.sha256((stage/'recovery-flash.log').read_bytes()).hexdigest()
   (stage/'result.json').write_text(json.dumps({'recovery_write_confirmed':True,'trial_id':'a'*32,'safe_sha256':hashlib.sha256(b'fake-image').hexdigest(),'recovery_log_sha256':loghash}))
   with patch.object(m,'SAFE_SHA',hashlib.sha256(b'fake-image').hexdigest()),patch.object(m.subprocess,'run',side_effect=AssertionError('duplicate write')):
    self.assertTrue(m.restore_device(root,{'stage':str(stage),'image_sha256':'abc','trial_id':'a'*32,'safe_sha256':hashlib.sha256(b'fake-image').hexdigest()}))
if __name__=='__main__':unittest.main()
