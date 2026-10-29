#!/usr/bin/env python3
"""Build an explicitly non-distributable diagnostic payload from workflow inputs.

The ordinary a04br3 pre-build must have generated data.dbr, trustcache and
owner-data/seed.json in RUNNER_TEMP. UNIQUE_CHIP_ID is the workflow's
software identity secret, not an extracted device calibration value. Never
use this diagnostic payload as a distribution image: runtime per-device
identity acquisition remains a separate requirement.
"""
import argparse,hashlib,json,os,pathlib,subprocess,sys,uuid

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def checked_args(line):
 args=line.split(); required=('sheng-smp=1','cc=1','gi=6','gs=1','native-io-trace=1')
 if len(line.encode())+1>1024 or any(args.count(x)!=1 for x in required):raise ValueError('required boot arguments or length invalid')
 forbidden=('armcpu-cycle-clock','armcpu-runtime-cycle','arc','a04br3-gpu-stage','a04br3-gio','a04br3-gpu-io-trace')
 if any(any(t.split('=',1)[0]==n for t in args) for n in forbidden):raise ValueError('conflicting long or runtime diagnostic argument')
 return len(line.encode())+1

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--device',type=pathlib.Path,default=pathlib.Path(os.environ.get('DEVICE_DIR','.')))
 parser.add_argument('--output',type=pathlib.Path,default=None)
 args=parser.parse_args();root=pathlib.Path.cwd().resolve();device=args.device.resolve(strict=True)
 runner=pathlib.Path(os.environ['RUNNER_TEMP']).resolve(strict=True);out=(args.output or runner/'a04br3-recorded-diagnostic').resolve()
 if out.exists():raise ValueError('output already exists')
 if not (root/'unixnu').is_file():raise ValueError('run from UniXNU source root')
 data=runner/'data.dbr';trust=runner/'trustcache';seed=runner/'owner-data/seed.json';kernel=root/'out/a04br3/kernel/kernel.macho'
 for p in (data,trust,seed,kernel):
  if not p.is_file():raise ValueError('missing official build input: '+str(p))
 identity=os.environ.get('UNIQUE_CHIP_ID') or os.environ.get('UNIXNU_UNIQUE_CHIP_ID')
 if not identity:raise ValueError('workflow identity seed missing')
 vol=str(uuid.UUID(json.loads(seed.read_text())['volume_uuid'])).upper()
 if not uuid.UUID(vol).int or kernel.read_bytes().count(vol.encode())!=1:raise ValueError('kernel and pre-build Data volume identity do not match')
 def git(path,*a):return subprocess.check_output(['git','-C',str(path),*a],text=True).strip()
 source=git(root,'rev-parse','HEAD');revision=git(device,'rev-parse','HEAD');arm=git(device,'ls-tree','HEAD','drivers/ArmCpu').split()[2]
 if git(device/'drivers/ArmCpu','rev-parse','HEAD')!=arm:raise ValueError('ArmCpu gitlink mismatch')
 for repo in (root,device,device/'drivers/ArmCpu'):
  if git(repo,'status','--porcelain','--untracked-files=no','--ignore-submodules=all'):raise ValueError('tracked source changes: '+str(repo))
 env=dict(os.environ,UNIXNU_UNIQUE_CHIP_ID=identity)
 cmd=[str(root/'unixnu'),'build','payload','-D',str(device),'--out',str(out),'--kernel',str(kernel),'--data-ramdisk',str(data),'--owner-trust-cache',str(trust)]
 for v in ('sheng-fb-gpu=1','a04br3-charger-owner=1','sheng-vsync=60','vm_compressor=2','a04br3-home-perf=1','pl=1','gi=6','gs=1','cc=1','native-msdc-irq=1','a04br3-gpu-clock=380','native-io-trace=1','debug=334','a04br3-autoreset=720'):cmd+=['--kernel-arg='+v]
 for v in ('a04br3-stacklog','boot-ui-focus3','boot-ui-focus4','a04br3-gpu-packet','offline-hid-auto-home-count','offline-hid-auto-home','a04br3-powervrprobed','-no_corpses','-v','a04br3-proclog'):cmd+=['--omit-kernel-arg='+v]
 subprocess.run(cmd,cwd=root,env=env,check=True)
 p=out/'a04br3/payload';meta=json.loads((p/'payload.json').read_text());length=checked_args(meta['kernel_args'])
 adt=p/'stage/EFI/UniXNU/Payloads/kernel.macho.adt';payload=p/'payload.img'
 for key,file in (('kernel_sha256',kernel),('data_ramdisk_sha256',data),('trust_cache_sha256',trust),('adt_sha256',adt),('payload_sha256',payload)):
  if meta[key]!=sha(file):raise ValueError('generated artifact hash mismatch: '+key)
 if git(root,'rev-parse','HEAD')!=source or git(device,'rev-parse','HEAD')!=revision:raise ValueError('source changed during payload build')
 record={'schema':1,'purpose':'device-diagnostic-only; not a distributable image','source_commit':source,'device_commit':revision,'armcpu_commit':arm,'generated_adt_sha256':sha(adt),'kernel_sha256':sha(kernel),'data_sha256':sha(data),'data_seed_manifest_sha256':sha(seed),'trust_sha256':sha(trust),'payload_sha256':sha(payload),'kernel_args':meta['kernel_args'],'kernel_args_bytes_with_nul':length,'software_identity_source':'workflow secret, generated ADT; value omitted','hardware_tested':False}
 (out/'diagnostic-payload-manifest.json').write_text(json.dumps(record,indent=2)+'\n');print('Recorded diagnostic payload:',payload)
if __name__=='__main__':main()
