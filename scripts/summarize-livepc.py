#!/usr/bin/env python3
"""Aggregate logged backboardd PC records, without treating image snapshots as atomic mappings."""
import collections,hashlib,json,pathlib,re,sys
p=pathlib.Path(sys.argv[1]); raw=p.read_bytes(); text=raw.decode('utf-8','replace'); marker='a04br3-autoreset: reset requested on UART';cut=text.find(marker)
if cut>=0:text=text[:cut]
report=re.compile(r'livepc boot=([\w-]+) pid=(\d+) epoch=(\d+) previous_report_start_ns=(\d+) report_start_ns=(\d+)(?=\s|$)')
sample=re.compile(r'livepc sample epoch=(\d+) cpu=(\d+) seq=(\d+) pid=(\d+) uid=(\d+) exec_version=(\d+) tid=(\d+) mode=([\w-]+) pc=([0-9a-f]+) lr=([0-9a-f]+)(?=\s|$)')
snapshot=re.compile(r'livepc images epoch=(\d+) pid=(\d+) uid=(\d+) exec_version=(\d+) start_ns=(\d+) end_ns=(\d+) .*? count=(\d+) captured=(\d+) truncated=(\d+) detail_incomplete=(\d+) status=([A-Za-z_]+)(?=\s|$)')
image=re.compile(r'livepc image epoch=(\d+) index=(\d+) dyld=(\d+) mapping_at_sample=(\w+) load=([0-9a-f]+) text_vm=([0-9a-f]+) text_size=([0-9a-f]+) text_valid=(\d+) macho=(\d+) detail_complete=(\d+) uuid_valid=(\d+) uuid=([0-9a-f]{32})(?=\s|$)')
cpus=re.compile(r'livepc cpu=(\d+) epoch=(\d+) ticks=(\d+) end=(\d+) lost=(\d+) torn=(\d+)(?=\s|$)')
perf=re.compile(r'perf task p(\d+) backboardd uid=(\d+) cpu_ns=(\d+) cpu_at_ns=(\d+)')
epochs={};imgs=collections.defaultdict(list);snaps={}; records=[];perfs=[]; cpu=[]; image_keys=set()
for lno,line in enumerate(text.splitlines(),1):
 for m in report.finditer(line):
  boot,pid,ep,start,end=m.groups(); ep=int(ep)
  if ep in epochs: raise SystemExit('duplicate epoch; provide one boot only')
  epochs[ep]={'boot':boot,'pid':int(pid),'start_ns':int(start),'end_ns':int(end)}
 for m in snapshot.finditer(line):
  values=m.groups();ep,pid,uid,execv,start,end,count,cap,trunc,inc=map(int,values[:10]);status=values[10]
  if ep in snaps: raise SystemExit('duplicate snapshot')
  if end<start: raise SystemExit('invalid snapshot times')
  snaps[ep]={'pid':pid,'uid':uid,'exec':execv,'start_ns':start,'end_ns':end,'count':count,'captured':cap,'truncated':trunc,'incomplete':inc,'status':status}
 for m in image.finditer(line):
  ep,idx,dyld,at,load,tvm,size,valid,macho,complete,uvalid,uuid=m.groups();
  key=(int(ep),int(idx))
  if key in image_keys: raise SystemExit('duplicate image index')
  image_keys.add(key)
  if valid=='1' and macho=='1' and complete=='1' and uvalid=='1' and int(size,16)>0:imgs[int(ep)].append({'index':int(idx),'load':int(load,16),'size':int(size,16),'uuid':uuid,'at_sample':at})
 for m in sample.finditer(line):
  ep,c,seq,pid,uid,ex,tid,mode,pc,lr=m.groups();records.append({'epoch':int(ep),'cpu':int(c),'seq':int(seq),'pid':int(pid),'uid':int(uid),'exec':int(ex),'tid':int(tid),'mode':mode,'pc':int(pc,16),'line':lno})
 for m in perf.finditer(line):
  pid,uid,ns,t=map(int,m.groups());perfs.append({'pid':pid,'uid':uid,'cpu_ns':ns,'at_ns':t,'line':lno})
 for m in cpus.finditer(line):
  c,ep,ticks,end,lost,torn=map(int,m.groups());cpu.append({'cpu':c,'epoch':ep,'ticks':ticks,'end':end,'lost':lost,'torn':torn})
