from pathlib import Path
import sys,json,tomllib,subprocess,os,struct,tempfile,hashlib
import argparse
parser = argparse.ArgumentParser(description="Compare schema2 archives/config/ADT with the retained schema1 contract")
parser.add_argument('--unixnu', type=Path, required=True)
parser.add_argument('--soc', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
options = parser.parse_args()
B=options.unixnu.resolve();D=Path(__file__).resolve().parents[2];S=options.soc.resolve();L=options.out.resolve();L.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(B/'scripts'))
from unixnu.cli import parser
from unixnu.config import Context
from unixnu import feeds,kernel,devicetree,patches
args=['status','-D',str(D),'--local-module','soc:mt8167='+str(S),'--out',str(L)]
from unixnu.cli import cmd_defconfig
init_args=['defconfig','a04br3_defconfig','-D',str(D),'--local-module','soc:mt8167='+str(S),'--out',str(L)]
cmd_defconfig(Context(parser().parse_args(init_args)))
ctx=Context(parser().parse_args(args));mods=feeds.resolve(ctx);old=tomllib.loads((D/'migration/board-schema1.toml').read_text())
plan=kernel.archive_plan(ctx);expanded=[' '.join(entry['build']) if kind=='component' else entry for kind,entry in plan]
print('archive counts',len(old['kernel']['archives']),len(expanded));print('archive differences',[(i,a,b) for i,(a,b) in enumerate(zip(old['kernel']['archives'],expanded)) if a!=b]);assert expanded==old['kernel']['archives']
from unixnu.config import parse_config
oldcfg=parse_config((D/'configs/a04br3_defconfig').read_text());cfg={k:(v.replace(str(ctx.tree),'{tree}') if isinstance(v,str) else v) for k,v in ctx.config.items()};diff={k:[oldcfg.get(k),cfg.get(k)] for k in oldcfg.keys()|cfg.keys() if oldcfg.get(k)!=cfg.get(k)};print('config differences',diff);assert not diff
# Actual generator comparison: deterministic entropy only in this host process.
dtb=devicetree.compile_hardware(ctx)
def parse(data):
 def take(at):
  np,nc=struct.unpack_from('<II',data,at);at+=8;p={}
  for _ in range(np):
   name,length=struct.unpack_from('<32sI',data,at);at+=36;name=name.rstrip(b'\0').decode();assert name not in p;p[name]=data[at:at+length];at+=(length+3)&~3
  children={}
  for _ in range(nc):
   child,at=take(at);name=child['properties']['name'].rstrip(b'\0').decode();assert name not in children;children[name]=child
  return {'properties':p,'children':children},at
 result,end=take(0);assert end==len(data);return result
worker=r'''import sys,runpy,os
os.urandom=lambda n:bytes((i%251 for i in range(n)))
p=sys.argv[1];sys.argv=[p,*sys.argv[2:]];runpy.run_path(p,run_name='__main__')'''
orig_random=os.urandom;os.urandom=lambda n:bytes((i%251 for i in range(n)))
try:
 with tempfile.TemporaryDirectory() as temp:
  for data,trust,addon in [(False,False,False),(True,True,True)]:
   path=Path(temp)/'old.adt';a=['--output',str(path),'--dram-size','0x80000000','--owned-memory-size','0x54000000','--cpu-regs','0,1,2,3','--psci-method','smc','--ipad-identity','--unique-chip-id','123','--chip-id','8167','--root-match-volume-uuid',old['payload']['adt']['root_match_volume_uuid']]
   if data:a+=['--packed-data-ramdisk']
   if trust:a+=['--trust-cache']
   if addon:a+=['--gpu-addon']
   for fix in old['payload'].get('text_fixups',[]):a+=['--text-fixup',fix]
   subprocess.run(['python3','-c',worker,str(D/'scripts/make-a04br3-adt.py'),*a],check=True,capture_output=True)
   left=parse(path.read_bytes());right=parse(devicetree.generate(dtb,ctx.board,unique_chip_id='123',data=data,trust=trust,addon=addon))
   def walk(a,b,p=''):
    if a['properties']!=b['properties']:
     print('ADT property differences',p,{k:[a['properties'].get(k),b['properties'].get(k)] for k in a['properties'].keys()|b['properties'].keys() if a['properties'].get(k)!=b['properties'].get(k)})
    assert a['properties']==b['properties'],p
    assert a['children'].keys()==b['children'].keys(),(p,a['children'].keys(),b['children'].keys())
    for n in a['children']:walk(a['children'][n],b['children'][n],p+'/'+n)
   walk(left,right)
finally:os.urandom=orig_random
print('PASS exact ordered archive commands, normalized effective config, full ADT node/property semantics in two payload modes')
(L/'schema2-archive-plan.json').write_text(json.dumps(expanded,indent=2)+'\n');(L/'schema2-patch-plan.json').write_text(json.dumps([str(p) for p in patches.plan(ctx,mods)],indent=2)+'\n')
