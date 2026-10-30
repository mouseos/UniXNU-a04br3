#!/usr/bin/env python3
"""One-shot controller. Never run until the independent image review is complete."""
import fcntl, hashlib, json, os, re, selectors, signal, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
WORK = ROOT/'port/gsi/logs/ios-feasibility/atp-off-be06c6d-first'
PRIOR = ROOT/'port/gsi/logs/ios-feasibility/cpu-crosscheck-63c4b1e-first'
IMAGE = ROOT/'port/xnu/out/atp-off-be06c6d-boot/boot.img'
MANIFEST = IMAGE.parent/'manifest.json'
BASE = ROOT/'port/xnu/out/alltask-f2dee95-boot/manifest.json'
SAFE = ROOT/'port/xnu/out/iostat-ui-stack-stream-20261030/boot.img'
UART = ROOT/'port/gsi/logs/uart-ttyUSB0-921600.log'
COMMON = ROOT/'port/gsi/logs/flash.log'
COLLECTOR = ROOT/'unixnu/UniXNU-a04br3-read-batch-analysis/scripts/collect-read-batch-uart.py'
REVIEW = WORK/'review-approved.json'
SERIAL = '0C0U265083'
IMG_SHA = 'eee4e1cb90fa4b62042701572fd8f27e2707651fef75e02339300f6d42703222'
MANIFEST_SHA = 'e12806dd0f2a031cac654bf8ec6656155a965b5085f4e14a4351abc73d62fe2a'
ARGS_SHA = '8844754ee328d15c8e767b4ec11990bd6e507a5efae331557df85bf5060242c4'
BASE_SHA = '89a02631bcf6750871f557f70dc44ec71e6d6cd2a8754e4f2f87bad20f995fad'
SAFE_SHA = 'c35596ebe9a18f2dd1bd21d59c7231b8e6a36d2062c82e7d20cd6208e5ac09ea'
PRIOR_IMAGE = 'd6cdef0b08218e15db8a289980649f0f5ea7f2680d86a4740b24a667d8ecd463'
BUILDER_COMMIT = 'be06c6dce0914cd6684a85c2830b58330941f3a8'
PRIOR_HASHES = {'result.json':'c57b54de047931f655220f9368f88d79a657732a24dc9bc7a7db2cdc496ac6c6',
 'run.py':'7d1928a407c8799063cd27f0e04fbd3d9d974071226247ab64e5d18fc368f52b',
 'run.log':'2fe5d600358529afd3ed063c3b5bab483df9ba87bd95d6e682899268b4f53ad3',
 'recovery-flash.log':'09e0e33a5fb6a0167b3075d6f4836ffd1a1ca1d857a88a868cce1d49de529715',
 'flash.json':'c9e592e9cbfc90d8739d30c909d03493b8d5e257b607fa72149c65a10e0c1d6e',
 'flash.log':'0aea35fb305a884b0a56bb9ec7efa78c385484b2d120dadf67efcdcba4afc275',
 'capture-final/capture.json':'d12a17fd572ecc474aa5b2f872ffd8cd130178621825da52d7d4aa96faf6a0a6',
 'capture-final/boot-1.raw':'cce492259763ceb8bf4379dd7d301fa52cc2e068ce3735aafa21ae784c140924'}

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
 return h.hexdigest()

def read_json(path): return json.loads(path.read_text())

def require(condition, message):
 if not condition: raise RuntimeError(message)

def flash_ok(output, returncode):
 return returncode == 0 and len(re.findall(rb"^Sending 'boot'.*\bOKAY\b",output,re.M)) == 1 and len(re.findall(rb"^Writing 'boot'.*\bOKAY\b",output,re.M)) == 1 and output.count(b'OKAY') == 2 and output.count(b'Finished.') == 1

def validate_review(review, evidence):
 require(review.get('status')=='approved' and review.get('image_sha256')==IMG_SHA and review.get('manifest_sha256')==MANIFEST_SHA and review.get('blocking_findings')==[] and isinstance(review.get('reviewer'),str) and bool(review['reviewer'].strip()),'independent image review is not approved for these exact artifacts')
 require(bool(evidence) and hashlib.sha256(evidence).hexdigest()==review.get('evidence_sha256'),'independent review evidence missing or mismatch')

PANIC = re.compile(rb'(?i)(?:panic[ \t]*(?:\(|:)|kernel panic)')
EL3 = re.compile(rb'(?i)(?:EL3[^\r\n]{0,60}(?:exception|abort|serror)|(?:exception|abort|serror)[^\r\n]{0,60}EL3|\bESR_EL3[ \t]*[:=])')