for marker,pattern in [('livepc boot=',report),('livepc sample epoch=',sample),('livepc images epoch=',snapshot),('livepc image epoch=',image),('livepc cpu=',cpus)]:
 if text.count(marker)!=len(list(pattern.finditer(text))): raise SystemExit('malformed or truncated '+marker)
keys=[(r['epoch'],r['cpu'],r['seq']) for r in records]
if len(keys)!=len(set(keys)): raise SystemExit('duplicate sample sequence')
if len({x['boot'] for x in epochs.values()}) != 1: raise SystemExit('expected exactly one boot ID')
if any(r['epoch'] not in epochs for r in records): raise SystemExit('sample has no matching epoch report')
if any(c['epoch'] not in epochs for c in cpu): raise SystemExit('CPU record has no epoch')
counts={(c['epoch'],c['cpu']):c for c in cpu}
if len(counts)!=len(cpu): raise SystemExit('duplicate CPU record')
all_cpus={0,1,2,3}
if any(r['cpu'] not in all_cpus for r in records) or any(c['cpu'] not in all_cpus for c in cpu): raise SystemExit('unexpected CPU')
if any({c for e,c in counts if e==ep}!=all_cpus for ep in epochs):
 raise SystemExit('missing CPU ring counters')
ordered=sorted(epochs)
for a,b in zip(ordered,ordered[1:]):
 if b!=a+1 or epochs[b]['start_ns']!=epochs[a]['end_ns']: raise SystemExit('epoch sequence or report time discontinuity')
if any(v['end_ns']<v['start_ns'] for v in epochs.values()): raise SystemExit('invalid report times')
prev={c:0 for c in all_cpus}
for ep in ordered:
 for c in sorted(all_cpus):
  ring=counts[(ep,c)]
  if ring['lost'] or ring['torn']: raise SystemExit('loss or torn sample in ring')
  seqs=sorted(r['seq'] for r in records if r['epoch']==ep and r['cpu']==c)
  if ring['end']<prev[c] or len(seqs)!=ring['end']-prev[c] or any(seq!=prev[c]+i for i,seq in enumerate(seqs)):
   raise SystemExit('sample sequence does not match CPU ring end')
  prev[c]=ring['end']
modules=collections.Counter();byepoch=collections.defaultdict(collections.Counter);offsets=collections.Counter();pids=collections.Counter();unmatched=[]
for rec in records:
 ep=rec['epoch'];pids[rec['pid']]+=1
 if rec['mode']!='user': key='mode:'+rec['mode']
 elif ep not in snaps or snaps[ep]['status'] not in ('ok','partial','complete') or snaps[ep]['pid']!=rec['pid'] or snaps[ep]['uid']!=rec['uid'] or snaps[ep]['exec']!=rec['exec']:key='unmatched:missing-or-different-snapshot'
 else:
  matches=[im for im in imgs[ep] if im['load']<=rec['pc']<im['load']+im['size']]
  if len(matches)==1:
   im=matches[0];key='candidate_uuid:'+im['uuid'];rec['candidate_uuid']=im['uuid'];rec['offset']=rec['pc']-im['load'];offsets[(im['uuid'],rec['offset'])]+=1
  elif len(matches)>1:key='unmatched:overlapping-snapshot-images'
  else:key='unmatched:no-snapshot-range'
 rec['classification']=key;modules[key]+=1;byepoch[ep][key]+=1
 if key.startswith('unmatched:'):unmatched.append(rec)
result={'input':str(p),'sha256_of_read_bytes':hashlib.sha256(raw).hexdigest(),'cut_at_reset_marker':cut>=0,'sample_count':len(records),'unparsed_sample_markers':text.count('livepc sample epoch=')-len(records),'modes':dict(collections.Counter(r['mode'] for r in records)),'pid_counts':dict(pids),'classification':dict(modules),'snapshots':snaps,'epochs':epochs,'by_epoch':{k:dict(v) for k,v in byepoch.items()},'cpu_counters':cpu,'cpu_observations':perfs,'candidate_offsets':[{'uuid':u,'offset':hex(o),'count':n} for (u,o),n in offsets.most_common()],'records':records,'limitations':['image snapshot is later and not atomic at sample time; changed status is excluded','image list can be truncated','logged samples can be limited, lost or torn','samples are not CPU time or representative time proportions','report-start timestamps are not precise sample time bounds; each CPU ring is read later','target identity updates can drop samples before logging; lost=0 is not complete capture']}
print(json.dumps(result,indent=2))
