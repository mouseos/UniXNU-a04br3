#!/usr/bin/env python3
"""Observe parent-written flash records and an existing UART file; never operate devices."""
import argparse
import base64
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result

C=module('read_batch_capture',HERE/'collect-read-batch-uart.py')
S=module('read_batch_iostat',HERE/'summarize-msdc-io.py')
FRAME=re.compile(rb'a04br3 msdc io end seq=[0-9]{1,20} rows=[0-9]{1,4}\r?\n')
SCAN_CHUNK=1024*1024
SCAN_BUDGET=8*1024*1024
CARRY=256
MAX_UART=128*1024*1024


def sha(data):return hashlib.sha256(data).hexdigest()


def file_sha(path):
    h=hashlib.sha256()
    with C.open_regular(path) as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def load(path):return C.strict_json(C.read_regular(path))


def atomic_json(path,value):
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(value,f,indent=2,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def truncated(value):
    if isinstance(value,dict):return value.get('truncated') is True or any(truncated(v) for v in value.values())
    if isinstance(value,list):return any(truncated(v) for v in value)
    return False


def event(out,state,run,phase,detail=None):
    if run['phase']==phase and run.get('detail')==detail:return
    run['phase']=phase;run['detail']=detail
    line={'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'id':run['id'],'phase':phase,'detail':detail}
    with (out/'events.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps(line,sort_keys=True)+'\n');f.flush()
    atomic_json(out/'state.json',state)


def validate_plan(plan, manifest):
    if plan.get('schema')!=1 or not isinstance(plan.get('runs'),list) or len(plan['runs'])!=2:raise ValueError('plan must specify exactly one baseline and one batch')
    if [r.get('variant') for r in plan['runs']] not in (['baseline','batch'],['batch','baseline']):raise ValueError('one of each variant required')
    if (manifest.get('measurement')!='iostat' or manifest.get('source_commit')!='e8f5385e4cee02bb15efa2fb97c20bb59b98d72f' or
        manifest.get('device_commit')!='8fde8ba87486338342f3be528823cb46394f2c5b' or manifest.get('uefi_commit')!='d3d4f28a89b29553ede7d7523c6af86d7e2459e8'):
        raise ValueError('unexpected comparison manifest')
    args={v:manifest['variants'][v]['kernel_args'] for v in ('baseline','batch')}
    if args['batch']!=args['baseline']+' native-cs-read-batch=1' or any('native-msdc-iostat=1' not in x.split() or 'native-root-timing=1' in x.split() or len(x.encode('ascii'))>1023 for x in args.values()):raise ValueError('unexpected measurement profile')
    seen=set()
    for entry in plan['runs']:
        name=entry.get('id');variant=entry['variant']
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',name) or name in seen:raise ValueError('invalid or duplicate run id')
        seen.add(name)
        for k in ('flash_record','image'):
            if not isinstance(entry.get(k),str) or not Path(entry[k]).is_absolute():raise ValueError('absolute image and flash record paths required')
        if file_sha(Path(entry['image']))!=manifest['variants'][variant]['boot_sha256']:raise ValueError('image digest mismatch')
    if plan['runs'][0]['flash_record']==plan['runs'][1]['flash_record']:raise ValueError('flash records must differ')
    if not isinstance(plan.get('uart'),str) or not Path(plan['uart']).is_absolute():raise ValueError('absolute UART file path required')


def init(out,plan,manifest,plan_digest,manifest_digest):
    runs=[{'id':x['id'],'variant':x['variant'],'phase':'awaiting_flash_record','detail':None,
           'cursor':None,'carry':'','anchors':0,'second_anchor':None,'frames':0,'second_reason':False,
           'flash_sha256':None,'checkpoint_sha256':None,'open_saved':False} for x in plan['runs']]
    state={'schema':1,'plan_sha256':plan_digest,'manifest_sha256':manifest_digest,
           'observer_sha256':file_sha(Path(__file__)),'collector_sha256':file_sha(HERE/'collect-read-batch-uart.py'),
           'summarizer_sha256':file_sha(HERE/'summarize-msdc-io.py'),'runs':runs,'phase':'waiting'}
    atomic_json(out/'state.json',state)
    with (out/'events.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps({'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'phase':'waiting_for_parent_flash_records'})+'\n')
    return state


def setup_run(out,state,run,entry,manifest):
    path=Path(entry['flash_record'])
    if not path.exists():return None
    data=C.read_regular(path);flash=C.strict_json(data)
    if (flash.get('boot_sha256')!=manifest['variants'][run['variant']]['boot_sha256'] or
        flash.get('flash_success') is not True or not isinstance(flash.get('flash_success_evidence'),str) or not flash['flash_success_evidence'].strip() or
        type(flash.get('uart_offset')) is not int or type(flash.get('flash_completed_uart_offset')) is not int or
        flash['flash_completed_uart_offset']<flash['uart_offset'] or not isinstance(flash.get('uart_checkpoint'),str)):
        raise ValueError('flash record incomplete or unexpected image')
    cp=Path(flash['uart_checkpoint']);cp=cp if cp.is_absolute() else path.parent/cp
    cp_data=C.read_regular(cp);checkpoint=C.strict_json(cp_data)
    if checkpoint.get('uart_offset')!=flash['uart_offset']:raise ValueError('flash/checkpoint offset mismatch')
    run.update(cursor=flash['uart_offset'],flash_sha256=sha(data),checkpoint_sha256=sha(cp_data))
    event(out,state,run,'waiting_for_first_boot')
    return flash


def scan(plan,run,flash,entry):
    flash_path=Path(entry['flash_record']); cp=Path(flash['uart_checkpoint']);cp=cp if cp.is_absolute() else flash_path.parent/cp
    if sha(C.read_regular(flash_path))!=run['flash_sha256'] or sha(C.read_regular(cp))!=run['checkpoint_sha256']:raise ValueError('flash or checkpoint changed after observation began')
    checkpoint=load(cp);uart=Path(plan['uart'])
    with C.open_regular(uart) as f:
        st=os.fstat(f.fileno());named=uart.stat()
        if (st.st_dev,st.st_ino)!=(checkpoint.get('source_dev'),checkpoint.get('source_inode')) or (st.st_dev,st.st_ino)!=(named.st_dev,named.st_ino):raise ValueError('UART rotated or checkpoint identity mismatch')
        if st.st_size<run['cursor'] or st.st_size-flash['uart_offset']>MAX_UART:raise ValueError('UART truncated or snapshot limit exceeded')
        f.seek(run['cursor']);remaining=min(st.st_size-run['cursor'],SCAN_BUDGET);carry=base64.b64decode(run['carry'],validate=True)
        while remaining:
            data=f.read(min(remaining,SCAN_CHUNK))
            if not data:raise ValueError('UART shortened while scanning')
            combined=carry+data;base=run['cursor']-len(carry)
            for m in re.finditer(re.escape(C.ANCHOR),combined):
                if m.end()>len(carry):
                    run['anchors']+=1
                    if run['anchors']==2:run['second_anchor']=base+m.start()
            for m in FRAME.finditer(combined):
                if m.end()>len(carry):run['frames']+=1
            if run['second_anchor'] is not None:
                for m in C.REASON.finditer(combined):
                    if m.end()>len(carry) and base+m.start()>=run['second_anchor']:run['second_reason']=True
            run['cursor']+=len(data);remaining-=len(data);carry=combined[-CARRY:]
        run['carry']=base64.b64encode(carry).decode('ascii')
        after=os.fstat(f.fileno());named=uart.stat()
        if after.st_size<st.st_size or (named.st_dev,named.st_ino)!=(st.st_dev,st.st_ino) or named.st_size<st.st_size:raise ValueError('UART changed incompatibly while scanning')


def capture(out,state,run,entry,manifest,plan,label):
    folder=out/run['id'];folder.mkdir(exist_ok=True)
    target=folder/('capture-'+label)
    result_path=folder/('result-'+label+'.json')
    if result_path.exists():
        result=load(result_path)
        if (result.get('capture')!=str(target) or result.get('flash_record_sha256')!=run['flash_sha256'] or
            result.get('capture_sha256')!=file_sha(target/'capture.json')):
            raise ValueError('existing result does not match capture or flash record')
        if result.get('iostat') and file_sha(Path(result['iostat']['path']))!=result['iostat']['sha256']:
            raise ValueError('existing iostat result changed')
        return result
    if target.exists() or (folder/('iostat-'+label+'.jsonl')).exists():
        raise ValueError('interrupted capture retained; use a fresh output directory after inspection')
    meta=C.collect(Path(plan['uart']),Path(entry['flash_record']),target)
    if meta['flash_record_sha256']!=run['flash_sha256'] or meta.get('checkpoint_sha256')!=run['checkpoint_sha256']:raise ValueError('capture flash/checkpoint changed during snapshot')
    boots=meta['boots'];reasons=[];result={'capture':str(target),'capture_sha256':file_sha(target/'capture.json'),
        'snapshot_sha256':meta['snapshot_sha256'],'flash_record_sha256':meta['flash_record_sha256'],
        'state':'incomplete','reasons':reasons,'first_boot':None,'following_boot':None,'iostat':None,
        'limits':['Global physical transfer calls, not per-process or signature attribution.',
                  'Counters are non-atomic; UART completeness and cache state require outside evidence.',
                  'No sample before first or after last frame; no automatic speed or security conclusion.']}
    if not boots:reasons.append('first_boot_not_observed')
    else:
        first=boots[0];data=(target/'boot-1.raw').read_bytes()
        if sha(data)!=first['sha256']:raise ValueError('boot capture digest mismatch')
        result['first_boot']={k:first.get(k) for k in ('start_offset','end_offset','closed_by_next_preloader','boot_reasons','boot_reason_markers','panics','el3_candidates','sha256')}
        if not first['closed_by_next_preloader']:reasons.append('first_boot_open')
        if first.get('panics'):reasons.append('first_boot_panic_candidate')
        if first.get('el3_candidates'):reasons.append('first_boot_el3_candidate')
        if first.get('boot_reason_markers')!=len(first.get('boot_reasons',[])) or not first.get('boot_reasons'):reasons.append('first_boot_reason_missing_or_unparsed')
        if len({x['value'] for x in first.get('boot_reasons',[]) if 'value' in x})>1:reasons.append('first_boot_reason_conflict')
        arg_lines=[line.split(b'boot-args =',1)[1].strip().decode('ascii','replace') for line in data.split(b'\n') if b'boot-args =' in line]
        if len(arg_lines)!=1 or arg_lines[0]!=manifest['variants'][run['variant']]['kernel_args']:reasons.append('boot_arguments_missing_or_mismatch')
        if len(boots)>1:
            later=boots[1];result['following_boot']={k:later.get(k) for k in ('start_offset','boot_reasons','boot_reason_markers','panics','el3_candidates','closed_by_next_preloader')}
            if later.get('panics'):reasons.append('following_boot_panic_candidate')
            if later.get('el3_candidates'):reasons.append('following_boot_el3_candidate')
            if later.get('boot_reason_markers')!=len(later.get('boot_reasons',[])) or not later.get('boot_reasons'):reasons.append('following_boot_reason_missing_or_unparsed')
            if any(x.get('value')==9 for x in later.get('boot_reasons',[])):reasons.append('following_watchdog_reason_observed')
            if len({x['value'] for x in later.get('boot_reasons',[]) if 'value' in x})>1:reasons.append('following_boot_reason_conflict')
        else:reasons.append('following_boot_not_observed')
        try:
            h=sha(data);segment=S.verify_source(data,h,0,h);rows=S.summarize(segment,h,h,0)
            target_rows=folder/('iostat-'+label+'.jsonl')
            with target_rows.open('x',encoding='utf-8') as f:
                for row in rows:f.write(json.dumps(row,sort_keys=True)+'\n')
            transfers=[x for x in rows if x['kind']=='transfer' and x['mode'] in ('dma_read','pio_read')]
            result['iostat']={'path':str(target_rows),'sha256':file_sha(target_rows),'intervals':len(rows)//16,
                'interval_begin_ns':rows[0]['begin_ns'],'interval_end_ns':rows[-1]['end_ns'],
                'successful_read_bytes':sum(x['ok_bytes'] for x in transfers),'read_calls':sum(x['calls'] for x in transfers),
                'read_errors':sum(x['errors'] for x in transfers),'summed_transfer_ns':sum(x['transport_ns'] for x in transfers)}
        except (ValueError,OSError,KeyError,TypeError) as e:reasons.append('iostat_unavailable: '+str(e))
    if not meta.get('checkpoint_verified'):reasons.append('checkpoint_unverified')
    if not meta.get('first_anchor_after_flash'):reasons.append('flash_to_boot_order_unverified')
    if meta.get('offset_crosses_preloader_anchor'):reasons.append('flash_offset_crosses_preloader')
    if meta.get('boots_truncated') or truncated(meta):reasons.append('capture_truncated')
    completed=meta.get('flash_completed_uart_offset')
    if type(completed) is int:
        prefix=meta.get('pre_preloader',{})
        if any(x.get('absolute_offset',-1)>=completed for field in ('panics','el3_candidates') for x in prefix.get(field,[])):reasons.append('post_flash_prefix_crash_unattributed')
    result['state']='recorded_conditions_unverified' if not reasons else 'incomplete_or_excluded'
    atomic_json(result_path,result)
    return result


def step(out,state,plan,manifest):
    for run,entry in zip(state['runs'],plan['runs']):
        if run['phase'] in ('finished','failed'):continue
        try:
            if run['cursor'] is None:
                flash=setup_run(out,state,run,entry,manifest)
                if flash is None:continue
            else:flash=load(Path(entry['flash_record']))
            scan(plan,run,flash,entry)
            atomic_json(out/'state.json',state)
            if run['anchors']>=1 and run['frames']>=2 and not run['open_saved']:
                capture(out,state,run,entry,manifest,plan,'open');run['open_saved']=True
                event(out,state,run,'first_boot_open_snapshot_saved')
            if run['anchors']>=2 and run['second_reason']:
                result=capture(out,state,run,entry,manifest,plan,'final');run['result']=result
                event(out,state,run,'finished',result['state'])
        except (ValueError,OSError,KeyError,TypeError,AttributeError) as e:
            event(out,state,run,'failed',str(e))
    if all(r['phase'] in ('finished','failed') for r in state['runs']):
        report={'schema':1,'plan_sha256':state['plan_sha256'],'manifest_sha256':state['manifest_sha256'],
                'runs':[{**{k:r.get(k) for k in ('id','variant','phase','detail','result')},'conditions':entry.get('conditions',{})} for r,entry in zip(state['runs'],plan['runs'])],
                'comparison':'exploratory_only_no_automatic_winner',
                'limits':['Physical transfer counters are global and intervals are not guaranteed to cover the same workload.',
                          'Run order, cache, persistent Data, GPU/CPU load, GPIO and UART completeness need external evidence.',
                          'Two runs do not satisfy the three-pair comparison criterion; later watchdog may be deliberate or a failure.']}
        atomic_json(out/'comparison.json',report);state['phase']='complete';atomic_json(out/'state.json',state)
    return state['phase']=='complete'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--watch',action='store_true');p.add_argument('--poll-seconds',type=int,default=60);p.add_argument('--timeout-seconds',type=int,default=7200);a=p.parse_args()
    if a.poll_seconds<15 or a.timeout_seconds<=0:raise ValueError('poll must be >=15 seconds and timeout positive')
    plan_bytes=C.read_regular(a.plan);plan=C.strict_json(plan_bytes)
    manifest_path=Path(plan['manifest']);manifest_bytes=C.read_regular(manifest_path);manifest=C.strict_json(manifest_bytes)
    if sha(manifest_bytes)!=plan.get('manifest_sha256'):raise ValueError('plan manifest digest mismatch')
    validate_plan(plan,manifest)
    a.out.mkdir(parents=True,exist_ok=True)
    with (a.out/'observer.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state_path=a.out/'state.json'
        state=load(state_path) if state_path.exists() else init(a.out,plan,manifest,sha(plan_bytes),sha(manifest_bytes))
        if state['plan_sha256']!=sha(plan_bytes) or state['manifest_sha256']!=sha(manifest_bytes) or state['observer_sha256']!=file_sha(Path(__file__)) or state['collector_sha256']!=file_sha(HERE/'collect-read-batch-uart.py') or state['summarizer_sha256']!=file_sha(HERE/'summarize-msdc-io.py'):
            raise ValueError('plan, manifest or scripts changed; use a fresh output directory')
        if state['phase']=='complete':return
        if state['phase']=='timed_out':
            state['phase']='waiting';atomic_json(state_path,state)
        deadline=time.monotonic()+a.timeout_seconds
        while True:
            if step(a.out,state,plan,manifest):return
            if not a.watch:return
            if time.monotonic()>=deadline:
                for run,entry in zip(state['runs'],plan['runs']):
                    if run['cursor'] is not None and run['phase'] not in ('finished','failed') and not run.get('timeout_saved'):
                        try:
                            run['timeout_result']=capture(a.out,state,run,entry,manifest,plan,'timeout');run['timeout_saved']=True
                        except (ValueError,OSError,KeyError,TypeError,AttributeError) as error:
                            event(a.out,state,run,'failed','timeout capture: '+str(error))
                state['phase']='timed_out';atomic_json(state_path,state)
                with (a.out/'events.jsonl').open('a') as f:f.write(json.dumps({'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'phase':'timed_out','detail':'partial evidence retained; safe to resume'})+'\n')
                return
            time.sleep(min(a.poll_seconds,max(0,deadline-time.monotonic())))

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as error:print('iostat observer:',error,file=sys.stderr);sys.exit(2)