def scanout_summary(raw):
 pattern=re.compile(r'a04br3 gpu scanout: offers=(\d+) completed=(\d+) failed=(\d+) abandoned=(\d+) waits=(\d+) timeouts=(\d+)')
 fields=('offers','completed','failed','abandoned','waits','timeouts')
 rows=[dict(zip(fields,map(int,match.groups()))) for match in pattern.finditer(raw)]
 monotonic=len(rows)>=2 and all(all(b[k]>=a[k] for k in fields) for a,b in zip(rows,rows[1:]))
 delta={k:rows[-1][k]-rows[0][k] for k in fields} if len(rows)>=2 and monotonic else None
 return {'observations':rows,'monotonic':monotonic,'delta':delta,
         'progress_observed':bool(delta and delta['completed']>0),
         'framebuffer_obtained':False,'display_status':'unverified'}

def capture_valid(cap, raw, args, expected, record_sha, raw_sha, snapshot):
 boots=cap.get('boots',[]); lines=[line.split('boot-args = ',1)[1].strip() for line in raw.splitlines() if 'boot-args = ' in line]
 n=cap.get('prefix_bytes_before_first_preloader'); prefix_ok=type(n) is int and 0<=n<=len(snapshot)
 prefix=snapshot[:n] if prefix_ok else b''
 unsplit=cap.get('pre_preloader')
 return bool(cap.get('checkpoint_verified') is True and cap.get('offset_crosses_preloader_anchor') is False and cap.get('first_anchor_after_flash') is True and cap.get('flash_success_claimed') is True and cap.get('flash_boot_sha256')==expected and cap.get('flash_record_sha256')==record_sha and hashlib.sha256(snapshot).hexdigest()==cap.get('snapshot_sha256') and prefix_ok and snapshot[n:].startswith(b'[pmic6392_init] Preloader Start') and not PANIC.search(prefix) and not EL3.search(prefix) and isinstance(unsplit,dict) and isinstance(unsplit.get('panics'),list) and isinstance(unsplit.get('el3_candidates'),list) and not unsplit['panics'] and not unsplit['el3_candidates'] and boots and boots[0].get('sha256')==raw_sha and boots[0].get('closed_by_next_preloader') is True and not cap.get('boots_truncated') and not any(b.get('panics') or b.get('el3_candidates') for b in boots) and {x.get('phase') for x in cap.get('prefix_segments',[])}=={'before_flash_completion','after_flash_completion'} and not any(x.get('panics') or x.get('el3_candidates') for x in cap.get('prefix_segments',[])) and {x.get('value') for x in boots[0].get('boot_reasons',[])}=={4} and (boots[0].get('subsequent_boot_reason_candidate') or {}).get('value')==4 and 'mount-complete volume OwnerData' in raw and 'a04br3-autoreset: 720 s elapsed, resetting into fastboot' in raw and lines and all(line==args for line in lines))

def validate_manifest(manifest, baseline):
 args=manifest.get('kernel_args',''); words=args.split(); keys=[x.partition('=')[0] for x in words]
 require(hashlib.sha256(args.encode('ascii')).hexdigest()==ARGS_SHA and baseline.get('kernel_args')==args+' atp=1','kernel args do not match exact baseline minus atp')
 require(len(keys)==len(set(keys)) and all(words.count(x)==1 for x in ('a04br3-gpu=2','sheng-gpu-addon=1','a04br3-gpu-metal=1','sheng-fb-gpu=1','a04br3-home-perf=1','pl=60','native-msdc-iostat=1','a04br3-autoreset=720')) and not any(x in keys for x in ('atp','cm','a04br3-live-pc')),'GPU, timer, recovery or diagnostics invalid')
 for key in ('source_commit','old_device_commit','new_device_commit','uefi_commit','kernel_build_record','fd_sha256','dtb_sha256','fixed_payload_sha256','owner_data_volume_uuid','old_boot_sha256','old_pair_sha256','seed_provenance_sha256','adt_identity_sha256'):
  require(key in manifest and key in baseline and manifest[key]==baseline[key], 'baseline mismatch: '+key)
 require(manifest.get('boot_sha256')==IMG_SHA and manifest.get('all_task_control') is True and manifest.get('all_task_sampling') is False and manifest.get('live_pc_pid1') is False and manifest.get('comparison_baseline_manifest_sha256')==BASE_SHA and manifest.get('new_device_commit')=='f2dee958a180ae6fee7b84a44baf9d74dcd22aff','manifest identity mismatch')
 return args

