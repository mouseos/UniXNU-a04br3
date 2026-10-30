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
 result={'schema':1,'boot_raw_sha256':hashlib.sha256(data).hexdigest(),'image_sha256_user_supplied':image_sha,'vm_usage':[],'kernel_vm':[],'copies':[],'skips':[],'errors':[],'limitations':['input image identity is caller-supplied, not independently authenticated','VM counters are non-atomic and may be cached; no fresh-snapshot claim','kernel heartbeat timestamp follows the VM read; it is not its sampling time','user and kernel clock epochs are not assumed equal','copy logical bytes measure CPU memcpy, not DRAM controller traffic','no overlap or process attribution is inferred']}
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
  elif 'a04br3_mem_copy phase=error ' in line:result['errors'].append(line.split('a04br3_mem_copy ',1)[1])
 if pending is not None:raise ValueError('last VM sample lacks heartbeat')
 for snapshot in result['vm_usage']:
  if 'extended' not in snapshot:snapshot['extended']={'status':'missing'}
 return result
def assess(data, result, expected, size=None):
 """Explicit single invocation protocol; incomplete data cannot be success."""
 if expected == 'kernel-vm':
  result['assessment']={'expected':expected,'status':'observed' if result['kernel_vm'] else 'missing','copy_test_success_claimed':False}
  return bool(result['kernel_vm'])
 rows=[]
 for line in data.splitlines(keepends=True):
  if b'a04br3_mem_copy ' not in line: continue
  if not line.endswith(b'\n'): raise ValueError('unterminated diagnostic record')
  record=line.split(b'a04br3_mem_copy ',1)[1].rstrip(b'\r\n').decode('ascii')
  pairs=record.split(' ')
  if any(not re.fullmatch(r'[a-z_]+=[A-Za-z0-9_]+',x) for x in pairs):raise ValueError('malformed diagnostic record')
  f=parse_fields(record)
  if len(f)!=len(pairs):raise ValueError('duplicate diagnostic field')
  rows.append(f)
 begins=[(i,f) for i,f in enumerate(rows) if f.get('phase')=='begin']
 ends=[(i,f) for i,f in enumerate(rows) if f.get('phase')=='end']
 status='missing'
 if begins or ends:
  if len(begins)!=1 or len(ends)!=1:status='incomplete'
  else:
   bi,b=begins[0];ei,e=ends[0]
   if bi!=0 or ei!=len(rows)-1 or bi>=ei or b.get('kind')!=expected or e.get('kind')!=expected or set(b)!={'phase','kind','size_bytes'} or set(e)!={'phase','kind','status'}:raise ValueError('invalid invocation boundaries')
   declared=nums(b,('size_bytes',))['size_bytes']
   if expected=='observe' and declared!=0 or expected=='copy' and declared!=size:raise ValueError('unexpected invocation size')
   phases=[x.get('phase', 'copy_result' if 'threads' in x else '') for x in rows]
   allowed={'begin','end','prepare','vm_activity','vm_activity_extended','vm_usage','vm_usage_extended','memory_check','copy_result','skip','error'}
   if any(x not in allowed for x in phases):raise ValueError('unknown diagnostic phase')
   actual=e['status']
   if actual not in ('ok','skip','error'):raise ValueError('invalid invocation status')
   if actual=='ok':
    if result['skips'] or result['errors'] or len(result['vm_usage'])!=1 or result['vm_usage'][0].get('extended',{}).get('status')=='missing':raise ValueError('success has missing VM or failure records')
    required=['begin']+(['prepare'] if expected=='copy' else [])+['vm_activity','vm_activity_extended','vm_usage','vm_usage_extended']+(['memory_check','copy_result'] if expected=='copy' else [])+['end']
    if phases!=required:raise ValueError('missing, duplicate or out-of-order diagnostic phase')
    activity=next(x for x in rows if x.get('phase')=='vm_activity'); av=nums(activity,('monotonic_ns','query_begin_ns','query_end_ns','page_size','pageins','pageouts','reactivations','faults','purgeable_pages','purges'))
    v=result['vm_usage'][0]['sample']
    if activity.get('non_atomic')!='1' or activity.get('cache_freshness')!='unknown' or any(av[k]!=v[k] for k in ('query_begin_ns','query_end_ns','page_size')):raise ValueError('inconsistent VM activity')
    extended=next(x for x in rows if x.get('phase')=='vm_activity_extended')
    if extended.get('status')!='unavailable':
     nums(extended,('compressions','decompressions','swapins','swapouts'))
     if extended.get('non_atomic')!='1':raise ValueError('invalid extended VM activity')
    if expected=='observe' and result['copies'] or expected=='copy' and (len(result['copies'])!=1 or result['copies'][0]['size_bytes']!=size):raise ValueError('success has missing or unexpected copy')
    if expected=='copy':
     prep=next(x for x in rows if x.get('phase')=='prepare'); check=next(x for x in rows if x.get('phase')=='memory_check')
     pv=nums(prep,('size_bytes','monotonic_ns')); cv=nums(check,('reported_free_bytes','speculative_bytes','free_bytes','threshold_bytes'))
     sample=result['vm_usage'][0]; v=sample['sample']
     if pv['size_bytes']!=size or cv['reported_free_bytes']!=v['free_pages']*v['page_size'] or cv['speculative_bytes']!=v['speculative_pages']*v['page_size'] or cv['free_bytes']!=sample['free_excluding_speculative_bytes'] or cv['threshold_bytes']!=size*2+32*1048576 or cv['free_bytes']<cv['threshold_bytes'] or check.get('cache_freshness')!='unknown':raise ValueError('inconsistent memory guard')
    status='success'
   elif actual=='skip':
    if expected!='copy' or len(result['skips'])!=1 or 'reason=insufficient_free_pages' not in result['skips'][0] or result['errors'] or result['copies']:raise ValueError('inconsistent skip')
    status='skip'
   else:status='error'
 elif result['errors']:status='error'
 elif result['skips']:status='incomplete'
 result['assessment']={'expected':expected,'status':status,'expected_size_bytes':size}
 return status=='success'

