#!/usr/bin/env python3
"""Summarize one verified post-reboot UART segment of a04br3 CPU load samples."""
import argparse
import hashlib
import json
import re
import sys

MARKER=b'a04br3 cpu-load '
ANCHOR=b'[pmic6392_init] Preloader Start'
BLDR=b'[BLDR] Build Time:'
REASON=re.compile(rb'(?i)\bBOOT_REASON\s*:')
PREFIX=re.compile(r'(?:\[[ \t]*[0-9]+(?:\.[0-9]+)?\][ \t]*)?\Z')
CPU=re.compile(r'a04br3 cpu-load cpu=(\d+) at_ns=(\d+) user=(\d+) system=(\d+) idle=(\d+) nice=(\d+) state_before=(\d+) state_after=(\d+) ticks32=1 non_atomic=1\Z')
END=re.compile(r'a04br3 cpu-load sample_begin_ns=(\d+) sample_end_ns=(\d+) cpus=(\d+) extra=(\d+) non_atomic=1\Z')
ONLINE={4,5,6}  # processor_state_t: IDLE, DISPATCHING, RUNNING
MOD=1<<32
MIN_NS=9_000_000_000
MAX_NS=12_000_000_000
MAX_TICKS_PER_SECOND=1000  # conservative rejection limit, not a measured tick rate

def sha(data): return hashlib.sha256(data).hexdigest()

def verify_inputs(capture, flash, source):
    data=open(capture,'rb').read()
    flash_data=open(flash,'rb').read()
    meta=json.loads(flash_data)
    reset=meta.get('uart_offset_before_reset'); write=meta.get('uart_offset_before_flash'); start=meta.get('uart_offset_before_reboot')
    if any(type(v) is not int or v < 0 for v in (reset,write,start)) or not reset <= write <= start:
        raise ValueError('invalid flash UART offsets')
    boot_hash=meta.get('manifest',{}).get('boot_sha256','')
    if not re.fullmatch(r'[0-9a-f]{64}',boot_hash):
        raise ValueError('flash manifest lacks a valid boot image SHA-256')
    h=hashlib.sha256()
    with open(source,'rb') as uart:
        remaining=start
        while remaining:
            chunk=uart.read(min(1024*1024,remaining))
            if not chunk: raise ValueError('UART source shorter than reboot offset')
            h.update(chunk);remaining-=len(chunk)
        raw=uart.read(len(data));h.update(raw)
    if not data or raw != data:
        raise ValueError('capture differs from UART source at reboot offset')
    anchor=data.find(ANCHOR);bldr=data.find(BLDR);first=data.find(MARKER)
    if anchor<0 or data.count(ANCHOR)!=1 or data.count(BLDR)!=1 or bldr<anchor or first<bldr:
        raise ValueError('missing or mixed boot boundaries')
    reasons=list(REASON.finditer(data))
    if not reasons or any(x.start()<anchor or x.start()>=first for x in reasons):
        raise ValueError('boot reason absent or appears after CPU sampling')
    identity={'parser_sha256':sha(open(__file__,'rb').read()),'source_prefix_sha256':h.hexdigest(),'source_prefix_end_offset':start+len(data),
              'capture_sha256':sha(data),'boot_segment_sha256':sha(data[anchor:]),
              'boot_start_offset_in_capture':anchor,'uart_reboot_offset':start,
              'flash_manifest_sha256':sha(flash_data),'boot_image_sha256':boot_hash,
              'capture_bytes':len(data)}
    return data[anchor:],identity

def uart_records(data):
    parts=data.split(b'\n')
    for index,raw in enumerate(parts):
        if MARKER not in raw: continue
        if index==len(parts)-1: raise ValueError('unterminated CPU record')
        if raw.endswith(b'\r'):raw=raw[:-1]
        if any((c<32 and c!=9) or c==127 for c in raw): raise ValueError('control character in CPU record')
        try: line=raw.decode('ascii')
        except UnicodeDecodeError as exc: raise ValueError('non-ASCII CPU record') from exc
        if line.count(MARKER.decode())!=1: raise ValueError('joined CPU records')
        pos=line.index(MARKER.decode())
        if not PREFIX.fullmatch(line[:pos]) or '\t' in line[pos:]: raise ValueError('invalid CPU record prefix or control')
        yield line[pos:]