def validate_prior():
 for name,digest in PRIOR_HASHES.items(): require(sha(PRIOR/name)==digest,'prior evidence changed: '+name)
 result=read_json(PRIOR/'result.json'); cap=read_json(PRIOR/'capture-final/capture.json'); rec=read_json(PRIOR/'flash.json')
 prior_manifest=ROOT/'port/xnu/out/cpu-crosscheck-63c4b1e-boot/manifest.json'
 require(sha(prior_manifest)=='395a4a983e319011cee52e685b27502f4afb40bb4cd447161853be787d9e6cea','prior manifest mismatch')
 args=read_json(prior_manifest)['kernel_args']; raw=(PRIOR/'capture-final/boot-1.raw').read_text(errors='replace')
 snapshot=(PRIOR/'capture-final/post-flash.raw').read_bytes()
 require(result.get('boot_capture_valid') is True and rec.get('boot_sha256')==PRIOR_IMAGE and rec.get('flash_success') is True and capture_valid(cap,raw,args,PRIOR_IMAGE,sha(PRIOR/'flash.json'),sha(PRIOR/'capture-final/boot-1.raw'),snapshot),'prior capture invalid')
 require(flash_ok((PRIOR/'flash.log').read_bytes(),0) and flash_ok((PRIOR/'recovery-flash.log').read_bytes(),0),'prior flash or recovery not confirmed')
 require('known prior image restored; device retained in fastboot' in (PRIOR/'run.log').read_text(),'prior recovery result missing')

def validate_sources(manifest):
 repos=(('UniXNU-a04br3-atp-off-review',BUILDER_COMMIT),
        ('UniXNU-cs-read-batch-validation',manifest['source_commit']),
        ('UniXNU-a04br3-all-task-sample',manifest['new_device_commit']),
        ('uefi-a04br3',manifest['uefi_commit']))
 for name,expected in repos:
  repo=ROOT/'unixnu'/name
  head=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
  status=subprocess.check_output(['git','-C',str(repo),'status','--porcelain','--untracked-files=normal','--ignore-submodules=all'],text=True).strip()
  require(head==expected and not status,'source revision or cleanliness mismatch: '+name)
 for path,expected in ((IMAGE.parent/'payload.img',manifest['payload_sha256']),
                       (ROOT/'port/xnu/out/alltask-f2dee95/kernel-f2dee95-route-intermediate.macho',manifest['kernel_build_record']['sha256']),
                       (ROOT/'port/xnu/out/a04br3-fd-d3d4f28a/SILICIUM_UEFI.fd',manifest['fd_sha256']),
                       (ROOT/'unixnu/uefi-a04br3/Resources/DTBs/a04br3.dtb',manifest['dtb_sha256'])):
  require(sha(path)==expected,'source artifact hash mismatch: '+path.name)

class Cancellation:
 def __init__(self): self.signum=None
 def handle(self, signum, frame):
  if self.signum is None: self.signum=signum
 def check(self):
  if self.signum is not None: raise RuntimeError('interrupted by signal '+str(self.signum))

def serial_ready(cancel=None):
 rc,out,status=logged_fastboot(['devices'],WORK/'device-check.log',15,cancel=cancel,select_device=False)
 if cancel is not None: cancel.check()
 require(status=='completed' and rc==0,'fastboot device check '+status)
 lines=out.decode('utf-8','replace').splitlines()
 ids=[line.split()[0] for line in lines if len(line.split())>=2 and line.split()[1]=='fastboot']
 return ids==[SERIAL]

