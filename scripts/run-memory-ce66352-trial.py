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
LAST=ROOT/'port/gsi/logs/ios-feasibility/msdc-adjacency-trial-20261030/adjacency'
REVIEW=WORK/'independent-review.json'
DEVICE_COMMIT='ce66352db798560ac8c4f380d66bf78876d6c7e2'
PARSER_COMMIT='ed98bfa1c429e26de66bc42b88859eb923dbda98'
SUPPORT_COMMIT='e9c39f2a4bf147ea1dfafb3a91f307bef6e2536f'
REPO=ROOT/'unixnu/UniXNU-a04br3-memory-low-impact'
PARSER='scripts/summarize-memory-observation.py'
DEVICE_FILES=('tools/a04br3-memory-copy-bench.c','package/A04BR3ProbeLaunch.c',
              'package/A04BR3Board.cpp','scripts/build-a04br3-memory-bench.py')
GUARDIAN_UNIT='a04br3-ce66352-recovery.service'
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
 result=json.loads((LAST/'result.json').read_text());need(result.get('boot_capture_valid') is True and result.get('recovery_confirmed') is True,'先行復旧未確認')
 need(digest(LAST/'recovery-flash.log')=='039a6b142799a91d26246cad705dafcbe3369a15b74dcc0ad573363f46ff6014','先行復旧ログ不一致')
 review=json.loads(REVIEW.read_text()); evidence=(WORK/'independent-review.txt').read_bytes()
 need(review.get('status')=='reviewed_for_diagnostic' and review.get('blocking_findings')==[] and review.get('reviewer') and hashlib.sha256(evidence).hexdigest()==review.get('evidence_sha256'),'当該画像の独立レビュー未完了')
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
   cancel=kwargs.get('cancel')
   if cancel is not None: cancel.check()
   with (stage/'flash.log').open('ab'), (ROOT/'port/gsi/logs/flash.log').open('ab'): pass
   trial_id=uuid.uuid4().hex
   started_path=stage/'started.json';started=json.loads(started_path.read_text());started['trial_id']=trial_id
   guardian.write_state(started_path,started)
   guardian.write_state(WORK/'recovery-state.json',{'state':'armed','stage':str(stage.resolve()),'image_sha256':mod.IMG_SHA,'safe_sha256':mod.SAFE_SHA,'trial_id':trial_id})
  return old_fastboot(arguments,*args,**kwargs)
 mod.logged_fastboot=fastboot_guard
 def capture_with_bench(cap,raw,args,expected,record_sha,raw_sha,snapshot):
  data=(stage/'capture-final/boot-1.raw').read_bytes()
  need(hashlib.sha256(data).hexdigest()==raw_sha,'bench対象起動ログ不一致')
  parser.capture_evidence(data,expected,stage/'capture-final/capture.json',stage/'flash.json',stage/'capture-final/post-flash.raw',stage/'flash.log')
  flash=json.loads((stage/'flash.json').read_text())
  separation=boundary.classify(snapshot,cap,flash)
  final_state=boundary.service_snapshot(UART,'a04br3-uart-main-20261030.service',subprocess.check_output)
  continuity=boundary.continuity(uart_start,final_state)
  report={'separation':separation,'uart_continuity':continuity}
  tmp=stage/'boot-boundaries.json.tmp';tmp.write_text(json.dumps(report,indent=2)+'\n');tmp.replace(stage/'boot-boundaries.json')
  reasons=separation['boots'][0]['events']['boot_reasons'];next_reasons=separation['boots'][0]['next_boot_reasons']
  lines=[line.split('boot-args = ',1)[1].strip() for line in raw.splitlines() if 'boot-args = ' in line]
  need(separation['trial_exception_free'] and reasons==[4] and next_reasons==[4] and 'mount-complete volume OwnerData' in raw and 'a04br3-autoreset: 720 s elapsed, resetting into fastboot' in raw and lines and all(line==args for line in lines),'起動または例外の証拠不正')
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
  analysis.update(parser_commit=PARSER_COMMIT,parser_sha256=hashlib.sha256(parser_source).hexdigest(),boot_raw_sha256=raw_sha,image_sha256=expected)
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
 (stage/'independent-image-review.txt').write_bytes((WORK/'independent-review.txt').read_bytes())
 if mode=='on':
  off_dir=WORK/'off';off=json.loads((off_dir/'result.json').read_text());analysis_path=off_dir/'benchmark-analysis.json'
  analysis=json.loads(analysis_path.read_text())
  off_raw=(off_dir/'capture-final/boot-1.raw').read_bytes()
  off_cap=json.loads((off_dir/'capture-final/capture.json').read_text())
  off_flash=json.loads((off_dir/'flash.json').read_text())
  parser.capture_evidence(off_raw,m['variants']['off']['boot_sha256'],off_dir/'capture-final/capture.json',off_dir/'flash.json',off_dir/'capture-final/post-flash.raw',off_dir/'flash.log')
  off_separation=boundary.classify((off_dir/'capture-final/post-flash.raw').read_bytes(),off_cap,off_flash)
  boundary_file=off_dir/'boot-boundaries.json';saved_boundary=json.loads(boundary_file.read_text())
  boundary.continuity(saved_boundary['uart_continuity']['before'],saved_boundary['uart_continuity']['after'])
  text=off_raw.decode('utf8','replace');off_args=[line.split('boot-args = ',1)[1].strip() for line in text.splitlines() if 'boot-args = ' in line]
  need(off.get('boot_capture_valid') is True and off.get('recovery_confirmed') is True and mod.flash_ok((off_dir/'recovery-flash.log').read_bytes(),0) and off_separation['trial_exception_free'] and off_separation['boots'][0]['events']['boot_reasons']==[4] and off_separation['boots'][0]['next_boot_reasons']==[4] and 'mount-complete volume OwnerData' in text and 'a04br3-autoreset: 720 s elapsed, resetting into fastboot' in text and off_args and all(a==m['variants']['off']['kernel_args'] for a in off_args) and saved_boundary['separation']==off_separation and off.get('boot_boundaries_sha256')==digest(boundary_file) and off.get('scanout')==mod.scanout_summary(text) and off['scanout'].get('progress_observed') is True and off.get('benchmark_analysis_sha256')==digest(analysis_path) and analysis.get('assessment',{}).get('status')=='disabled' and analysis.get('image_sha256')==m['variants']['off']['boot_sha256'] and analysis.get('boot_raw_sha256')==hashlib.sha256(off_raw).hexdigest() and b'a04br3_mem_copy ' not in off_raw and b'a04br3: memory bench spawn ' not in off_raw and analysis.get('parser_commit')==PARSER_COMMIT,'off試験未完了')
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
  fcntl.flock(device_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  fcntl.flock(trial_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  main_locked(mode)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=('off','on'));p.add_argument('--execute',action='store_true');a=p.parse_args()
 try:
  if a.execute:main(a.mode)
  else:check(a.mode);print('検証成功: 実機操作なし')
 except (OSError,ValueError,KeyError,RuntimeError) as e:raise SystemExit(str(e))
