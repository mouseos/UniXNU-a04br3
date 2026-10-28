"""Offline counter semantics and real host TCP integrity/failure tests."""
from pathlib import Path
import json,os,runpy,socket,subprocess,tempfile,threading,time,unittest,struct,errno
R=Path(__file__).resolve().parents[2]
report=runpy.run_path(str(R/'scripts/a04br3-performance-report.py'))['report']
server_module=runpy.run_path(str(R/'scripts/a04br3-network-bench-server.py'));serve=server_module['serve'];fnv=server_module['fnv']
class Reports(unittest.TestCase):
 def test_epochs_clock_cpu(self):
  boot=b'Darwin Kernel Version test\n'
  raw=boot+b'ArmCpu: runtime-cycle cpu=2 round=0 mpidr=80000002 status=0 cntfrq=13000000\nArmCpu: runtime-cycle-count cpu=2 ticks=13000 cycles=1000000 outer=13001\na04br3 cpu-load cpu=0 at_ns=1 user=2 system=3 idle=5 nice=0 state_before=4 state_after=4\na04br3 cpu-load cpu=0 at_ns=2 user=3 system=6 idle=11 nice=0 state_before=4 state_after=4\n'
  data=report(raw+b'[pmic6392_init] Preloader Start\n'+boot+b'a04br3 cpu-load cpu=0 at_ns=3 user=10 system=10 idle=10 nice=0\n',1)
  self.assertEqual(data['cpu_direct_cycles'][0]['mhz'],1000)
  self.assertEqual(data['cpu_interval_usage'][0]['busy_percent'],40)
  self.assertEqual(report(raw+b'[pmic6392_init] Preloader Start\n'+boot,2)['cpu_interval_usage'],[])
  with self.assertRaises(ValueError):report(raw,0)
 def test_reject_reset_incomplete_io(self):
  raw=b'Darwin Kernel Version test\na04br3 cpu-load cpu=0 at_ns=1 user=10 system=0 idle=20 nice=0\na04br3 cpu-load cpu=0 at_ns=2 user=1 system=0 idle=2 nice=0\na04br3 msdc io sample seq=1 begin_ns=1 end_ns=2\na04br3 msdc transfer seq=1 mode=dma_read size=le4k calls=1 ok_bytes=4096 errors=0 elapsed_ns=1000\na04br3 msdc io end seq=1 rows=16\n'
  a=report(raw,1);self.assertFalse(a['cpu_interval_usage'][0]['valid']);self.assertEqual(a['emmc_complete_samples'],[])
 def test_truncated_vm(self):
  data=report(b'Darwin Kernel Version test\na04br3 perf vm pagesize=\npartial',1)
  self.assertTrue(data['truncated_tail_ignored']);self.assertEqual(data['memory_usage'],[])
 def test_complete_io_and_panic_banner(self):
  base='[pmic6392_init] Preloader Start\nDarwin Kernel Version test\na04br3 msdc io sample seq=1 begin_ns=1 end_ns=2\n'
  for mode in ('dma_read','pio_read','write'):
   for size in ('le4k','4k_16k','gt16k'):base+=f'a04br3 msdc transfer seq=1 mode={mode} size={size} calls=1 ok_bytes=1048576 errors=0 elapsed_ns=1000000000\n'
  for mode in ('read','write'):
   for size in ('le4k','4k_16k','gt16k'):base+=f'a04br3 msdc offered seq=1 mode={mode} size={size} calls=1 bytes=1048576\n'
  base+='a04br3 msdc queue seq=1 started=1 wait_ns=1 max_wait_ns=1\na04br3 msdc io end seq=1 rows=16\npanic(cpu 0)\nDarwin Kernel Version panic-repeat\n[pmic6392_init] Preloader Start\nBOOT_REASON: 9\n'
  a=report(base.encode(),1);self.assertEqual(a['epoch_count'],1);self.assertTrue(a['panic_observed']);self.assertEqual(a['boot_inventory'][-1]['boot_reasons'],[9]);self.assertEqual(a['emmc_complete_samples'][0]['transfers'][0]['service_MiB_s'],1)
  self.assertEqual(report(base.replace('rows=16','rows=15').encode(),1)['emmc_complete_samples'],[])