def logged_fastboot(arguments, logfile, timeout, cancel=None, select_device=True, on_pre_spawn=None, on_spawn=None, on_launch_error=None):
 command=['fastboot']+(['-s',SERIAL] if select_device else [])+arguments
 chunks=[]; status='launch_error'; rc=None; proc=None; error=None
 with logfile.open('ab',buffering=0) as f, COMMON.open('ab',buffering=0) as common:
  def save(chunk):
   if chunk: f.write(chunk);common.write(chunk);chunks.append(chunk)
  try:
   if cancel is not None: cancel.check()
   if on_pre_spawn is not None: on_pre_spawn()
   proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0)
   if on_spawn is not None: on_spawn()
   deadline=time.monotonic()+timeout; status='completed'
   with selectors.DefaultSelector() as selector:
    selector.register(proc.stdout,selectors.EVENT_READ)
    while selector.get_map():
     if cancel is not None and cancel.signum is not None:
      status='interrupted';break
     remaining=deadline-time.monotonic()
     if remaining<=0:
      status='timeout';break
     for key,_ in selector.select(min(remaining,0.2)):
      chunk=os.read(key.fd,65536)
      if chunk: save(chunk)
      else: selector.unregister(key.fileobj)
   while status=='completed' and proc.poll() is None:
    if cancel is not None and cancel.signum is not None: status='interrupted';break
    if time.monotonic()>=deadline: status='timeout';break
    time.sleep(min(0.05,max(0,deadline-time.monotonic())))
   if status!='completed':
    if proc.poll() is None:
     try: proc.terminate()
     except ProcessLookupError: pass
    try: tail,_=proc.communicate(timeout=3)
    except subprocess.TimeoutExpired:
     proc.kill();tail,_=proc.communicate()
    save(tail)
   rc=proc.returncode if status=='completed' else None
  except BaseException as exc:
   error=type(exc).__name__+': '+str(exc)
   if proc is None and on_launch_error is not None: on_launch_error()
   if proc is not None:
    status='error'
    try:
     if proc.poll() is None: proc.kill()
     tail,_=proc.communicate();save(tail)
    except (OSError,ValueError): pass
   raise
  finally:
   if proc is not None and proc.stdout is not None: proc.stdout.close()
   with (WORK/'fastboot-status.jsonl').open('a') as log:
    log.write(json.dumps({'arguments':arguments,'status':status,'returncode':rc,'error':error})+'\n')
 return rc,b''.join(chunks),status

def observe(offset,cancel):
 anchor=b'[pmic6392_init] Preloader Start'
 for _ in range(45):
  cancel.check()
  with UART.open('rb') as stream:
   stream.seek(offset); data=stream.read(128*1024*1024+1)
  require(len(data)<=128*1024*1024,'UART capture limit exceeded')
  if data.count(anchor)>=2:
   wait_interruptible(25,cancel)
   subprocess.run([sys.executable,str(COLLECTOR),'--uart-log',str(UART),'--flash-record',str(WORK/'flash.json'),'--out',str(WORK/'capture-final')],check=True)
   return
  wait_interruptible(30,cancel)
 try: subprocess.run([sys.executable,str(COLLECTOR),'--uart-log',str(UART),'--flash-record',str(WORK/'flash.json'),'--out',str(WORK/'capture-timeout')],check=False)
 finally: raise RuntimeError('UART timeout waiting for second preloader')

def wait_interruptible(seconds,cancel):
 end=time.monotonic()+seconds
 while True:
  cancel.check(); remaining=end-time.monotonic()
  if remaining<=0:return
  time.sleep(min(remaining,0.2))

def recover():
 for attempt in range(30):
  try:
   if sha(SAFE)==SAFE_SHA and serial_ready():
    rc,out,status=logged_fastboot(['flash','boot',str(SAFE)],WORK/'recovery-flash.log',300)
    if flash_ok(out,rc): return True
  except (OSError,RuntimeError,subprocess.SubprocessError): pass
  if attempt<29: time.sleep(60)
 return False

def write_result(obj):
 tmp=WORK/'result.json.tmp'; tmp.write_text(json.dumps(obj,indent=2)+'\n'); os.replace(tmp,WORK/'result.json')

def finish_with_recovery(result, started, cancel=None):
 # Block while installing ignore handlers, so a second signal cannot interrupt
 # the transition from capture to recovery or result publication.
 signals=(signal.SIGTERM,signal.SIGHUP,signal.SIGINT)
 oldmask=signal.pthread_sigmask(signal.SIG_BLOCK,signals)
 handlers={s:signal.signal(s,signal.SIG_IGN) for s in signals}
 signal.pthread_sigmask(signal.SIG_SETMASK,oldmask)
 try:
  try:
   if started: result['recovery_confirmed']=bool(recover())
  except BaseException as exc:
   result['recovery_confirmed']=False
   result['recovery_error']=type(exc).__name__+': '+str(exc)
  if cancel is not None and cancel.signum is not None:
   result['cancel_signal']=cancel.signum
   result['reason']='interrupted by signal '+str(cancel.signum)
  write_result(result)
 finally:
  # Keep ignored signals discarded while restoring the caller's handlers.
  oldmask=signal.pthread_sigmask(signal.SIG_BLOCK,signals)
  for s,handler in handlers.items(): signal.signal(s,handler)
  signal.pthread_sigmask(signal.SIG_SETMASK,oldmask)

