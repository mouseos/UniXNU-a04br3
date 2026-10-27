#!/usr/bin/env python3
"""Opt-in six-boot26/380/380/26/26/380 measurement using the existing unattended transport.
Default validates inputs and writes a plan only. --execute permits flash/reset.
An existing UART collector is mandatory and never stopped or replaced.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import shutil
import struct
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
ORDER=(26,380,380,26,26,380)
FAULT=re.compile(r'panic\(|Panicked|Kernel panic|Synchronous Exception at|context reset\s+[1-9]|HWR counter [1-9]|\bhwr\s+[1-9]|\bfaults [1-9]|MMU fault|powervr[^\n]*\bFAULT\b',re.I)

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def save(path,data): path.write_text(json.dumps(data,indent=2)+'\n')

def validate_inputs(a):
    reports={}
    for clock in (26,380):
        image=getattr(a,'boot'+str(clock));report=json.loads(getattr(a,'payload'+str(clock)).read_text())
        subprocess.run([sys.executable,str(ROOT/'scripts/check-a04br3-bootimg.py'),str(image)],check=True,timeout=60)
        raw=image.read_bytes();kernel_size=struct.unpack_from('<I',raw,8)[0];page=struct.unpack_from('<I',raw,36)[0]
        offset=page+((kernel_size+page-1)//page)*page
        length=struct.unpack_from('<I',raw,offset+4)[0]
        if hashlib.sha256(raw[offset+512:offset+512+length]).hexdigest()!=report['payload_sha256']:
            raise ValueError('boot embedded payload SHA differs from report')
        tokens=shlex.split(report['kernel_args']);freq=[t for t in tokens if t.startswith('a04br3-gpu-clock=')]
        if clock==380 and freq!=['a04br3-gpu-clock=380']:raise ValueError('380 clock arg missing')
        if clock==26 and freq not in ([],['a04br3-gpu-clock=0']):raise ValueError('26 clock arg invalid')
        if 'a04br3-powervrprobed=1' not in tokens:raise ValueError('probe disabled')
        reports[clock]=report
    for key in ('kernel_sha256','payload_kernel_sha256','data_ramdisk_sha256','gpu_addon_sha256','trust_cache_sha256'):
        if not reports[26].get(key) or reports[26][key]!=reports[380].get(key):raise ValueError('pair differs: '+key)
    clean=lambda d:[t for t in shlex.split(d['kernel_args']) if not t.startswith('a04br3-gpu-clock=')]
    if clean(reports[26])!=clean(reports[380]):raise ValueError('nonclock args differ')
    return {str(c):{'boot_sha256':sha(getattr(a,'boot'+str(c))),'payload_report_sha256':sha(getattr(a,'payload'+str(c))),'payload_report':reports[c]} for c in (26,380)}

SOURCE_IDS=('base','device','base_builder','device_builder','kernel_device','metal','uefi','loader')

RESET_ACK=re.compile(rb'a04br3-autoreset: (?:reset requested on UART|[0-9]+ s elapsed), resetting into fastboot')

def prior_tail(collector,record,measurement_raw,end,allow_following=False):
    start=record['epoch']['end_offset']
    if end<start:raise ValueError('prior log newer than collector snapshot; retry')
    collector.seek(start);raw=collector.read(min(end-start,32*1024*1024+1))
    reset=RESET_ACK.search(raw);previous_reset=RESET_ACK.search(measurement_raw)
    positions=[raw.find(token) for token in (b'A04BR3 SEC:',b'Darwin Kernel Version ') if token in raw]
    following=min(positions) if positions else None
    if following is not None and previous_reset is None and (reset is None or following<reset.end()):raise ValueError('unrequested prior reboot after saved snapshot')
    if following is not None and not allow_following:raise ValueError('unaccounted boot between prior epochs')
    relevant=raw[:following] if following is not None else raw
    if len(relevant)>32*1024*1024 or len(raw)<min(end-start,32*1024*1024+1):raise ValueError('prior tail exceeds bound or collector truncated')
    text=relevant.decode(errors='replace')
    if FAULT.search(text):raise ValueError('fault in prior tail after saved snapshot')
    runtime=b'' if previous_reset else (relevant[:reset.start()] if reset else relevant)
    if re.search(rb'Preloader Start|BOOT_REASON:',runtime):raise ValueError('prior reboot before intended reset')
    verdict=analyze(measurement_raw+runtime,record['clock_request'])
    if verdict['state']!='pass':raise ValueError('prior tail invalidates measurement: '+verdict.get('reason','failed'))
    tail={'start_offset':start,'scanned_end_offset':start+len(relevant),'collector_cutoff_offset':end,
          'sha256':hashlib.sha256(relevant).hexdigest(),'closed_by':'kernel reset acknowledgement' if reset or previous_reset else 'still running at observation',
          'reset_ack_offset':record['epoch']['start_offset']+previous_reset.start() if previous_reset else (start+reset.start() if reset else None),'following_boot_observed':following is not None,
          'following_boot_offset':start+following if following is not None else None}
    return tail,relevant


def refresh_last_prior(a,priors,snapshots,out=None,end=None):
    if not priors:return
    stat=a.uart_log.stat();last=priors[-1]
    if [stat.st_dev,stat.st_ino]!=last['prior_evidence']['verified_collector_identity']:raise ValueError('prior collector identity changed')
    with a.uart_log.open('rb') as source:
        tail,raw=prior_tail(source,last,snapshots[-1][1],stat.st_size if end is None else end,True)
    last['prior_tail']=tail
    if tail['following_boot_observed']:raise ValueError('unimported newer boot observed; import its flash-state/log before execute')
    if out is not None:
        folder=out/f'prior-{len(priors):02d}-{last["clock_request"]}'
        (folder/'tail.log').write_bytes(raw);save(folder/'result.json',last)


def import_priors(a,inputs):
    """Validate an ordered prefix from actual log bytes, not claimed PASS flags.
    Return immutable bytes too, so an actively growing P25 file cannot change
    between validation and evidence staging. All reads are host files only.
    """
    specs=getattr(a,'prior_epoch',[]) or []
    if len(specs)>len(ORDER):raise ValueError('too many prior epochs')
    records=[];snapshots=[];seen=set();previous_end=-1;common=None
    source_stat=a.uart_log.stat()
    with a.uart_log.open('rb') as collector:
        opened=os.fstat(collector.fileno())
        if (opened.st_dev,opened.st_ino)!=(source_stat.st_dev,source_stat.st_ino):
            raise ValueError('collector changed during prior validation')
        for index,(state_path,log_path) in enumerate(specs):
            state_path,log_path=Path(state_path),Path(log_path)
            with state_path.open('rb') as state_file:state_bytes=state_file.read(1024*1024+1)
            if len(state_bytes)>1024*1024:raise ValueError('prior state exceeds1MiB')
            state=json.loads(state_bytes)
            with log_path.open('rb') as log_file:raw=log_file.read(32*1024*1024+1)
            if not 0<len(raw)<=32*1024*1024:raise ValueError('prior log empty or exceeds32MiB')
            offset=state.get('uart_offset')
            if type(offset) is not int or offset<0:raise ValueError('invalid original UART offset')
            end=offset+len(raw)
            if offset in seen or offset<previous_end:raise ValueError('duplicate, overlapping or out-of-order prior epoch')
            collector.seek(offset)
            if collector.read(len(raw))!=raw:raise ValueError('prior log does not match collector at original offset')
            clock=ORDER[index];bound=inputs[str(clock)]
            if state.get('boot_sha256')!=bound['boot_sha256']:raise ValueError('prior boot SHA/clock does not match selected image')
            if state.get('kernel_sha256')!=bound['payload_report']['kernel_sha256']:raise ValueError('prior kernel differs')
            if state.get('boot_bytes')!=getattr(a,'boot'+str(clock)).stat().st_size:raise ValueError('prior boot size differs')
            ids={key:state.get(key) for key in SOURCE_IDS}
            if any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{40}',v) for v in ids.values()):raise ValueError('prior source IDs missing/invalid')
            if common is not None and ids!=common:raise ValueError('prior source IDs differ')
            common=ids
            verdict=analyze(raw,clock)
            if verdict['state']!='pass':raise ValueError(f'prior {state_path.name} not complete: '+verdict.get('reason','failed'))
            records.append({'clock_request':clock,'state':'pass','origin':'imported_host_evidence',
                'measurement':verdict,'source_ids':ids,'original_flash_state':state,
                'epoch':{'start_offset':offset,'end_offset':end,'flash_requested_utc':state.get('at'),
                         'uart_file':str(a.uart_log.resolve())},
                'prior_evidence':{'state_path':str(state_path.resolve()),'log_path':str(log_path.resolve()),
                                  'state_sha256':hashlib.sha256(state_bytes).hexdigest(),'log_sha256':hashlib.sha256(raw).hexdigest(),
                                  'verified_collector_identity':[source_stat.st_dev,source_stat.st_ino]},
                'recovery_status':'not inferred from imported measurement window','environment':{'temperature':'unobserved','power':'unobserved'}})
            snapshots.append((state_bytes,raw));seen.add(offset);previous_end=end
        for index,record in enumerate(records):
            end=records[index+1]['epoch']['start_offset'] if index+1<len(records) else source_stat.st_size
            tail,raw=prior_tail(collector,record,snapshots[index][1],end,index==len(records)-1)
            record['prior_tail']=tail;snapshots[index]=(*snapshots[index],raw)
        current=a.uart_log.stat()
        if (current.st_dev,current.st_ino)!=(source_stat.st_dev,source_stat.st_ino) or current.st_size<previous_end:
            raise ValueError('collector rotated/truncated during prior validation')
    return records,snapshots


def stage_priors(out,records,snapshots):
    for index,(record,(state,raw,tail)) in enumerate(zip(records,snapshots),1):
        folder=out/f'prior-{index:02d}-{record["clock_request"]}';folder.mkdir()
        (folder/'flash-state.json').write_bytes(state);(folder/'first-boot.log').write_bytes(raw);(folder/'tail.log').write_bytes(tail)
        save(folder/'result.json',record)


def analyze(raw,clock):
    # Accept raw collector bytes and the project's escaped saved UART transcripts.
    text=raw.decode('utf-8',errors='replace').replace('\\r','\r').replace('\\n','\n').replace('\r','\n')
    boots=list(re.finditer(r'Darwin Kernel Version ',text))
    if len(boots)>1:return {'state':'fail','reason':'multiple XNU boots in measurement epoch'}
    if not boots:return {'state':'pending','reason':'waiting for XNU boot marker'}
    prefix=text[:boots[0].start()]
    if prefix.count('A04BR3 SEC:')!=1:return {'state':'fail','reason':'expected exactly one fresh firmware epoch before XNU'}
    after_sec=prefix[prefix.index('A04BR3 SEC:'):]
    if re.search(r'Preloader Start|BOOT_REASON:',after_sec) or re.search(r'Synchronous Exception at|ASSERT \[|panic\(',prefix,re.I):
        return {'state':'fail','reason':'firmware fault/reboot before XNU'}
    text=text[boots[0].start():]
    if re.search(r'A04BR3 SEC:|Preloader Start|BOOT_REASON:',text):return {'state':'fail','reason':'reboot boundary before complete measurement'}
    fault=FAULT.search(text)
    if fault:return {'state':'fail','reason':'fault/panic/reset: '+fault.group(0)}
    if re.search(r'powervrprobed: FAIL',text):return {'state':'fail','reason':'probe FAIL'}
    if re.search(r'gpu scanout: [^\n]*(?:failed|abandoned)=[1-9]',text):return {'state':'fail','reason':'UI scanout failure'}
    # Fault/reboot detection above also sees an unfinished final UART line.
    # Success records below require complete lines.
    if not text.endswith('\n'):text=text.rsplit('\n',1)[0] if '\n' in text else ''
    active=re.findall(r'gpu clock: active parent (\d+) core (\d+) Hz',text)
    if not active:return {'state':'pending','reason':'waiting for active clock'}
    if len(set(active))!=1:return {'state':'fail','reason':'clock changed in epoch'}
    parent,hz=map(int,active[0])
    if (clock==26 and (parent,hz)!=(4,26000000)) or (clock==380 and (parent!=7 or not 379000000<=hz<=381000000)):
        return {'state':'fail','reason':'clock fallback or unexpected active rate'}
    starts=list(re.finditer(r'powervrprobed: start: pid (\d+)',text))
    if len(starts)>1:return {'state':'fail','reason':'multiple probe processes in epoch'}
    if not starts:return {'state':'pending','reason':'waiting for probe start'}
    probe=text[starts[0].start():]
    done=re.search(r'powervrprobed: done: (\d+) passed, (\d+) failed, status (\d+)',probe)
    if not done:return {'state':'pending','reason':'waiting for probe done'}
    if tuple(map(int,done.groups()))!=(77,0,0):return {'state':'fail','reason':'probe summary not77/0/status0'}
    probe=probe[:done.end()]
    allocations={int(i):int(size) for i,size in re.findall(r'buffer-rows allocation (\d) requested=\d+ actual=(\d+)',probe)}
    required=['rows submit: VkResult 0','rows wait: VkResult 0','vulkan stage passed',
              'rows=1280 width=3152 srcPitch=3328 dstPitch=3200 prefix=4096']
    if any(token not in probe for token in required) or set(allocations)!={0,1}:return {'state':'fail','reason':'incomplete rows GPU evidence'}
    checks={k:(int(n),int(bad)) for k,n,bad in re.findall(r'PASS vk (rows payload|rows all padding/prefix/allocation-tail|rows source unchanged): checked=(\d+) mismatches=(\d+)',probe)}
    expected={'rows payload':4034560,'rows all padding/prefix/allocation-tail':allocations[1]-4034560,'rows source unchanged':allocations[0]}
    if any(n<=0 or checks.get(k)!=(n,0) for k,n in expected.items()):return {'state':'fail','reason':'missing or inconsistent full allocation checks'}
    host=re.search(r'single-shot host-us: record=(\d+) submit=(\d+) wait=(\d+) validate=(\d+) clock=1',probe)
    cache=re.search(r'single-shot cache-us: flush=(\d+) invalidate=(\d+) clock=1',probe)
    if not host or not cache:return {'state':'fail','reason':'timing version/clock validity missing'}
    # Require one actual backboardd instance and one ordered copy-report block.
    images=re.findall(r'powervr-metal\[backboardd\]: IM main=',text)
    if len(images)>1:return {'state':'fail','reason':'backboardd restarted in epoch'}
    if not images:return {'state':'pending','reason':'waiting for backboardd process identity'}
    messages=re.findall(r'powervr-metal\[backboardd\]: ([^\n]+)',text)
    block={};complete=None
    patterns=[('CG',r'CG w800 h1280 a16 r2'),('CS',r'CS s3328 d3200'),
              ('CP',r'CP5 e(\d+) r1'),('CT',r'CT i(\d+) n(\d+) c(\d+) w(\d+)'),
              ('CE',r'CE p5 q(\d+) b(\d+) t(\d+)'),('CX',r'CX x(\d+) e(\d+)')]
    for message in messages:
        if message.startswith('CG '):block={}
        for index,(key,pattern) in enumerate(patterns):
            match=re.fullmatch(pattern,message.strip())
            if match and list(block)==[x[0] for x in patterns[:index]]:
                block[key]=[int(v) for v in match.groups()]
                if key=='CX':complete=dict(block)
    if not complete:return {'state':'pending','reason':'waiting for matched CP5/CT/CE/CX report'}
    pf=[re.fullmatch(r'PF (\d+)/(\d+) r(-?\d+) v(\d+)/(\d+) t(\d+) a(\d+)',message.strip()) for message in messages if message.startswith('PF ')]
    if any(not record for record in pf):return {'state':'fail','reason':'malformed PF result'}
    if any(int(record[2]) or int(record[3]) or int(record[5]) or not int(record[4]) for record in pf):
        return {'state':'fail','reason':'UI PF failure or unverified copy'}
    # PF is emitted by scanout, CP/CE/CX by status: either thread may print first.
    if not any(int(record[1])==1 for record in pf):return {'state':'pending','reason':'waiting for first verified UI PF'}
    elapsed=complete['CP'][0];intervals=complete['CT']
    if sum(intervals)>elapsed or sum(complete['CE']+complete['CX'])>intervals[1]+4:
        return {'state':'fail','reason':'inconsistent phase intervals'}
    return {'state':'pass','core_hz':hz,'probe_pid':int(starts[0].group(1)),
            'rows_host_us':dict(zip(('record','submit','wait','validate'),map(int,host.groups()))),
            'rows_cache_us':dict(zip(('flush','invalidate'),map(int,cache.groups()))),
            'UI_copy_ms':elapsed,'UI_intervals_ms':dict(zip(('import','encode','commit','wait'),intervals)),
            'UI_encode_ms':dict(zip(('command_buffer','blit_encoder','setup','copy_record','end_encoding'),complete['CE']+complete['CX']))}

class Transport:
    now=staticmethod(time.monotonic)
    sleep=staticmethod(time.sleep)
    def __init__(self,a):self.a=a
    def check_collector(self):
        pid=self.a.collector_pid
        os.kill(pid,0)
        expected=self.a.uart_log.stat()
        for fd in Path(f'/proc/{pid}/fd').iterdir():
            try:
                info=fd.stat()
                flags=int(re.search(r'^flags:\s+(\d+)',Path(f'/proc/{pid}/fdinfo/{fd.name}').read_text(),re.M)[1],8)
                if (info.st_dev,info.st_ino)==(expected.st_dev,expected.st_ino) and flags & os.O_ACCMODE in (os.O_WRONLY,os.O_RDWR):return
            except (OSError,TypeError):continue
        raise RuntimeError('collector PID does not hold UART log open for writing')
    def fastboot(self):
        result=subprocess.run(['fastboot','devices'],capture_output=True,text=True,timeout=10,check=True)
        return any(line.split() and line.split()[0]==self.a.serial for line in result.stdout.splitlines())
    def reset(self,folder):
        with (folder/'uart-reset.log').open('a') as log:
            subprocess.run(['bash',str(ROOT/'scripts/a04br3-reset.sh')],env=dict(os.environ,UART=self.a.uart),stdout=log,stderr=log,timeout=10,check=True)
    def start(self,image,folder,limit):
        env=dict(os.environ,SERIAL=self.a.serial,MODE='flash',ROUNDS='1',UART_LOG=str(self.a.uart_log.resolve()),RUNS_ROOT=str(folder/'transport'),DEVICE_WAIT='30',FB_TIMEOUT='120')
        for key in ('UART','USERDATA','NEXT_IMAGE_CMD'):env.pop(key,None)
        with (folder/'transport.log').open('w') as log:
            return subprocess.Popen(['bash',str(ROOT/'scripts/run-a04br3-unattended.sh'),str(image.resolve()),str(limit)],env=env,stdout=log,stderr=log,start_new_session=True)
    def stop(self,process):
        if process.poll() is None:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:return
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:pass


def kernel_ready(a,folder):
    try:
        epoch=json.loads((folder/'result.json').read_text())['epoch'];stat=a.uart_log.stat()
        if [stat.st_dev,stat.st_ino]!=epoch['identity']:return False
        with a.uart_log.open('rb') as stream:
            stream.seek(epoch['start_offset']);raw=stream.read(32*1024*1024)
        text=raw.decode(errors='replace').replace('\\r','\r').replace('\\n','\n')
        return 'a04br3-autoreset:' in text.rsplit('Darwin Kernel Version ',1)[-1]
    except (OSError,KeyError):return False


def recover(io,a,folder,process=None):
    request=folder/('initial-reset-request.json' if process is None else 'reset-request.json')
    end=io.now()+a.recovery_timeout;sent=request.exists()
    while io.now()<end:
        present=io.fastboot()
        if present and (process is None or process.poll() is not None):
            if process is not None and process.poll()!=0:raise RuntimeError('unattended transport failed: '+str(process.poll()))
            return
        # Do not reset a flash transfer. Wait for the new kernel listener, or
        # autonomous recovery, before sending at most one UART reset request.
        if not present and not sent and (process is None or kernel_ready(a,folder)):
            save(request,{'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                 'uart_offset':a.uart_log.stat().st_size,'method':'existing a04br3-reset.sh','cause':'host requested; not autonomous recovery proof'})
            io.reset(folder);sent=True
        io.sleep(2)
    raise TimeoutError('no observed fastboot/transport completion after reset')


def campaign(a,inputs,io,priors=(),prior_snapshots=()):
    result={'schedule':list(ORDER),'inputs':inputs,'rounds':list(priors),'remaining_schedule':list(ORDER[len(priors):]),'success':False}
    save(a.out/'campaign.json',result)
    for index,clock in enumerate(ORDER[len(priors):],len(priors)+1):
        folder=a.out/f'round-{index:02d}-{clock}';folder.mkdir()
        round_result={'clock_request':clock,'source_ids':priors[0]['source_ids'] if priors else {},'state':'incomplete','epoch':{},'reset_cause':'not inferred from fastboot return'}
        process=None;raw=bytearray()
        try:
            if index==len(priors)+1 and priors:refresh_last_prior(a,priors,prior_snapshots,a.out)
            io.check_collector()
            recover(io,a,folder)
            io.sleep(a.settle)
            io.check_collector()
            stat=a.uart_log.stat();offset=stat.st_size
            if index==len(priors)+1 and priors:refresh_last_prior(a,priors,prior_snapshots,a.out,offset)
            identity=(stat.st_dev,stat.st_ino)
            round_result['epoch']={'uart_file':str(a.uart_log.resolve()),'start_offset':offset,'identity':list(identity),'flash_requested_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
            round_result['environment']=json.loads(a.environment_json.read_text()) if a.environment_json else {'temperature':'unobserved','power':'unobserved','load':'UI+probe profile; no overlap inferred'}
            save(folder/'result.json',round_result)
            image=getattr(a,'boot'+str(clock))
            if sha(image)!=inputs[str(clock)]['boot_sha256']:raise ValueError('boot image changed before flash')
            process=io.start(image,folder,a.measurement_timeout+a.recovery_timeout+120)
            end=io.now()+a.measurement_timeout;first_pass=None;verdict={'state':'pending'}
            with a.uart_log.open('rb') as source,(folder/'measurement-uart.log').open('wb') as capture:
                source.seek(offset)
                while io.now()<end:
                    io.check_collector()
                    current=a.uart_log.stat()
                    if (current.st_dev,current.st_ino)!=identity or current.st_size<source.tell():raise RuntimeError('UART log rotated/truncated')
                    block=source.read(262144)
                    if block:raw.extend(block);capture.write(block);capture.flush()
                    if len(raw)>32*1024*1024:raise RuntimeError('UART epoch exceeds32MiB bound')
                    verdict=analyze(bytes(raw),clock)
                    if verdict['state']=='fail':raise RuntimeError(verdict['reason'])
                    if verdict['state']=='pass':
                        if first_pass is None:first_pass=io.now()
                        if io.now()-first_pass>=a.post_success:break
                    if process.poll() is not None:raise RuntimeError('transport ended before measurement completion')
                    io.sleep(1)
                else:raise TimeoutError('measurement timeout: '+verdict.get('reason','incomplete'))
                round_result['epoch']['end_offset']=source.tell()
            round_result['measurement']=verdict
            recover(io,a,folder,process)
            round_result['state']='pass'
        except (OSError,ValueError,RuntimeError,TimeoutError,subprocess.SubprocessError) as error:
            round_result.update(state='fail',error=str(error))
            round_result['epoch']['end_offset']=round_result['epoch'].get('start_offset',0)+len(raw)
            # Recovery is best-effort and never changes a failed measurement to PASS.
            if process is not None:
                try:recover(io,a,folder,process)
                except Exception as recovery:round_result['recovery_error']=str(recovery)
        finally:
            if process is not None:
                try:io.stop(process)
                except OSError as stop_error:round_result.update(state='fail',stop_error=str(stop_error))
            try:
                round_result['epoch']['recovery_end_offset']=a.uart_log.stat().st_size if a.uart_log.exists() else None
                epoch=round_result['epoch']
                if 'start_offset' in epoch:
                    stat=a.uart_log.stat()
                    if [stat.st_dev,stat.st_ino]==epoch['identity']:
                        with a.uart_log.open('rb') as stream:
                            stream.seek(epoch.get('end_offset',epoch['start_offset']));recovery=stream.read(32*1024*1024+1)
                        (folder/'recovery-uart.log').write_bytes(recovery)
                        recovery_text=recovery.decode(errors='replace')
                        round_result['recovery_boot_reasons']=re.findall(r'BOOT_REASON[:=]\s*(\d+)',recovery_text)
                        if len(recovery)>32*1024*1024 or FAULT.search(recovery_text):
                            round_result.update(state='fail',recovery_error='fault or overflow in recovery UART')
            except OSError as capture_error:
                round_result.update(state='fail',recovery_capture_error=str(capture_error))
            save(folder/'result.json',round_result);result['rounds'].append(round_result);save(a.out/'campaign.json',result)
        if round_result['state']!='pass':return 2
    result['summary']={}
    for clock in (26,380):
        samples=[r['measurement'] for r in result['rounds'] if r['clock_request']==clock]
        fields={'UI_encode_ms':[s['UI_intervals_ms']['encode'] for s in samples],
                'UI_commit_ms':[s['UI_intervals_ms']['commit'] for s in samples],
                'UI_wait_ms':[s['UI_intervals_ms']['wait'] for s in samples],
                'rows_record_us':[s['rows_host_us']['record'] for s in samples],
                'rows_submit_us':[s['rows_host_us']['submit'] for s in samples],
                'rows_wait_us':[s['rows_host_us']['wait'] for s in samples]}
        result['summary'][str(clock)]={k:{'n':len(v),'median':statistics.median(v),'min':min(v),'max':max(v)} for k,v in fields.items()}
    result['success']=True;save(a.out/'campaign.json',result);return 0

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('boot26','boot380','payload26','payload380','uart-log','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--prior-epoch',nargs=2,action='append',default=[],metavar=('FLASH_STATE','FIRST_BOOT_LOG'),
                   help='repeat for an ordered validated prefix; original --uart-log bytes at each state offset must match')
    p.add_argument('--collector-pid',type=int,help='existing process holding UART log open for writing; required for --execute on Linux')
    p.add_argument('--serial',required=True);p.add_argument('--uart',default='/dev/ttyUSB0')
    p.add_argument('--measurement-timeout',type=int,default=1800);p.add_argument('--recovery-timeout',type=int,default=180)
    p.add_argument('--settle',type=int,default=30);p.add_argument('--post-success',type=int,default=5)
    p.add_argument('--environment-json',type=Path,help='optional externally maintained temperature/power/load observations; copied per round')
    p.add_argument('--execute',action='store_true',help='explicitly permit existing runner flash and UART reset; absent=offline plan only')
    a=p.parse_args()
    if a.out.exists():p.error('output must be new; preserve incomplete campaigns')
    if min(a.measurement_timeout,a.recovery_timeout,a.post_success)<1 or a.settle<0:p.error('invalid time bounds')
    if not a.uart_log.is_file():p.error('existing collector log required')
    if a.environment_json:json.loads(a.environment_json.read_text())
    inputs=validate_inputs(a)
    priors,snapshots=import_priors(a,inputs)
    remaining=ORDER[len(priors):]
    if a.execute and remaining and priors:refresh_last_prior(a,priors,snapshots)
    if a.execute and remaining and (not a.collector_pid or a.collector_pid<1):p.error('--execute requires existing collector writer PID')
    if a.execute and remaining and (not Path(a.uart).is_char_device() or any(shutil.which(x) is None for x in ('fastboot','timeout','stty'))):
        p.error('execute requires UART character device and fastboot/timeout/stty')
    a.out.mkdir(parents=True);stage_priors(a.out,priors,snapshots)
    save(a.out/'plan.json',{'source_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
         'script_sha256':sha(Path(__file__)), 'transport_sha256':sha(ROOT/'scripts/run-a04br3-unattended.sh'),
         'reset_sha256':sha(ROOT/'scripts/a04br3-reset.sh'),'schedule':list(ORDER),
         'prior_epochs':priors,'remaining_schedule':list(remaining),
         'execute_blocked_by_following_boot':bool(priors and priors[-1]['prior_tail']['following_boot_observed']),'inputs':inputs,'execute':a.execute,'environment':'record same power/temperature/load externally; no thermal equivalence inferred'})
    if not a.execute:print('PLAN ONLY; no fastboot/UART operations');return 0
    return campaign(a,inputs,Transport(a),priors,snapshots)
if __name__=='__main__':sys.exit(main())