def validate_flash_log(log):
 # Accept only the complete three-line fastboot transcript used by this workflow.
 # A hash and a count of OKAY tokens cannot identify the partition or stage.
 lines=log.split(b'\n')
 send=rb"Sending 'boot' \(([1-9][0-9]*) KB\)[ \t]+OKAY \[[ \t]*[0-9]+\.[0-9]+s\]"
 write=rb"Writing 'boot'[ \t]+OKAY \[[ \t]*[0-9]+\.[0-9]+s\]"
 finish=rb"Finished\. Total time: [0-9]+\.[0-9]+s"
 if len(lines)!=4 or lines[-1]!=b'' or not all(re.fullmatch(p,l) for p,l in zip((send,write,finish),lines[:3])):
  raise ValueError('flash log must contain exactly successful boot send, boot write, and completion')

def capture_evidence(raw, image, capture_path, flash_path, snapshot_path, flash_log):
 cap_bytes=capture_path.read_bytes(); flash_bytes=flash_path.read_bytes(); snap=snapshot_path.read_bytes(); log=flash_log.read_bytes()
 cap=json.loads(cap_bytes); flash=json.loads(flash_bytes); boots=cap.get('boots',[])
 sha=lambda b:hashlib.sha256(b).hexdigest()
 if cap.get('flash_record_sha256')!=sha(flash_bytes) or cap.get('snapshot_sha256')!=sha(snap):raise ValueError('capture evidence hash mismatch')
 if cap.get('flash_boot_sha256')!=image or flash.get('boot_sha256')!=image or flash.get('flash_success') is not True or cap.get('flash_success_claimed') is not True:raise ValueError('flash identity or success claim mismatch')
 evidence=cap.get('flash_success_evidence','')
 if not isinstance(evidence,str) or not re.search(r' sha256='+sha(log)+r'\Z',evidence) or flash.get('flash_success_evidence')!=evidence:raise ValueError('flash log evidence mismatch')
 validate_flash_log(log)
 if cap.get('checkpoint_verified') is not True or cap.get('first_anchor_after_flash') is not True or cap.get('offset_crosses_preloader_anchor') is not False or cap.get('boots_truncated') is not False or len(boots)<2:raise ValueError('incomplete capture boundary evidence')
 base=cap.get('flash_offset'); start=boots[0].get('start_offset'); end=boots[0].get('end_offset'); completed=cap.get('flash_completed_uart_offset')
 if any(type(x) is not int for x in (base,start,end,completed)) or not base<=completed<=start<end<=base+len(snap) or base!=flash.get('uart_offset') or completed!=flash.get('flash_completed_uart_offset') or cap.get('snapshot_end_offset')!=base+len(snap):raise ValueError('invalid capture offsets')
 a=start-base; z=end-base
 if boots[0].get('index')!=1 or boots[0].get('closed_by_next_preloader') is not True or boots[1].get('start_offset')!=end or cap.get('prefix_bytes_before_first_preloader')!=a or snap[:a].count(ANCHOR) or snap[a:z]!=raw or sha(raw)!=boots[0].get('sha256') or boots[0].get('bytes')!=len(raw) or not snap[z:].startswith(ANCHOR):raise ValueError('raw or next boot boundary mismatch')
 return {'capture_sha256':sha(cap_bytes),'flash_record_sha256':sha(flash_bytes),'snapshot_sha256':sha(snap),'flash_log_sha256':sha(log),'first_boot_start_offset':start,'next_boot_start_offset':end,'image_sha256_recorded':image,'flash_success':'recorded claim supported by flash log, not runtime image authentication','limitations':'UART loss and runtime image identity remain unproven'}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('boot',type=Path);p.add_argument('--image-sha256',required=True);p.add_argument('--expected-raw-sha256',required=True);p.add_argument('--expect',choices=('kernel-vm','observe','copy'),required=True);p.add_argument('--size-bytes',type=int)
 p.add_argument('--capture-json',type=Path);p.add_argument('--flash-json',type=Path);p.add_argument('--snapshot',type=Path);p.add_argument('--flash-log',type=Path);a=p.parse_args()
 try:
  if a.expect=='copy' and a.size_bytes not in (65536,262144,1048576):raise ValueError('copy requires an expected supported size')
  if a.expect!='copy' and a.size_bytes is not None:raise ValueError('size only applies to copy')
  data=a.boot.read_bytes()
  if hashlib.sha256(data).hexdigest()!=a.expected_raw_sha256:raise ValueError('raw sha256 mismatch')
  result=analyze(data,a.image_sha256)
  files=(a.capture_json,a.flash_json,a.snapshot,a.flash_log)
  if any(files) and not all(files):raise ValueError('all capture evidence files required')
  result['capture_evidence']=capture_evidence(data,a.image_sha256,*files) if all(files) else {'status':'not_supplied'}
  ok=assess(data,result,a.expect,a.size_bytes)
  print(json.dumps(result,indent=2))
  if not ok:raise SystemExit(2)
 except (OSError,ValueError,UnicodeError,KeyError,TypeError) as e:p.exit(1,'error: '+str(e)+'\n')
