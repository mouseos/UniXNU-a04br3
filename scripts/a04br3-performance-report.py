#!/usr/bin/env python3
"""Offline evidence extraction; each report covers one explicitly selected XNU boot."""
import argparse,hashlib,json,re
from pathlib import Path

def fields(line):
    return {k:int(v) for k,v in re.findall(r'(\w+)=(\d+)(?=\s|$)',line.strip())}

def report(raw,epoch):
    markers=[m.start() for m in re.finditer(rb'\[pmic6392_init\] Preloader Start',raw)]
    cuts=sorted(set([0,*markers,len(raw)]));inventory=[];kernels=[]
    for begin,end in zip(cuts,cuts[1:]):
        part=raw[begin:end];banners=part.count(b'Darwin Kernel Version')
        item=dict(byte_begin=begin,byte_end=end,kernel_banners=banners,panic=b'panic(cpu' in part,
            boot_reasons=[int(x) for x in re.findall(rb'BOOT_REASON:\s*(\d+)',part)])
        inventory.append(item)
        if banners:
            if not markers and banners>1:raise ValueError('Multiple banners without reliable reset boundary; select an isolated boot')
            kernels.append((begin,end))
    if epoch<1 or epoch>len(kernels):raise ValueError('kernel epoch not present')
    begin,end=kernels[epoch-1]
    text=raw[begin:end].decode(errors='replace'); rows=text.split('\n');line_base=raw[:begin].count(b'\n')
    truncated_tail=bool(text) and not text.endswith('\n')
    if truncated_tail:rows=rows[:-1]
    cycles=[];pending={};last_cpu={};cpu=[];io=[];block=None;vm=[];mem=[];pf=[]
    for no,line in enumerate(rows,line_base+1):
        line=line.replace('\r','').strip();f=fields(line)
        m=re.search(r'runtime-cycle cpu=(\d+) round=(\d+).*status=(\d+) cntfrq=(\d+)',line)
        if m:pending[int(m[1])]=dict(cpu=int(m[1]),round=int(m[2]),status=int(m[3]),timer_hz=int(m[4]),line=no)
        m=re.search(r'runtime-cycle-count cpu=(\d+) ticks=(\d+) cycles=(\d+) outer=(\d+)',line)
        if m:
            p=pending.pop(int(m[1]),None)
            if p and p['status']==0 and int(m[2]) and int(m[3]):
                p.update(ticks=int(m[2]),cycles=int(m[3]),outer_ticks=int(m[4]));p['mhz']=p['cycles']*p['timer_hz']/p['ticks']/1e6;cycles.append(p)
        if 'ArmCpu: cycle-clock ' in line and f.get('status')==0 and f.get('ticks') and f.get('cycles') and f.get('cntfrq'):
            cycles.append(dict(cpu=f.get('cpu'),ticks=f['ticks'],cycles=f['cycles'],timer_hz=f['cntfrq'],mhz=f['cycles']*f['cntfrq']/f['ticks']/1e6,line=no,scope='startup only'))
        if 'a04br3 cpu-load cpu=' in line and all(k in f for k in ('cpu','at_ns','user','system','idle','nice')):
            old=last_cpu.get(f['cpu']);last_cpu[f['cpu']]=f
            if old and f['at_ns']>old['at_ns']:
                delta={k:f[k]-old[k] for k in ('user','system','idle','nice')}
                state_valid=all(v.get('state_before')==v.get('state_after') and v.get('state_before') in (4,5,6) for v in (old,f))
                valid=state_valid and all(x>=0 for x in delta.values()) and sum(delta.values())>0
                cpu.append(dict(cpu=f['cpu'],end_ns=f['at_ns'],interval_ns=f['at_ns']-old['at_ns'],busy_percent=100*(delta['user']+delta['system']+delta['nice'])/sum(delta.values()) if valid else None,valid=valid,non_atomic=True))
        if 'msdc io sample seq=' in line:
            block=dict(seq=f['seq'],begin_ns=f['begin_ns'],end_ns=f['end_ns'],transfers=[],offered=[],queue_rows=0,line=no) if all(k in f for k in ('seq','begin_ns','end_ns')) else None
        if 'msdc transfer seq=' in line and block and f.get('seq')==block['seq']:
            mode=re.search(r'mode=(\w+) size=(\w+)',line)
            if mode and all(k in f for k in ('calls','ok_bytes','errors','elapsed_ns')):
                block['transfers'].append(dict(mode=mode[1],size=mode[2],**{k:f[k] for k in ('calls','ok_bytes','errors','elapsed_ns')},service_MiB_s=f['ok_bytes']*1e9/f['elapsed_ns']/1048576 if f['elapsed_ns'] and f['errors']==0 else None))
        if 'msdc queue seq=' in line and block and f.get('seq')==block['seq']:
            block['queue']=f;block['queue_rows']+=1
        if 'msdc offered seq=' in line and block and f.get('seq')==block['seq']:
            mode=re.search(r'mode=(\w+) size=(\w+)',line)
            if mode:block['offered'].append(dict(mode=mode[1],size=mode[2],raw=line,values=f))
        if 'msdc io end seq=' in line and block and f.get('seq')==block['seq']:
            # Nine transfer rows: read DMA/PIO and write, three size classes each.
            if len(block['transfers'])==9 and {(x['mode'],x['size']) for x in block['transfers']}=={(m,s) for m in ('dma_read','pio_read','write') for s in ('le4k','4k_16k','gt16k')} and block['end_ns']>block['begin_ns'] and f.get('rows')==16 and block['queue_rows']==1 and len(block['offered'])==6 and {(x['mode'],x['size']) for x in block['offered']}=={(m,s) for m in ('read','write') for s in ('le4k','4k_16k','gt16k')}:io.append(block)
            block=None
        if 'a04br3 perf vm pagesize=' in line and f.get('pagesize',0)>0:
            vm.append(dict(line=no,raw=f,category_bytes={k:v*f['pagesize'] for k,v in f.items() if k in ('free','active','inactive','wired','compressor','speculative')},non_atomic=True))
        if 'a04br3_mem_copy ' in line:mem.append(dict(line=no,text=line))
        if re.search(r'PF \d+/\d+',line):pf.append(dict(line=no,text=line))
    io_intervals=[]
    for first,last in zip(io,io[1:]):
        window=last['begin_ns']-first['begin_ns']
        if window<=0:continue
        old={(x['mode'],x['size']):x for x in first['transfers']}
        changes=[]
        for x in last['transfers']:
            previous=old[(x['mode'],x['size'])]
            delta={k:x[k]-previous[k] for k in ('calls','ok_bytes','errors','elapsed_ns')}
            valid=all(v>=0 for v in delta.values()) and delta['errors']==0
            changes.append(dict(mode=x['mode'],size=x['size'],valid=valid,delta=delta if valid else None,wall_MiB_s=delta['ok_bytes']*1e9/window/1048576 if valid else None))
        io_intervals.append(dict(first_seq=first['seq'],last_seq=last['seq'],window_ns=window,transfers=changes,non_atomic=True))
    return dict(source_sha256=hashlib.sha256(raw).hexdigest(),truncated_tail_ignored=truncated_tail,epoch=epoch,epoch_count=len(kernels),boot_inventory=inventory,byte_begin=begin,byte_end=end,epoch_sha256=hashlib.sha256(raw[begin:end]).hexdigest(),panic_observed='panic(cpu' in text,cpu_direct_cycles=cycles,cpu_interval_usage=cpu,emmc_complete_samples=io,emmc_interval_workload=io_intervals,memory_usage=vm,memory_benchmark=mem,gpu_scanout=pf,raw_cpu_evidence=[dict(line=line_base+i+1,text=l.strip()) for i,l in enumerate(rows) if 'runtime-cycle' in l or 'cycle-clock' in l],network='not measured by this kernel log parser',limitations=['CPU busy-cycle samples do not measure idle MHz or prove PMU ownership; retain source owner/window lines.','MSDC rate is cumulative successful service bytes divided by cumulative elapsed service time, not device bus peak or wall throughput; counters non-atomic.','VM categories may overlap; do not sum them into physical usage.','PF timing includes CPU import/encode/commit/wait, not GPU execution or utilization.','No mixed-epoch deltas; incomplete MSDC blocks and counter decreases are not valid rates.'])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('log',type=Path);p.add_argument('--epoch',type=int,required=True);a=p.parse_args();r=report(a.log.read_bytes(),a.epoch);r['source']=str(a.log.resolve());print(json.dumps(r,indent=2))
if __name__=='__main__':main()
