import bisect,collections,json,pathlib,struct,sys
if len(sys.argv)!=4: raise SystemExit('usage: summarize-livepc-functions.py PROFILE MACHO UUID')
binary=pathlib.Path(sys.argv[2]); uuid=sys.argv[3].replace('-','').lower()
if len(uuid)!=32 or any(c not in '0123456789abcdef' for c in uuid): raise SystemExit('invalid image UUID')
b=binary.read_bytes();p=32;data=None;base=None;text_size=None;file_uuid=None
if len(b)<32 or struct.unpack_from('<I',b)[0]!=0xfeedfacf: raise SystemExit('not a little-endian Mach-O 64-bit file')
for _ in range(struct.unpack_from('<I',b,16)[0]):
 if p+8>len(b): raise SystemExit('truncated load command')
 c,z=struct.unpack_from('<II',b,p)
 if z<8 or p+z>len(b): raise SystemExit('invalid load command')
 if c==0x1b:
  if z<24 or file_uuid is not None: raise SystemExit('invalid or duplicate LC_UUID')
  file_uuid=b[p+8:p+24].hex()
 if c==0x26:
  if z<16 or data is not None: raise SystemExit('invalid or duplicate LC_FUNCTION_STARTS')
  data=struct.unpack_from('<II',b,p+8)
 if c==0x19 and b[p+8:p+24].rstrip(b'\0')==b'__TEXT':
  if z<72 or base is not None: raise SystemExit('invalid or duplicate __TEXT')
  base,text_size=struct.unpack_from('<QQ',b,p+24)
 p+=z
if file_uuid!=uuid: raise SystemExit('Mach-O UUID mismatch')
if not data or base is None or data[0]+data[1]>len(b): raise SystemExit('missing or invalid function starts')
addr=0;vals=[];v=shift=0;terminated=False
for x in b[data[0]:sum(data)]:
 v|=(x&127)<<shift
 if x&128:
  shift+=7
  if shift>=64: raise SystemExit('ULEB128 overflow')
  continue
 if not v: terminated=True;break
 addr+=v
 if addr>=text_size: raise SystemExit('function starts outside __TEXT')
 vals.append(addr);v=shift=0
if not terminated or shift: raise SystemExit('truncated function starts')
p=pathlib.Path(sys.argv[1]);d=json.loads(p.read_text());counter=collections.Counter(); per=collections.defaultdict(collections.Counter)
candidate_count=0;unclassified=0
for r in d['records']:
 if r.get('candidate_uuid')!=uuid:continue
 candidate_count+=1
 i=bisect.bisect_right(vals,r['offset'])-1
 if i<0 or i+1>=len(vals):
  unclassified+=1;continue
 k=(vals[i],vals[i+1]);counter[k]+=1;per[r['epoch']][k]+=1
print(json.dumps({'profile':str(p),'image':str(binary),'uuid':uuid,'candidate_samples':candidate_count,'classified_samples':candidate_count-unclassified,'unclassified_samples':unclassified,'source':'Mach-O LC_FUNCTION_STARTS: next start is an upper bound, not a confirmed function body','functions':[{'start':hex(a),'end_exclusive':hex(b),'samples':n} for (a,b),n in counter.most_common()],'per_epoch':{e:[{'start':hex(a),'end_exclusive':hex(b),'samples':n} for (a,b),n in c.most_common()] for e,c in per.items()}},indent=2))