def samples(data):
    pending=[]; result=[]; last_end=None
    for line in uart_records(data):
        cpu=CPU.fullmatch(line)
        if cpu:
            values=[int(x) for x in cpu.groups()]
            ident,at,*rest=values; ticks=rest[:4]; before,after=rest[4:]
            if ident != len(pending) or ident>3: raise ValueError('missing, duplicate or out-of-order CPU')
            if any(t>=MOD for t in ticks): raise ValueError('tick outside 32-bit range')
            if before not in ONLINE or after not in ONLINE: raise ValueError('CPU not online at both state reads')
            pending.append({'cpu':ident,'at_ns':at,'ticks':ticks,'state_before':before,'state_after':after})
            continue
        end=END.fullmatch(line)
        if not end: raise ValueError('malformed CPU load record')
        begin,finish,count,extra=map(int,end.groups())
        if len(pending)!=4 or count!=4 or extra!=0: raise ValueError('incomplete CPU sample')
        if finish<begin or (last_end is not None and begin<last_end): raise ValueError('overlapping sample timestamps')
        ats=[r['at_ns'] for r in pending]
        if any(a>=b for a,b in zip(ats,ats[1:])) or any(t<begin or t>finish for t in ats): raise ValueError('CPU timestamps outside sample or out of order')
        result.append({'begin_ns':begin,'end_ns':finish,'cpus':pending});pending=[];last_end=finish
    if pending: raise ValueError('incomplete CPU sample at EOF')
    if len(result)<2: raise ValueError('at least two complete CPU samples required')
    return result

def summarize(frames,identity):
    output=[]
    for previous,current in zip(frames,frames[1:]):
        cores=[]
        for old,new in zip(previous['cpus'],current['cpus']):
            duration=new['at_ns']-old['at_ns']
            if not MIN_NS<=duration<=MAX_NS: raise ValueError('CPU interval outside 9–12 seconds')
            delta=[(b-a)%MOD for a,b in zip(old['ticks'],new['ticks'])]
            total=sum(delta)
            if not total or total > (duration*MAX_TICKS_PER_SECOND//1_000_000_000)+1:
                raise ValueError('zero or implausible tick delta; reset or multiple wraps cannot be excluded')
            nonidle=delta[0]+delta[1]+delta[3];idle=delta[2]
            cores.append({'cpu':new['cpu'],'from_ns':old['at_ns'],'to_ns':new['at_ns'],'duration_ns':duration,
                          'non_idle_ticks':nonidle,'idle_ticks':idle,'non_idle_percent':100*nonidle/total,
                          'start_state_before':old['state_before'],'start_state_after':old['state_after'],
                          'end_state_before':new['state_before'],'end_state_after':new['state_after'],
                          'wrapped_components':sum(b<a for a,b in zip(old['ticks'],new['ticks']))})
        total_idle=sum(x['idle_ticks'] for x in cores);total_active=sum(x['non_idle_ticks'] for x in cores)
        output.append({**identity,'previous_sample_begin_ns':previous['begin_ns'],'previous_sample_end_ns':previous['end_ns'],
                       'sample_begin_ns':current['begin_ns'],'sample_end_ns':current['end_ns'],
                       'cores':cores,'combined_non_idle_percent':100*total_active/(total_active+total_idle),
                       'combined_definition':'tick-weighted four-core non-idle; separate reads, not instantaneous',
                       'limitations':'online state endpoints only; online-state changes allowed; intervening offline/hotplug unknown; single-wrap assumption; user accounting may understate non-idle'})
    return output

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',required=True,help='first-boot UART capture')
    p.add_argument('--flash-json',required=True,help='flash metadata containing reboot offset')
    p.add_argument('--uart-source',required=True,help='original UART stream used to create capture')
    args=p.parse_args()
    data,identity=verify_inputs(args.capture,args.flash_json,args.uart_source)
    results=summarize(samples(data),identity)
    rendered=[json.dumps(r,separators=(',',':'),allow_nan=False) for r in results]
    sys.stdout.write('\n'.join(rendered)+'\n')

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,TypeError,json.JSONDecodeError,OverflowError) as exc:
        print(f'cpu load: {exc}',file=sys.stderr);sys.exit(2)
