#!/usr/bin/env python3
"""Snapshot an existing UART file from a recorded flash offset; no device access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

ANCHOR = b'[pmic6392_init] Preloader Start'
REASON = re.compile(rb'(?i)\bBOOT_REASON[ \t]*[:=][ \t]*(0[xX][0-9a-fA-F]+|\d+)(?![0-9A-Za-z])')
REASON_MARK = re.compile(rb'(?i)\bBOOT_REASON\s*[:=]')
PANIC = re.compile(rb'(?i)(?:panic\s*\(|kernel panic)')
EL3 = re.compile(rb'(?i)(?:EL3[^\r\n]{0,60}exception|exception[^\r\n]{0,60}EL3)')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def event_lines(data, pattern, absolute_start):
    return [{'offset': m.start(), 'absolute_offset':absolute_start+m.start(), 'line': data[data.rfind(b'\n', 0, m.start())+1:
             (data.find(b'\n', m.start()) if data.find(b'\n', m.start())>=0 else len(data))][:512].decode('ascii','replace')}
            for m in pattern.finditer(data)]


def checkpoint(uart, output):
    if output.exists():raise ValueError('refusing existing checkpoint')
    with uart.open('rb') as stream:
        st=os.fstat(stream.fileno()); start=max(0,st.st_size-4096)
        stream.seek(start);tail=stream.read(st.st_size-start);after=os.fstat(stream.fileno());named=uart.stat()
    if len(tail)!=st.st_size-start or after.st_size<st.st_size or (named.st_dev,named.st_ino)!=(st.st_dev,st.st_ino) or named.st_size<st.st_size:
        raise ValueError('UART changed during checkpoint')
    record={'schema':1,'uart_offset':st.st_size,'source_dev':st.st_dev,'source_inode':st.st_ino,
            'tail_start_offset':start,'tail_bytes':len(tail),'tail_sha256':digest(tail),
            'limitations':'Checkpoint identifies an append-only file and its recent bytes, not UART completeness.'}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as f:json.dump(record,f,indent=2);f.write('\n')
    return record


def collect(uart, flash, output, max_bytes=128*1024*1024):
    flash_bytes = flash.read_bytes()
    meta = json.loads(flash_bytes)
    offset = meta.get('uart_offset')
    image = meta.get('boot_sha256')
    if type(offset) is not int or offset < 0 or not isinstance(image,str) or not re.fullmatch(r'[0-9a-f]{64}', image):
        raise ValueError('flash record needs nonnegative uart_offset and lowercase boot_sha256')
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError('invalid snapshot limit')
    checkpoint_path=meta.get('uart_checkpoint')
    checkpoint_bytes=None; point=None
    if checkpoint_path is not None:
        if not isinstance(checkpoint_path,str):raise ValueError('invalid checkpoint path')
        cp=Path(checkpoint_path)
        if not cp.is_absolute():cp=flash.parent/cp
        checkpoint_bytes=cp.read_bytes();point=json.loads(checkpoint_bytes)
    with uart.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if offset > before.st_size:
            raise ValueError('flash offset past captured UART end')
        length = before.st_size - offset
        if length > max_bytes:
            raise ValueError('snapshot exceeds configured memory limit')
        if point is not None:
            start=point.get('tail_start_offset');n=point.get('tail_bytes')
            if (type(point.get('uart_offset')) is not int or type(point.get('source_dev')) is not int or type(point.get('source_inode')) is not int or point.get('uart_offset')!=offset or point.get('source_dev')!=before.st_dev or point.get('source_inode')!=before.st_ino or
                type(start) is not int or type(n) is not int or start<0 or n<0 or n>4096 or start+n!=offset):
                raise ValueError('UART checkpoint identity or bounds mismatch')
            stream.seek(start);tail=stream.read(n)
            if len(tail)!=n or digest(tail)!=point.get('tail_sha256'):raise ValueError('UART checkpoint tail mismatch')
        stream.seek(offset)
        raw = stream.read(length)
        after = os.fstat(stream.fileno())
        named = uart.stat()
    if (named.st_dev,named.st_ino)!=(before.st_dev,before.st_ino) or named.st_size < before.st_size:
        raise ValueError('UART path changed or was truncated while snapshotting')
    if len(raw) != length or after.st_size < before.st_size or (after.st_dev,after.st_ino)!=(before.st_dev,before.st_ino):
        raise ValueError('UART file changed incompatibly while snapshotting')
    positions=[]; cursor=0
    while True:
        hit=raw.find(ANCHOR,cursor)
        if hit<0:break
        positions.append(hit);cursor=hit+len(ANCHOR)
    if output.exists():
        raise ValueError('refusing existing output')
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.'+output.name+'.',dir=output.parent))
    try:
        (stage/'post-flash.raw').write_bytes(raw)
        (stage/'flash-record.json').write_bytes(flash_bytes)
        if checkpoint_bytes is not None:
            (stage/'uart-checkpoint.json').write_bytes(checkpoint_bytes)
            (stage/'checkpoint-tail.raw').write_bytes(tail)
        boots=[]
        for i,start in enumerate(positions):
            end=positions[i+1] if i+1<len(positions) else len(raw)
            data=raw[start:end];filename=f'boot-{i+1}.raw';(stage/filename).write_bytes(data)
            boots.append({'index':i+1,'path':filename,'start_offset':offset+start,'end_offset':offset+end,
                          'closed_by_next_preloader':i+1<len(positions),'sha256':digest(data),'bytes':len(data),
                          'boot_reasons':[{'offset':m.start(),'absolute_offset':offset+start+m.start(),'value':int(m.group(1),16 if m.group(1).lower().startswith(b'0x') else 10)} for m in REASON.finditer(data)],
                          'boot_reason_markers':len(list(REASON_MARK.finditer(data))),
                          'panics':event_lines(data,PANIC,offset+start),'el3_candidates':event_lines(data,EL3,offset+start),
                          'integrity':'unknown_without_external_evidence'})
        for i in range(len(boots)-1):
            later=boots[i+1]['boot_reasons']
            boots[i]['subsequent_boot_reason_candidate']=later[0] if later else None
        prefix_end=positions[0] if positions else len(raw)
        prefix=raw[:prefix_end]
        completed=meta.get('flash_completed_uart_offset')
        boundary=completed-offset if type(completed) is int and offset<=completed<=offset+prefix_end else None
        prefix_segments=[]
        for label,start,end in ([('before_flash_completion',0,boundary),('after_flash_completion',boundary,prefix_end)] if boundary is not None else [('completion_boundary_unknown',0,prefix_end)]):
            segment=prefix[start:end]
            prefix_segments.append({'phase':label,'start_offset':offset+start,'end_offset':offset+end,'panics':event_lines(segment,PANIC,offset+start),'el3_candidates':event_lines(segment,EL3,offset+start)})
        record={'schema':1,'flash_record_sha256':digest(flash_bytes),'flash_offset':offset,
                'flash_boot_sha256':image,'flash_success_claimed':meta.get('flash_success') is True and isinstance(meta.get('flash_success_evidence'),str) and bool(meta['flash_success_evidence'].strip()),
                'flash_success_evidence':meta.get('flash_success_evidence'),
                'flash_completed_uart_offset':meta.get('flash_completed_uart_offset'),
                'first_anchor_after_flash':type(meta.get('flash_completed_uart_offset')) is int and bool(positions) and offset <= meta['flash_completed_uart_offset'] <= offset+positions[0],
                'snapshot_end_offset':before.st_size,'source_dev':before.st_dev,'source_inode':before.st_ino,
                'snapshot_path':'post-flash.raw','snapshot_sha256':digest(raw),
                'checkpoint_verified':point is not None,'checkpoint_sha256':digest(checkpoint_bytes) if checkpoint_bytes is not None else None,
                'pre_preloader':{'start_offset':offset,'end_offset':offset+prefix_end,
                   'boot_reason_markers':len(list(REASON_MARK.finditer(prefix))),
                   'panics':event_lines(prefix,PANIC,offset),'el3_candidates':event_lines(prefix,EL3,offset),
                   'boot_reasons':[{'offset':m.start(),'absolute_offset':offset+m.start(),'value':int(m.group(1),16 if m.group(1).lower().startswith(b'0x') else 10)} for m in REASON.finditer(prefix)]},
                'prefix_bytes_before_first_preloader':prefix_end, 'prefix_segments':prefix_segments, 'boots':boots,
                'capture_limitations':'Append-only snapshot; loss, concurrent in-place edits, flash success and UART completeness are not proven. Panic/EL3 patterns are incomplete; watchdog causality is not proven. Snapshot byte cap is not a process-memory cap.'}
        (stage/'capture.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
        output.mkdir()  # atomic reservation: never replace another caller's directory
        for entry in stage.iterdir():entry.rename(output/entry.name)
    finally:
        if stage.exists():shutil.rmtree(stage)
    return record


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--uart-log',type=Path,required=True);p.add_argument('--flash-record',type=Path)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--checkpoint',action='store_true')
    p.add_argument('--max-bytes',type=int,default=128*1024*1024)
    a=p.parse_args()
    if a.checkpoint:
        if a.flash_record:p.error('--checkpoint does not accept --flash-record')
        checkpoint(a.uart_log,a.out)
    else:
        if not a.flash_record:p.error('--flash-record is required for capture')
        collect(a.uart_log,a.flash_record,a.out,a.max_bytes)

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,TypeError,AttributeError) as e:raise SystemExit(str(e))
