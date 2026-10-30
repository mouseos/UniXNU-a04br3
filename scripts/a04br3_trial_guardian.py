#!/usr/bin/env python3
"""Persistent recovery journal and independent guardian; device I/O only in --watch mode."""
import argparse, fcntl, hashlib, json, os, re, subprocess, time
from pathlib import Path

SAFE_SHA='c35596ebe9a18f2dd1bd21d59c7231b8e6a36d2062c82e7d20cd6208e5ac09ea'
SERIAL='0C0U265083'

def flash_transcript(data):
    lines=data.split(b'\n')
    patterns=(rb"Sending 'boot' \(([1-9][0-9]*) KB\)[ \t]+OKAY \[[ \t]*[0-9]+\.[0-9]+s\]",rb"Writing 'boot'[ \t]+OKAY \[[ \t]*[0-9]+\.[0-9]+s\]",rb'Finished\. Total time: [0-9]+\.[0-9]+s')
    return len(lines)==4 and lines[-1]==b'' and all(re.fullmatch(p,line) for p,line in zip(patterns,lines[:3]))

def conflicts(argv):
    if not argv or not argv[0]:return False
    program=os.path.basename(os.fsdecode(argv[0]))
    if program not in ('fastboot','mtk','mtkclient'):return False
    if program=='fastboot' and argv[1:]==[b'devices']:return False
    if b'-s' in argv:
        i=argv.index(b'-s')
        if i+1<len(argv) and argv[i+1]!=SERIAL.encode():return False
    return True