def run_locked():
 require(not any((WORK/x).exists() for x in ('started.json','checkpoint.json','flash.json','result.json','capture-final','capture-timeout')),'trial has already started')
 review=read_json(REVIEW); evidence_path=WORK/'independent-image-review.txt'; validate_review(review,evidence_path.read_bytes())
 require(sha(MANIFEST)==MANIFEST_SHA and sha(IMAGE)==IMG_SHA and sha(BASE)==BASE_SHA and sha(SAFE)==SAFE_SHA,'artifact hash mismatch')
 manifest=read_json(MANIFEST); args=validate_manifest(manifest,read_json(BASE)); validate_prior(); validate_sources(manifest)
 require(serial_ready(),'expected sole fastboot device absent')
 with (WORK/'started.json').open('x') as f: json.dump({'image_sha256':IMG_SHA,'manifest_sha256':MANIFEST_SHA,'review_sha256':sha(REVIEW),'review_evidence_sha256':sha(evidence_path),'builder_commit':BUILDER_COMMIT},f,indent=2)
 subprocess.run([sys.executable,str(COLLECTOR),'--uart-log',str(UART),'--out',str(WORK/'checkpoint.json'),'--checkpoint'],check=True)
 require(sha(IMAGE)==IMG_SHA and sha(MANIFEST)==MANIFEST_SHA and sha(SAFE)==SAFE_SHA,'image changed before flash')
 point=read_json(WORK/'checkpoint.json'); started=False; result={'boot_capture_valid':False,'recovery_confirmed':False,'reason':'not completed','scanout':scanout_summary('')}
 cancel=Cancellation()
 previous={s:signal.signal(s,cancel.handle) for s in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT)}
 try:
  cancel.check()
  def flash_started():
   nonlocal started
   started=True
  rc,out,status=logged_fastboot(['flash','boot',str(IMAGE)],WORK/'flash.log',300,cancel=cancel,on_spawn=flash_started)
  cancel.check()
  require(flash_ok(out,rc),'trial flash '+status+' or unconfirmed')
  record={'uart_offset':point['uart_offset'],'uart_checkpoint':str(WORK/'checkpoint.json'),'flash_completed_uart_offset':UART.stat().st_size,'boot_sha256':IMG_SHA,'flash_success':True,'flash_success_evidence':str(WORK/'flash.log')+' sha256='+sha(WORK/'flash.log')}
  tmp=WORK/'flash.json.tmp'; tmp.write_text(json.dumps(record,indent=2)+'\n'); os.replace(tmp,WORK/'flash.json')
  rc,out,status=logged_fastboot(['reboot'],WORK/'reboot.log',60,cancel=cancel); cancel.check(); require(rc==0,'reboot '+status+' or unconfirmed')
  observe(record['flash_completed_uart_offset'],cancel); cancel.check()
  cap=read_json(WORK/'capture-final/capture.json'); raw_path=WORK/'capture-final/boot-1.raw'; raw=raw_path.read_text(errors='replace'); snapshot=(WORK/'capture-final/post-flash.raw').read_bytes()
  result['boot_capture_valid']=capture_valid(cap,raw,args,IMG_SHA,sha(WORK/'flash.json'),sha(raw_path),snapshot)
  result['scanout']=scanout_summary(raw)
  result['reason']='capture checked' if result['boot_capture_valid'] else 'capture invalid'
 except BaseException as exc:
  result['reason']=type(exc).__name__+': '+str(exc)
 finally:
  if cancel.signum is not None:
   result['cancel_signal']=cancel.signum
   result['reason']='interrupted by signal '+str(cancel.signum)
  try: finish_with_recovery(result,started,cancel)
  finally:
   for sig,handler in previous.items(): signal.signal(sig,handler)
 if result.get('cancel_signal') is not None or not result['recovery_confirmed'] or not result['boot_capture_valid'] or not result.get('scanout',{}).get('progress_observed'):
  raise SystemExit('trial incomplete; see result.json')
 print('capture and scanout counter progress recorded; display unverified; recovery confirmed')

def main():
 with (WORK/'run.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  run_locked()

if __name__=='__main__':
 try: main()
 except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as e: sys.exit(str(e))
