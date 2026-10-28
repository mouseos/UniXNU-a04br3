#!/usr/bin/env python3
"""Check process identity, snapshot ambiguity and cross-boot failure."""
import json,pathlib,subprocess,sys,tempfile
script=pathlib.Path(__file__).resolve().parents[1]/'summarize-livepc.py'
head='livepc boot=AAAA pid=1 epoch=1 previous_report_start_ns=10 report_start_ns=20\n'
snap='livepc images epoch=1 pid=1 uid=2 exec_version=3 start_ns=21 end_ns=22 info=0 count=1 captured=1 truncated=0 detail_incomplete=0 status=partial\n'
image='livepc image epoch=1 index=0 dyld=0 mapping_at_sample=unknown load=1000 text_vm=0 text_size=100 text_valid=1 macho=1 detail_complete=1 uuid_valid=1 uuid=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n'
sample='livepc sample epoch=1 cpu=0 seq=0 pid=1 uid=2 exec_version=3 tid=4 mode=user pc=1010 lr=0\n'
ring='livepc cpu=0 epoch=1 ticks=16 end=1 lost=0 torn=0\n'+''.join(f'livepc cpu={i} epoch=1 ticks=0 end=0 lost=0 torn=0\n' for i in (1,2,3))
with tempfile.TemporaryDirectory() as d:
 p=pathlib.Path(d)/'uart';
 def run(data):
  p.write_text(data+ring);return subprocess.run([sys.executable,str(script),str(p)],capture_output=True,text=True)
 r=run(head+snap+image+sample);assert r.returncode==0,r.stderr;data=json.loads(r.stdout);assert data['classification']=={'candidate_uuid:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa':1} and data['records'][0]['offset']==16
 r=run(head+snap+image+sample.replace('exec_version=3','exec_version=4'));assert json.loads(r.stdout)['classification']=={'unmatched:missing-or-different-snapshot':1}
 r=run(head+snap+image+image.replace('index=0','index=1')+sample);assert json.loads(r.stdout)['classification']=={'unmatched:overlapping-snapshot-images':1}
 assert run(head+snap+image+sample+sample).returncode!=0
 assert run(head.replace('report_start_ns=20','report_start_ns=20GARBAGE')+snap+image+sample).returncode!=0
 assert run(head+snap.replace('start_ns=21 end_ns=22','start_ns=22 end_ns=21')+image+sample).returncode!=0
 assert run(head+snap+image+image.replace('load=1000','load=2000')+sample).returncode!=0
 p.write_text(head+snap+image+sample+ring.replace('torn=0','torn=0BROKEN',1));assert subprocess.run([sys.executable,str(script),str(p)],capture_output=True).returncode!=0
 assert run(head+snap+image+sample.replace('seq=0','seq=2')).returncode!=0
 p.write_text(head+snap+image+sample+ring.replace('lost=0','lost=1',1));assert subprocess.run([sys.executable,str(script),str(p)],capture_output=True).returncode!=0
 assert run(head+snap+image+sample.replace('lr=0','lr=0GARBAGE')).returncode!=0
 assert run(head+snap+image+sample+sample.replace('cpu=0','cpu=4')).returncode!=0
 assert json.loads(run(head+snap.replace('status=partial','status=complete')+image+sample).stdout)['classification']=={'candidate_uuid:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa':1}
 assert run(head+snap+snap+image+sample).returncode!=0
 assert run(head+snap+image+sample.replace('pc=1010','pc=xyz')).returncode!=0
 assert run(head+snap+image.replace('uuid=aaaa','uuid=aaaaZ')+sample).returncode!=0
 r=run(head+snap.replace('status=partial','status=changed')+image+sample);assert json.loads(r.stdout)['classification']=={'unmatched:missing-or-different-snapshot':1}
 p.write_text(head+snap+image+sample+ring.replace('livepc cpu=2 epoch=1 ticks=0 end=0 lost=0 torn=0\n',''));assert subprocess.run([sys.executable,str(script),str(p)],capture_output=True).returncode!=0
 p.write_text(head+snap+image+sample+ring+head.replace('epoch=1','epoch=2').replace('previous_report_start_ns=10 report_start_ns=20','previous_report_start_ns=19 report_start_ns=30'));assert subprocess.run([sys.executable,str(script),str(p)],capture_output=True).returncode!=0
 assert run(head+head+snap+image+sample).returncode!=0
 assert run(head+snap+image+sample+head.replace('boot=AAAA','boot=BBBB').replace('epoch=1','epoch=2')).returncode!=0
 r=run(head+snap+image+sample+ring+'a04br3-autoreset: reset requested on UART\n'+head);assert r.returncode==0 and json.loads(r.stdout)['cut_at_reset_marker']
print('PASS: identity, overlapping maps and boot boundary safeguards')
