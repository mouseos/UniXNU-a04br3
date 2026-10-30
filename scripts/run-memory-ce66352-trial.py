#!/usr/bin/env python3
"""既存の実機controllerを用いたce66352計測。レビュー記録は対象ハッシュと結び付ける。"""
import argparse, fcntl, hashlib, importlib.util, json, os, re, shutil, subprocess, sys, types, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
WORK=ROOT/'port/gsi/logs/ios-feasibility/memory-ce66352-trial'
SOURCE=ROOT/'unixnu/UniXNU-a04br3-memory-low-impact/scripts/a04br3_trial_base.py'
ART=ROOT/'port/xnu/out/memory-ce66352-boot'
MANIFEST=ART/'manifest.json'
SAFE=ROOT/'port/xnu/out/iostat-ui-stack-stream-20261030/boot.img'
UART=ROOT/'port/gsi/logs/uart-ttyUSB0-921600.log'
DEVICE_COMMIT='ce66352db798560ac8c4f380d66bf78876d6c7e2'
PARSER_COMMIT='ed98bfa1c429e26de66bc42b88859eb923dbda98'
SUPPORT_COMMIT='1919bcb5fba43f1d8eaa41d426cb84598ad291b4'
REPO=ROOT/'unixnu/UniXNU-a04br3-memory-low-impact'
REVIEW=REPO/'configs/memory-ce66352-trial-review.json'
PARSER='scripts/summarize-memory-observation.py'
DEVICE_FILES=('tools/a04br3-memory-copy-bench.c','package/A04BR3ProbeLaunch.c',
              'package/A04BR3Board.cpp','scripts/build-a04br3-memory-bench.py')
GUARDIAN_UNIT='a04br3-ce66352-recovery.service'
ANCHOR=b'[pmic6392_init] Preloader Start'
def low_capacity_poweroff(segment):
 # LK shows the low-capacity logo and powers off before UniXNU when the battery
 # gauge reports low capacity; the charger then powers the device on again.
 return b'mt_disp_show_low_capacity_logo' in segment and b'mt_power_off' in segment and b'UNIXNU' not in segment
def trial_boot(stage_dir,snapshot,cap,separation):
 """Locate the trial boot after leading LK low-capacity power-off boots and verify it against the capture."""
 info=json.loads((stage_dir/'trial-boot.json').read_text());idx=info['index']
 boots=cap['boots'];need(type(idx) is int and 1<=idx<=3 and len(boots)>=idx+1 and len(separation['boots'])>=idx+1,'試験起動の番号不正')
 base=cap['flash_offset']
 def segment(i):
  rec=boots[i-1];data=(stage_dir/f'capture-final/boot-{i}.raw').read_bytes()
  need(rec['index']==i and rec['closed_by_next_preloader'] is True and hashlib.sha256(data).hexdigest()==rec['sha256']==separation['boots'][i-1]['sha256'] and snapshot[rec['start_offset']-base:rec['end_offset']-base]==data,'起動区間の照合失敗')
  return data
 for i in range(1,idx):
  reasons=separation['boots'][i-1]['events']['boot_reasons']
  need(low_capacity_poweroff(segment(i)) and reasons and all(r==4 for r in reasons),'低残量起動の判定不正')
 data=segment(idx);entry=separation['boots'][idx-1]
 reasons=entry['events']['boot_reasons'];following=entry['next_boot_reasons'];expected_reason=4 if idx==1 else 1
 need(separation['trial_exception_free'] and reasons and all(r==expected_reason for r in reasons) and following and all(r==4 for r in following),'起動または例外の証拠不正')
 return idx,data
