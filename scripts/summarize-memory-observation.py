#!/usr/bin/env python3
"""Summarize a single fixed UART boot; never accesses a device."""
import argparse, hashlib, json, re
from pathlib import Path
ANCHOR=b'[pmic6392_init] Preloader Start'
PAIR=re.compile(r'([a-z_]+)=([^\s]+)')
HEX=re.compile(r'^[0-9a-f]{64}$')
VM=('pagesize','free','active','inactive','wired','compressor','speculative','throttled','pageable_internal','pageable_external','mode','active_compressor','active_swap')
USAGE=('query_begin_ns','query_end_ns','page_size','free_pages','speculative_pages','active_pages','inactive_pages','wired_pages')
COPY=('size_bytes','start_monotonic_ns','end_monotonic_ns','elapsed_ns','logical_bytes','verified','threads','synthetic')
def parse_fields(text):
 pairs=PAIR.findall(text); fields=dict(pairs)
 if len(fields)!=len(pairs):raise ValueError('duplicate field')
 return fields
def nums(fields, names):
 if any(k not in fields or not re.fullmatch(r'[0-9]+',fields[k]) for k in names):raise ValueError('missing or invalid numeric field')
 return {k:int(fields[k]) for k in names}
def analyze(data,image_sha):
 if not HEX.fullmatch(image_sha):raise ValueError('image sha256 required')
 if not data.startswith(ANCHOR) or data.count(ANCHOR)!=1:raise ValueError('input is not a single boot beginning at preloader')
 result={'schema':1,'boot_raw_sha256':hashlib.sha256(data).hexdigest(),'image_sha256_user_supplied':image_sha,'vm_usage':[],'kernel_vm':[],'copies':[],'skips':[],'limitations':['input image identity is caller-supplied, not independently authenticated','VM counters are non-atomic and may be cached; no fresh-snapshot claim','kernel heartbeat timestamp follows the VM read; it is not its sampling time','user and kernel clock epochs are not assumed equal','copy logical bytes measure CPU memcpy, not DRAM controller traffic','no overlap or process attribution is inferred']}
 pending=None
 for line in data.decode('utf-8','replace').splitlines():
  if 'a04br3 perf vm pagesize=' in line:
   if pending is not None:raise ValueError('VM sample without following heartbeat')
   pending=nums(parse_fields(line.split('a04br3 perf vm ',1)[1]),VM)
   if not pending['pagesize']:raise ValueError('zero VM page size')
  elif 'a04br3 perf sample ns=' in line and pending is not None:
   f=nums(parse_fields(line.split('a04br3 perf sample ',1)[1]),('ns','tasks'))
   result['kernel_vm'].append({'reported_pages':pending,'bytes_by_field':{k:pending[k]*pending['pagesize'] for k in ('free','active','inactive','wired','compressor','speculative','throttled','pageable_internal','pageable_external')},'following_heartbeat_ns':f['ns']});pending=None
  elif 'a04br3_mem_copy phase=vm_usage ' in line:
   f=parse_fields(line.split('a04br3_mem_copy ',1)[1]);v=nums(f,USAGE)
   if f.get('cache_freshness')!='unknown' or f.get('non_atomic')!='1' or not v['page_size'] or v['query_begin_ns']>v['query_end_ns'] or v['speculative_pages']>v['free_pages']:raise ValueError('invalid VM snapshot')
   result['vm_usage'].append({'sample':v,'free_excluding_speculative_bytes':(v['free_pages']-v['speculative_pages'])*v['page_size'],'bytes_by_field':{k:v[k]*v['page_size'] for k in ('free_pages','speculative_pages','active_pages','inactive_pages','wired_pages')}})
  elif 'a04br3_mem_copy phase=vm_usage_extended ' in line:
   if not result['vm_usage'] or 'extended' in result['vm_usage'][-1]:raise ValueError('orphan or duplicate extended VM snapshot')
   f=parse_fields(line.split('a04br3_mem_copy ',1)[1])
   if f.get('status')=='unavailable':result['vm_usage'][-1]['extended']={'status':'unavailable'}
   else:
    v=nums(f,('compressor_pages','external_pages','internal_pages'))
    if f.get('non_atomic')!='1':raise ValueError('invalid extended VM snapshot')
    page_size=result['vm_usage'][-1]['sample']['page_size']
    result['vm_usage'][-1]['extended']={'reported_pages':v,'bytes_by_field':{k:n*page_size for k,n in v.items()}}
  elif 'a04br3_mem_copy synthetic=1 threads=' in line:
   v=nums(parse_fields(line.split('a04br3_mem_copy ',1)[1]),COPY)
   if v['verified']!=1 or v['threads']!=1 or v['synthetic']!=1 or v['logical_bytes']!=v['size_bytes'] or not 65536<=v['size_bytes']<=64*1048576 or v['end_monotonic_ns']<=v['start_monotonic_ns'] or v['elapsed_ns']!=v['end_monotonic_ns']-v['start_monotonic_ns']:raise ValueError('invalid copy measurement')
   result['copies'].append({**v,'logical_mib_per_s':v['logical_bytes']*1e9/v['elapsed_ns']/1048576})
  elif 'a04br3_mem_copy phase=skip ' in line:result['skips'].append(line.split('a04br3_mem_copy ',1)[1])
 if pending is not None:raise ValueError('last VM sample lacks heartbeat')
 for snapshot in result['vm_usage']:
  if 'extended' not in snapshot:snapshot['extended']={'status':'missing'}
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('boot',type=Path);p.add_argument('--image-sha256',required=True);p.add_argument('--expected-raw-sha256',required=True);a=p.parse_args()
 try:
  data=a.boot.read_bytes()
  if hashlib.sha256(data).hexdigest()!=a.expected_raw_sha256:raise ValueError('raw sha256 mismatch')
  print(json.dumps(analyze(data,a.image_sha256),indent=2))
 except (OSError,ValueError) as e:p.exit(1,'error: '+str(e)+'\n')