def write_state(path, obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    with tmp.open('w') as f:
        json.dump(obj,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

def recover_if_abandoned(state_path, lock_path, restore):
    state_path=Path(state_path)
    if not state_path.exists():return 'idle'
    with Path(lock_path).open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 'active'
        state=json.loads(state_path.read_text())
        if state.get('state')=='restored':return 'restored'
        if state.get('state')!='armed':raise ValueError('unknown recovery journal state')
        outcome=restore(state)
        if outcome:
            write_state(state_path,{**state,'state':'restored','recovery':outcome if isinstance(outcome,str) else 'flash-confirmed','recovery_boot':'unverified'})
            return 'restored'
        return 'retry'

BOOT_WRITE=re.compile(rb"^(?:Writing 'boot'[ \t]+OKAY\b|Wrote .* to sector 294912 )",re.M)

def superseded(common,state,stage):
    """True when a boot write newer than the trial flash appears in the shared flash log.

    Every cooperating writer (this controller, its recovery, the guardian,
    flash_candidate_once.py and the fastboot helper scripts) appends its output
    to the shared log while holding flash-writer.lock. The trial flash is the
    first boot write after the recorded offset; the controller logs it to the
    stage log before the shared log. Any further boot write means the trial
    image is no longer on the device, so writing the recovery image would only
    overwrite another writer's image or duplicate a completed recovery."""
    offset=state.get('common_log_offset');inode=state.get('common_log_inode')
    if type(offset) is not int or type(inode) is not int:return False
    common=Path(common)
    if common.stat().st_ino!=inode:return False
    with common.open('rb') as f:
        f.seek(offset);data=f.read()
    try:trial=1 if BOOT_WRITE.search((stage/'flash.log').read_bytes()) else 0
    except FileNotFoundError:trial=0
    return len(BOOT_WRITE.findall(data))-trial>=1

def restore_device(root,state):
    safe=root/'port/xnu/out/iostat-ui-stack-stream-20261030/boot.img'
    with safe.open('rb') as f:
        if hashlib.file_digest(f,'sha256').hexdigest()!=SAFE_SHA:return False
    stage=Path(state['stage']).resolve()
    permitted=(root/'port/gsi/logs/ios-feasibility/memory-ce66352-trial').resolve()
    if stage.parent!=permitted or stage.name not in ('off','on'):return False
    if state.get('safe_sha256')!=SAFE_SHA or not isinstance(state.get('trial_id'),str) or len(state['trial_id'])!=32:return False
    # The controller may have published recovery evidence immediately before
    # dying and before closing its journal. Reconcile before attempting a write.
    try:
        started=json.loads((stage/'started.json').read_text())
        result=json.loads((stage/'result.json').read_text())
        statuses=[json.loads(x) for x in (stage/'fastboot-status.jsonl').read_text().splitlines()]
        done=[s for s in statuses if s.get('arguments')==['flash','boot',str(safe)] and s.get('status')=='completed' and s.get('returncode')==0]
        log=(stage/'recovery-flash.log').read_bytes()
        if (started.get('image_sha256')==state.get('image_sha256') and started.get('trial_id')==state['trial_id'] and result.get('trial_id')==state['trial_id'] and result.get('safe_sha256')==SAFE_SHA and result.get('recovery_log_sha256')==hashlib.sha256(log).hexdigest() and result.get('recovery_write_confirmed') is True and done and
            flash_transcript(log)):
            return True
    except (OSError,ValueError,KeyError):pass
    try:
        receipt=json.loads((stage/'guardian-recovery-receipt.json').read_text())
        log=(stage/'guardian-recovery.log').read_bytes()
        begin=receipt.get('start');end=receipt.get('end')
        if receipt.get('trial_id')==state['trial_id'] and receipt.get('safe_sha256')==SAFE_SHA and receipt.get('arguments')==['fastboot','-s',SERIAL,'flash','boot',str(safe)] and receipt.get('log_sha256')==hashlib.sha256(log).hexdigest() and receipt.get('returncode')==0 and type(begin) is int and type(end) is int and 0<=begin<end==len(log) and flash_transcript(log[begin:end]):
            return True
    except (OSError,ValueError,KeyError):pass
    try:
        if superseded(root/'port/gsi/logs/flash.log',state,stage):return 'superseded'
    except OSError:pass
    # An orphaned fastboot can survive SIGKILL of the controller. Never race it.
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():continue
        try:
            argv=(process/'cmdline').read_bytes().split(b'\0');command=argv[0]
        except FileNotFoundError:continue
        except OSError:return False
        if conflicts([x for x in argv if x]):return False
    def call(args):
        try:
            p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=300)
            output=p.stdout
        except subprocess.TimeoutExpired as e:
            output=(e.stdout or b'')+b'\ntimeout\n';p=None
        with (stage/'guardian-recovery.log').open('ab') as f, (root/'port/gsi/logs/flash.log').open('ab') as common:
            f.write(output);common.write(output);f.flush();os.fsync(f.fileno());common.flush();os.fsync(common.fileno())
        return p,output
    p,out=call(['fastboot','devices'])
    if p is None or p.returncode or [x.split()[0] for x in out.decode('utf8','replace').splitlines() if len(x.split())>=2 and x.split()[1]=='fastboot']!=[SERIAL]:return False
    p,out=call(['fastboot','-s',SERIAL,'flash','boot',str(safe)])
    if p is None or p.returncode:return False
    ok=flash_transcript(out)
    if ok:
        log=(stage/'guardian-recovery.log').read_bytes()
        write_state(stage/'guardian-recovery-receipt.json',{'trial_id':state['trial_id'],'safe_sha256':SAFE_SHA,'arguments':['fastboot','-s',SERIAL,'flash','boot',str(safe)],'returncode':p.returncode,'start':len(log)-len(out),'end':len(log),'log_sha256':hashlib.sha256(log).hexdigest()})
    return ok

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--watch',action='store_true');a=p.parse_args()
    if not a.watch:p.error('guardian is only activated by --watch')
    root=a.root.resolve();work=root/'port/gsi/logs/ios-feasibility/memory-ce66352-trial'
    lock=root/'port/gsi/logs/ios-feasibility/integration/flash-writer.lock'
    while True:
        try:recover_if_abandoned(work/'recovery-state.json',lock,lambda state:restore_device(root,state))
        except (OSError,ValueError,KeyError,subprocess.SubprocessError) as e:
            with (work/'guardian-errors.log').open('a') as f:f.write(type(e).__name__+': '+str(e)+'\n')
        time.sleep(15)
if __name__=='__main__':main()