def digest(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def need(ok,msg):
 if not ok: raise RuntimeError(msg)
def start_guardian():
 # Install only the exact unit owned by this controller; never overwrite another unit.
 guardian=REPO/'scripts/a04br3_trial_guardian.py'
 def quote(s): return '"'+str(s).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
 unit='[Unit]\nDescription=A04BR3 trial recovery guardian\n[Service]\nType=simple\nExecStart='+quote(sys.executable)+' '+quote(guardian)+' --root '+quote(ROOT)+' --watch\nRestart=always\nRestartSec=5\n[Install]\nWantedBy=default.target\n'
 path=Path.home()/'.config/systemd/user'/GUARDIAN_UNIT
 path.parent.mkdir(parents=True,exist_ok=True)
 if path.exists(): need(path.read_text()==unit,'既存の復旧サービス設定が異なります')
 else: path.write_text(unit)
 subprocess.run(['loginctl','enable-linger',str(os.getuid())],check=True)
 subprocess.run(['systemctl','--user','daemon-reload'],check=True)
 subprocess.run(['systemctl','--user','enable','--now',GUARDIAN_UNIT],check=True)
 # Always restart so a resident guardian runs the code verified by check().
 subprocess.run(['systemctl','--user','restart',GUARDIAN_UNIT],check=True)
 need(subprocess.run(['systemctl','--user','is-active','--quiet',GUARDIAN_UNIT]).returncode==0,'復旧サービスが起動していません')
 state=WORK/'recovery-state.json'
 if state.exists(): need(json.loads(state.read_text()).get('state')=='restored','以前の復旧処理が未完了')
def check_object_sources(repo=REPO):
 for commit in (DEVICE_COMMIT,PARSER_COMMIT,SUPPORT_COMMIT):
  subprocess.run(['git','-C',str(repo),'cat-file','-e',commit+'^{commit}'],check=True,stdout=subprocess.DEVNULL)
 subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',DEVICE_COMMIT,PARSER_COMMIT],check=True)
 for commit,paths in ((DEVICE_COMMIT,DEVICE_FILES),(SUPPORT_COMMIT,('scripts/a04br3_trial_guardian.py','scripts/a04br3_trial_boundaries.py','scripts/a04br3_trial_evidence.py','scripts/a04br3_trial_image_audit.py','scripts/a04br3_trial_base.py','scripts/collect-read-batch-uart.py'))):
  for path in paths:
   blob=subprocess.check_output(['git','-C',str(repo),'show',commit+':'+path])
   need(hashlib.sha256(blob).digest()==hashlib.sha256((repo/path).read_bytes()).digest(),'作業ツリーの対象ソース不一致: '+path)
 subprocess.check_output(['git','-C',str(repo),'cat-file','-e',PARSER_COMMIT+':'+PARSER])
def check(mode):
 m=json.loads(MANIFEST.read_text());v=m['variants'][mode]
 need(digest(ART/f'{mode}-boot.img')==v['boot_sha256'],'boot不一致')
 need(digest(SAFE)=='c35596ebe9a18f2dd1bd21d59c7231b8e6a36d2062c82e7d20cd6208e5ac09ea','復旧boot不一致')
 need(m['variants']['on']['kernel_args']==m['variants']['off']['kernel_args']+' mb=1','計測引数差不一致')
 need(len(v['kernel_args'].encode('ascii'))<=1023 and v['kernel_args'].split().count('a04br3-autoreset=720')==1,'起動引数不正')
 need(m['variants']['on']['kernel_sha256']==m['variants']['off']['kernel_sha256'],'kernelが不一致')
 need(m['device_commit']==DEVICE_COMMIT,'計測ソースcommit不一致')
 check_object_sources()
 need(subprocess.run(['systemctl','--user','is-active','--quiet','a04br3-uart-main-20261030.service']).returncode==0,'UART収集停止')
 review=json.loads(REVIEW.read_text())
 need(len(review.get('independent_evidence',[]))==2 and len(review.get('findings',[]))>=4,'独立レビューの記録不足')
 need(review.get('manifest_sha256')==digest(MANIFEST) and all(review.get(f'{n}_boot_sha256')==m['variants'][n]['boot_sha256'] for n in ('off','on')),'レビュー対象不一致')
 return m

def main_locked(mode):
 m=check(mode);stage=WORK/mode;stage.mkdir(exist_ok=True)
 audit_spec=importlib.util.spec_from_file_location('trial_image_audit',REPO/'scripts/a04br3_trial_image_audit.py')
 audit=importlib.util.module_from_spec(audit_spec);audit_spec.loader.exec_module(audit)
 image_audit=audit.audit(ROOT)
 need(all(image_audit['checks'].values()) and image_audit['manifest_sha256']==digest(MANIFEST),'画像内部の実体照合に失敗')
 (stage/'image-audit.json').write_text(json.dumps(image_audit,ensure_ascii=False,indent=2)+'\n')
 start_guardian()
 spec=importlib.util.spec_from_file_location('original_controller',SOURCE);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
 mod.WORK=stage;mod.IMAGE=ART/f'{mode}-boot.img';mod.MANIFEST=MANIFEST;mod.BASE=MANIFEST;mod.SAFE=SAFE;mod.UART=UART;mod.REVIEW=REVIEW
 mod.ROOT=ROOT;mod.COMMON=ROOT/'port/gsi/logs/flash.log';mod.COLLECTOR=REPO/'scripts/collect-read-batch-uart.py'
 mod.IMG_SHA=m['variants'][mode]['boot_sha256'];mod.MANIFEST_SHA=digest(MANIFEST);mod.BASE_SHA=mod.MANIFEST_SHA;mod.SAFE_SHA=digest(SAFE);mod.BUILDER_COMMIT=m['device_commit']
 mod.validate_review=lambda _review,_evidence:check(mode)
 mod.validate_manifest=lambda _manifest,_base:check(mode)['variants'][mode]['kernel_args']
 mod.validate_prior=lambda:check(mode)
 mod.validate_sources=lambda _manifest:check(mode)
 parser_source=subprocess.check_output(['git','-C',str(REPO),'show',PARSER_COMMIT+':'+PARSER])
 parser=types.ModuleType('fixed_memory_parser')
 exec(compile(parser_source,PARSER_COMMIT+':'+PARSER,'exec'),parser.__dict__)
 boundary_path=REPO/'scripts/a04br3_trial_boundaries.py'
 boundary_spec=importlib.util.spec_from_file_location('trial_boundaries',boundary_path)
 boundary=importlib.util.module_from_spec(boundary_spec);boundary_spec.loader.exec_module(boundary)
 evidence_spec=importlib.util.spec_from_file_location('trial_bench_evidence',REPO/'scripts/a04br3_trial_evidence.py')
 bench_evidence=importlib.util.module_from_spec(evidence_spec);evidence_spec.loader.exec_module(bench_evidence)
 guardian_spec=importlib.util.spec_from_file_location('trial_guardian',REPO/'scripts/a04br3_trial_guardian.py')
 guardian=importlib.util.module_from_spec(guardian_spec);guardian_spec.loader.exec_module(guardian)
 uart_start={}
 old_fastboot=mod.logged_fastboot
 def fastboot_guard(arguments,*args,**kwargs):
  if arguments[:2]==['flash','boot'] and len(arguments)>2 and Path(arguments[2])==mod.IMAGE:
   uart_start.update(boundary.service_snapshot(UART,'a04br3-uart-main-20261030.service',subprocess.check_output))
   need(shutil.which('fastboot') is not None,'fastbootが見つかりません')
   def arm():
    trial_id=uuid.uuid4().hex
    started_path=stage/'started.json';started=json.loads(started_path.read_text());started['trial_id']=trial_id
    guardian.write_state(started_path,started)
    common=mod.COMMON.stat()
    guardian.write_state(WORK/'recovery-state.json',{'state':'armed','stage':str(stage.resolve()),'image_sha256':mod.IMG_SHA,'safe_sha256':mod.SAFE_SHA,'trial_id':trial_id,'common_log_offset':common.st_size,'common_log_inode':common.st_ino})
   def launch_failed():
    state=WORK/'recovery-state.json'
    if state.exists():
     journal=json.loads(state.read_text())
     if journal.get('state')=='armed' and journal.get('image_sha256')==mod.IMG_SHA:
      guardian.write_state(state,{**journal,'state':'restored','recovery':'not-started','recovery_boot':'unverified'})
   kwargs['on_pre_spawn']=arm;kwargs['on_launch_error']=launch_failed
  return old_fastboot(arguments,*args,**kwargs)
 mod.logged_fastboot=fastboot_guard
 trial_ctx={}
 def observe_trial(offset,cancel):
  for _ in range(50):
   cancel.check()
   with UART.open('rb') as stream:
    stream.seek(offset);data=stream.read(128*1024*1024+1)
   need(len(data)<=128*1024*1024,'UART capture limit exceeded')
   pos=[m.start() for m in re.finditer(re.escape(ANCHOR),data)]
   skipped=0
   while skipped<2 and skipped+1<len(pos) and low_capacity_poweroff(data[pos[skipped]:pos[skipped+1]]):skipped+=1
   if len(pos)>=skipped+2:
    mod.wait_interruptible(25,cancel)
    subprocess.run([sys.executable,str(mod.COLLECTOR),'--uart-log',str(UART),'--flash-record',str(stage/'flash.json'),'--out',str(stage/'capture-final')],check=True)
    guardian.write_state(stage/'trial-boot.json',{'index':skipped+1,'skipped_low_capacity_boots':skipped})
    return
   mod.wait_interruptible(30,cancel)
  try: subprocess.run([sys.executable,str(mod.COLLECTOR),'--uart-log',str(UART),'--flash-record',str(stage/'flash.json'),'--out',str(stage/'capture-timeout')],check=False)
  finally: raise RuntimeError('UART timeout waiting for the trial boot to close')
 mod.observe=observe_trial
 original_scanout=mod.scanout_summary
 mod.scanout_summary=lambda raw:original_scanout(trial_ctx.get('text',raw))
 def capture_with_bench(cap,raw,args,expected,record_sha,raw_sha,snapshot):
  first=(stage/'capture-final/boot-1.raw').read_bytes()
  need(hashlib.sha256(first).hexdigest()==raw_sha,'捕捉の先頭起動ログ不一致')
  # The fixed parser authenticates the flash record, snapshot and first boundary.
  parser.capture_evidence(first,expected,stage/'capture-final/capture.json',stage/'flash.json',stage/'capture-final/post-flash.raw',stage/'flash.log')
  flash=json.loads((stage/'flash.json').read_text())
  separation=boundary.classify(snapshot,cap,flash)
  index,data=trial_boot(stage,snapshot,cap,separation)
  raw=data.decode('utf8','replace');trial_ctx['text']=raw
  final_state=boundary.service_snapshot(UART,'a04br3-uart-main-20261030.service',subprocess.check_output)
  continuity=boundary.continuity(uart_start,final_state)
  report={'separation':separation,'uart_continuity':continuity,'trial_boot_index':index}
  tmp=stage/'boot-boundaries.json.tmp';tmp.write_text(json.dumps(report,indent=2)+'\n');tmp.replace(stage/'boot-boundaries.json')
  lines=[line.split('boot-args = ',1)[1].strip() for line in raw.splitlines() if 'boot-args = ' in line]
  need('mount-complete volume OwnerData' in raw and 'a04br3-autoreset: 720 s elapsed, resetting into fastboot' in raw and lines and all(line==args for line in lines),'起動または例外の証拠不正')
  if mode=='off':
   need(b'a04br3_mem_copy ' not in data and b'a04br3: memory bench spawn ' not in data,'offでbench活動を観測')
   analysis={'assessment':{'status':'disabled'}}
  else:
   spawns=re.findall(rb'a04br3: memory bench spawn\s+(-?\d+)\s+\(pid\s+(-?\d+)\)',data)
   need(len(spawns)==1 and int(spawns[0][0])==0 and int(spawns[0][1])>0,'bench起動記録不正')
   analysis=parser.analyze(data,expected)
   success=parser.assess(data,analysis,'copy',65536,True)
   need(success or analysis.get('assessment',{}).get('status')=='skip','bench未完了')
   strict=bench_evidence.analyze_benchmark(data.decode('utf8','replace'),'on')
   need(strict['valid'] and (strict['outcome']=='completed' if success else strict['outcome']=='skip'),'bench統計またはskipの整合性不正')
   analysis['bench_crosscheck']=strict
  analysis.update(parser_commit=PARSER_COMMIT,parser_sha256=hashlib.sha256(parser_source).hexdigest(),boot_raw_sha256=hashlib.sha256(data).hexdigest(),trial_boot_index=index,first_boot_raw_sha256=raw_sha,image_sha256=expected)
  tmp=stage/'benchmark-analysis.json.tmp';tmp.write_text(json.dumps(analysis,indent=2)+'\n');tmp.replace(stage/'benchmark-analysis.json')
  return True
 mod.capture_valid=capture_with_bench
 original_write_result=mod.write_result
 def write_result_with_bench(result):
  result['recovery_write_confirmed']=result.get('recovery_confirmed') is True
  result['recovery_boot_verified']=False
  result['recovery_confirmed_meaning']='legacy field: recovery boot partition write only'
  state_path=WORK/'recovery-state.json'
  if state_path.exists():
   journal=json.loads(state_path.read_text())
   if journal.get('state')=='armed' and journal.get('image_sha256')==mod.IMG_SHA:
    result['trial_id']=journal.get('trial_id');result['safe_sha256']=mod.SAFE_SHA
    if result['recovery_write_confirmed']:result['recovery_log_sha256']=digest(stage/'recovery-flash.log')
  path=stage/'benchmark-analysis.json'
  if path.exists(): result['benchmark_analysis_sha256']=digest(path)
  path=stage/'boot-boundaries.json'
  if path.exists(): result['boot_boundaries_sha256']=digest(path)
  original_write_result(result)
 mod.write_result=write_result_with_bench
 (stage/'independent-image-review.txt').write_bytes(REVIEW.read_bytes())
 if mode=='on':
  off_dir=WORK/'off';off=json.loads((off_dir/'result.json').read_text());analysis_path=off_dir/'benchmark-analysis.json'
  analysis=json.loads(analysis_path.read_text())
  off_first=(off_dir/'capture-final/boot-1.raw').read_bytes()
  off_cap=json.loads((off_dir/'capture-final/capture.json').read_text())
  off_flash=json.loads((off_dir/'flash.json').read_text())
  parser.capture_evidence(off_first,m['variants']['off']['boot_sha256'],off_dir/'capture-final/capture.json',off_dir/'flash.json',off_dir/'capture-final/post-flash.raw',off_dir/'flash.log')
  off_snapshot=(off_dir/'capture-final/post-flash.raw').read_bytes()
  off_separation=boundary.classify(off_snapshot,off_cap,off_flash)
  off_index,off_raw=trial_boot(off_dir,off_snapshot,off_cap,off_separation)
  boundary_file=off_dir/'boot-boundaries.json';saved_boundary=json.loads(boundary_file.read_text())
  boundary.continuity(saved_boundary['uart_continuity']['before'],saved_boundary['uart_continuity']['after'])
  text=off_raw.decode('utf8','replace');off_args=[line.split('boot-args = ',1)[1].strip() for line in text.splitlines() if 'boot-args = ' in line]
  need(off.get('boot_capture_valid') is True and off.get('recovery_confirmed') is True and mod.flash_ok((off_dir/'recovery-flash.log').read_bytes(),0) and saved_boundary.get('trial_boot_index')==off_index and analysis.get('trial_boot_index')==off_index and 'mount-complete volume OwnerData' in text and 'a04br3-autoreset: 720 s elapsed, resetting into fastboot' in text and off_args and all(a==m['variants']['off']['kernel_args'] for a in off_args) and saved_boundary['separation']==off_separation and off.get('boot_boundaries_sha256')==digest(boundary_file) and off.get('scanout')==mod.scanout_summary(text) and off['scanout'].get('progress_observed') is True and off.get('benchmark_analysis_sha256')==digest(analysis_path) and analysis.get('assessment',{}).get('status')=='disabled' and analysis.get('image_sha256')==m['variants']['off']['boot_sha256'] and analysis.get('boot_raw_sha256')==hashlib.sha256(off_raw).hexdigest() and b'a04br3_mem_copy ' not in off_raw and b'a04br3: memory bench spawn ' not in off_raw and analysis.get('parser_commit')==PARSER_COMMIT,'off試験未完了')
 try: mod.run_locked()
 finally:
  result_file=stage/'result.json'
  if result_file.exists():
   result=json.loads(result_file.read_text())
   state=WORK/'recovery-state.json'
   if result.get('recovery_confirmed') is True and state.exists():
    journal=json.loads(state.read_text())
    if journal.get('state')=='armed' and journal.get('image_sha256')==mod.IMG_SHA:
     guardian.write_state(state,{**journal,'state':'restored','recovery':'flash-confirmed','recovery_boot':'unverified'})

def main(mode):
 # This lock is also used by integration/flash_candidate_once.py. Other writers
 # must cooperate with it; flock cannot exclude arbitrary fastboot processes.
 shared=ROOT/'port/gsi/logs/ios-feasibility/integration/flash-writer.lock'
 with shared.open('a') as device_lock, (WORK/'trial.lock').open('a') as trial_lock:
  fcntl.flock(trial_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  try: fcntl.flock(device_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:
   print('共有書込みロック待機中',flush=True)
   fcntl.flock(device_lock,fcntl.LOCK_EX)
  main_locked(mode)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=('off','on'));p.add_argument('--execute',action='store_true');a=p.parse_args()
 try:
  if a.execute:main(a.mode)
  else:check(a.mode);print('検証成功: 実機操作なし')
 except (OSError,ValueError,KeyError,RuntimeError) as e:raise SystemExit(str(e))
