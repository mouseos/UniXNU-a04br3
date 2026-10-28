#!/usr/bin/env python3
"""Check UUID matching and function interval assignment on a synthetic Mach-O."""
import json,pathlib,struct,subprocess,sys,tempfile
script=pathlib.Path(__file__).resolve().parents[1]/'summarize-livepc-functions.py'
with tempfile.TemporaryDirectory() as tmp:
 p=pathlib.Path(tmp); binary=p/'fake.macho'; profile=p/'profile.json';uuid='12'*16
 header=struct.pack('<IiiIIIII',0xfeedfacf,0,0,0,3,112,0,0)
 segment=struct.pack('<II16sQQQQiiII',0x19,72,b'__TEXT',0,0x1000,0,0,0,0,0,0)
 ident=struct.pack('<II',0x1b,24)+bytes.fromhex(uuid)
 starts=struct.pack('<IIII',0x26,16,144,3)
 binary.write_bytes(header+segment+ident+starts+bytes([0x10,0x10,0]))
 profile.write_text(json.dumps({'records':[{'epoch':1,'candidate_uuid':uuid,'offset':0x15}]}))
 def run(u):return subprocess.run([sys.executable,str(script),str(profile),str(binary),u],capture_output=True,text=True)
 r=run(uuid);assert r.returncode==0,r.stderr;out=json.loads(r.stdout);assert out['functions']==[{'start':'0x10','end_exclusive':'0x20','samples':1}]
 assert out['candidate_samples']==1 and out['unclassified_samples']==0
 assert run('34'*16).returncode!=0
 nonzero=struct.pack('<II16sQQQQiiII',0x19,72,b'__TEXT',0x1000,0x1000,0,0,0,0,0,0)
 binary.write_bytes(header+nonzero+ident+starts+bytes([0x10,0x10,0]));assert json.loads(run(uuid).stdout)['functions'][0]['start']=='0x10'
 binary.write_bytes(header+segment+ident+starts+bytes([0x10,0x10,0x80]));assert run(uuid).returncode!=0
 binary.write_bytes(header+segment+ident+struct.pack('<IIII',0x26,16,144,11)+bytes([0x80]*10+[0]));assert run(uuid).returncode!=0
 binary.write_bytes(header+segment+ident+starts+bytes([0x81,0x20,0]));assert run(uuid).returncode!=0
 binary.write_bytes(header+segment+ident+starts+bytes([0x10,0x10,0]));profile.write_text(json.dumps({'records':[{'epoch':1,'candidate_uuid':uuid,'offset':0x21}]}));assert json.loads(run(uuid).stdout)['unclassified_samples']==1
 binary.write_bytes(header+segment+ident+struct.pack('<IIII',0x26,16,10000,3));assert run(uuid).returncode!=0
print('PASS: function start ranges, UUID mismatch and bad offset')