class Network(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.bin=Path(cls.tmp.name)/'bench';cls.short=Path(cls.tmp.name)/'short'
  for binary,flags in [(cls.bin,[]),(cls.short,['-DA04_NETWORK_DEADLINE_NS=200000000ULL'])]:
   subprocess.run([os.environ.get('CC','cc'),'-std=c11','-O2','-Wall','-Wextra','-Werror',*flags,str(R/'tools/a04br3-network-bench.c'),'-o',str(binary)],check=True)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def exercise(self,mode,binary=None):
  errors=[];stop=threading.Event()
  with socket.socket() as listener:
   listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(3)
   def server():
    try:
     conn,_=listener.accept()
     with conn:
      if mode=='valid':self.assertTrue(serve(conn)['verified'])
      elif mode in ('corrupt','bad_download','bad_final','missing_final'):
       conn.settimeout(3)
       def take(n):
        b=b''
        while len(b)<n:
         part=conn.recv(n-len(b))
         if not part:raise ValueError('EOF')
         b+=part
        return b
       _,_,n,_=struct.unpack('!IIII',take(16));payload=take(n)
       conn.sendall(struct.pack('!I',0 if mode=='corrupt' else fnv(payload)))
       if mode!='corrupt':
        download=bytes(x^0xa5 for x in payload)
        if mode=='bad_download':download=bytes([download[0]^1])+download[1:]
        conn.sendall(download)
        if mode!='bad_download':
         take(4)
         if mode!='missing_final':conn.sendall(struct.pack('!I',0))
      elif mode=='eof':pass
      else:stop.wait(2)
    except Exception as e:errors.append(e)
   thread=threading.Thread(target=server);thread.start()
   result=subprocess.run([str(binary or self.bin),'--run','127.0.0.1',str(listener.getsockname()[1]),'1048576'],text=True,capture_output=True,timeout=5)
   stop.set();thread.join(3);self.assertFalse(thread.is_alive())
  if mode=='valid':
   self.assertEqual(errors,[]);self.assertEqual(result.returncode,0,result.stderr)
   complete=[json.loads(x) for x in result.stdout.splitlines() if '"phase":"complete"' in x]
   self.assertEqual(len(complete),1);self.assertTrue(complete[0]['verified']);self.assertEqual(complete[0]['bytes_each_direction'],1048576)
  else:self.assertNotEqual(result.returncode,0);self.assertNotIn('"verified":true',result.stdout)
  if mode in ('corrupt','bad_download','bad_final'):self.assertIn('errno='+str(errno.EBADMSG),result.stderr)
 def test_real_tcp(self):self.exercise('valid')
 def test_corruption(self):self.exercise('corrupt')
 def test_bad_download(self):self.exercise('bad_download')
 def test_bad_final(self):self.exercise('bad_final')
 def test_missing_final(self):self.exercise('missing_final')
 def test_c_server(self):
  with socket.socket() as pick:
   pick.bind(('127.0.0.1',0));port=pick.getsockname()[1]
  with subprocess.Popen([str(self.bin),'--serve','127.0.0.1',str(port)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as server:
   self.assertIn('listening',server.stdout.readline())
   client=subprocess.run([str(self.bin),'--run','127.0.0.1',str(port),'1048576'],capture_output=True,text=True,timeout=5)
   out,err=server.communicate(timeout=5)
   self.assertEqual(client.returncode,0,client.stderr);self.assertEqual(server.returncode,0,err);self.assertIn('server_complete',out);self.assertIn('client_processing',client.stdout)
 def test_short_eof(self):self.exercise('eof')
 def test_deadline(self):self.exercise('stall',self.short)
 def test_default_does_not_connect(self):
  for args in [[],['--run','localhost','1','1'],['--run','127.0.0.1','0','1'],['--run','127.0.0.1','1','16777217']]:
   self.assertEqual(subprocess.run([str(self.bin),*args],capture_output=True).returncode,2)
if __name__=='__main__':unittest.main()
