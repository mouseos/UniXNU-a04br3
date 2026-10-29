#!/usr/bin/env python3
"""Build an explicitly non-distributable diagnostic payload from workflow inputs.

The ordinary a04br3 pre-build must have generated data.dbr, trustcache and
owner-data/seed.json in RUNNER_TEMP. UNIQUE_CHIP_ID is the workflow's
software identity secret, not an extracted device calibration value. Never
use this diagnostic payload as a distribution image: runtime per-device
identity acquisition remains a separate requirement.
"""
import argparse,hashlib,importlib.util,json,os,pathlib,subprocess,sys,tempfile,uuid

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def checked_args(line, runtime_window=False, pmuv3_active=False, runtime_repeat=False, runtime_context=False):
 args=line.split(); required=('sheng-smp=1','cc=1','gi=6','gs=1','native-io-trace=1','sheng-gpu-addon=1','a04br3-gpu-metal=1','sheng-fb-gpu=1')
 if len(line.encode())+1>1024 or any(args.count(x)!=1 or sum(t.split('=',1)[0]==x.split('=',1)[0] for t in args)!=1 for x in required):raise ValueError('required boot arguments or length invalid')
 forbidden=('armcpu-cycle-clock','armcpu-runtime-cycle','arc','a04br3-gpu-stage','a04br3-gio','a04br3-gpu-io-trace')
 if any(any(t.split('=',1)[0]==n for t in args) for n in forbidden):raise ValueError('conflicting long or runtime diagnostic argument')
 window=[t for t in args if t.split('=',1)[0] in ('arw','armcpu-runtime-window')]
 if runtime_repeat and not (runtime_window and pmuv3_active):raise ValueError('repeat requires active runtime window')
 if runtime_context and not runtime_repeat:raise ValueError('context requires repeated sampling')
 if window != (['arw=3' if runtime_context else 'arw=2' if runtime_repeat else 'arw=1'] if runtime_window else []):raise ValueError('unexpected runtime window argument')
 experiment=[t for t in args if t.split('=',1)[0]=='aq']
 if experiment != (['aq=1'] if pmuv3_active else []) or (pmuv3_active and not runtime_window):raise ValueError('unexpected PMUv3 experimental argument')
 return len(line.encode())+1

def checked_context_kernel(config, kernel_bytes, symbols):
 required=('CONFIG_XNU_ARMCPU_RUNTIME_WINDOW=y','CONFIG_XNU_ARMCPU_IDLE_EPOCH=y','CONFIG_XNU_PMUV3_ACTIVE=y')
 if any(config.splitlines().count(flag)!=1 for flag in required):raise ValueError('context kernel config missing active PMU options')
 for marker in (b'ArmCpu: runtime-scope',b'ArmCpu: runtime-context unavailable=board-hook',b'a04br3 cpu-context round='):
  if marker not in kernel_bytes:raise ValueError('context kernel marker missing')
 for sym in ('_a04br3_cpu_context_sample','_unixnu_pmuv3_active_start','_unixnu_pmuv3_active_stop','_unixnu_idle_epoch_observe'):
  rows=[line.split() for line in symbols.splitlines() if line.split() and line.split()[-1]==sym]
  if not any(len(row)>=4 and row[-3]=='(__TEXT_EXEC,__text)' and row[-2]=='external' for row in rows):raise ValueError('context kernel symbol not defined: '+sym)


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--device',type=pathlib.Path,default=pathlib.Path(os.environ.get('DEVICE_DIR','.')))
 parser.add_argument('--output',type=pathlib.Path,default=None)
 parser.add_argument('--kernel-manifest',type=pathlib.Path,required=True)
 parser.add_argument('--runtime-window',action='store_true',help='enable runtime window diagnostic')
 parser.add_argument('--runtime-context',action='store_true',help='record read-only clock and CPU load context around each repeated window')
 parser.add_argument('--runtime-repeat',action='store_true',help='repeat experimental active sampling over time')
 parser.add_argument('--pmuv3-active',action='store_true',help='enable experimental PMUv3 active sampling; upper-EL ownership unverified')
 args=parser.parse_args();
 if args.pmuv3_active and not args.runtime_window:parser.error('--pmuv3-active requires --runtime-window')
 if args.runtime_repeat and not (args.runtime_window and args.pmuv3_active):parser.error('--runtime-repeat requires --runtime-window and --pmuv3-active')
 if args.runtime_context and not args.runtime_repeat:parser.error('--runtime-context requires --runtime-repeat')
 root=pathlib.Path.cwd().resolve();device=args.device.resolve(strict=True)
 runner=pathlib.Path(os.environ['RUNNER_TEMP']).resolve(strict=True);out=(args.output or runner/'a04br3-recorded-diagnostic').resolve()
 if out.exists():raise ValueError('output already exists')
 if not (root/'unixnu').is_file():raise ValueError('run from UniXNU source root')
 data=runner/'data.dbr';trust=runner/'trustcache';seed=runner/'owner-data/seed.json';kernel=root/'out/a04br3/kernel/kernel.macho'
 for p in (data,trust,seed,kernel):
  if not p.is_file():raise ValueError('missing official build input: '+str(p))
 identity=os.environ.get('UNIQUE_CHIP_ID') or os.environ.get('UNIXNU_UNIQUE_CHIP_ID')
 if not identity:raise ValueError('workflow identity seed missing')
 if args.runtime_context:
  config=(root/'out/a04br3/.config').read_text()
  symbols=subprocess.check_output(['xcrun','nm','-m',str(kernel)],text=True)
  checked_context_kernel(config,kernel.read_bytes(),symbols)
 vol=str(uuid.UUID(json.loads(seed.read_text())['volume_uuid'])).upper()
 if not uuid.UUID(vol).int or kernel.read_bytes().count(vol.encode())!=1:raise ValueError('kernel and pre-build Data volume identity do not match')
 def git(path,*a):return subprocess.check_output(['git','-C',str(path),*a],text=True).strip()
 source=git(root,'rev-parse','HEAD');revision=git(device,'rev-parse','HEAD');arm=git(device,'ls-tree','HEAD','drivers/ArmCpu').split()[2]
 if git(device/'drivers/ArmCpu','rev-parse','HEAD')!=arm:raise ValueError('ArmCpu gitlink mismatch')
 provenance=json.loads(args.kernel_manifest.read_text());kernel_device=provenance['device_commit']
 if provenance['source_commit']!=source or provenance['armcpu_commit']!=arm or provenance['kernel_sha256']!=sha(kernel):raise ValueError('kernel provenance differs from build sources or bytes')
 if kernel_device!=revision:
  subprocess.run(['git','-C',str(device),'merge-base','--is-ancestor',kernel_device,revision],check=True)
  changed=set(git(device,'diff','--name-only',kernel_device,revision).splitlines())
  allowed={'scripts/a04br3-verify-recorded-fd.py','scripts/tests/test_recorded_fd_validation.py','scripts/a04br3-recorded-diagnostic-payload.py','scripts/a04br3-prepare-diagnostic-gpu.py','scripts/tests/test_prepare_diagnostic_gpu.py','scripts/tests/test_recorded_diagnostic_payload.py','scripts/a04br3-recorded-diagnostic-boot.py'}
  if not changed or not changed.issubset(allowed):raise ValueError('device changes since kernel build affect its source')
 for repo in (root,device,device/'drivers/ArmCpu'):
  if git(repo,'status','--porcelain','--untracked-files=no','--ignore-submodules=all'):raise ValueError('tracked source changes: '+str(repo))
 scratch=tempfile.TemporaryDirectory(prefix='a04br3-recorded-gpu-')
 spec=importlib.util.spec_from_file_location('diagnostic_gpu',device/'scripts/a04br3-prepare-diagnostic-gpu.py');gpu=importlib.util.module_from_spec(spec);spec.loader.exec_module(gpu)
 addon=pathlib.Path(scratch.name)/'gpuaddon';gpu_record=gpu.prepare(root,runner,addon)
 env=dict(os.environ,UNIXNU_UNIQUE_CHIP_ID=identity)
 cmd=[str(root/'unixnu'),'build','payload','-D',str(device),'--out',str(out),'--kernel',str(kernel),'--data-ramdisk',str(data),'--owner-trust-cache',str(trust),'--gpu-addon',str(addon)]
 for v in ('sheng-fb-gpu=1','a04br3-charger-owner=1','sheng-vsync=60','vm_compressor=2','a04br3-home-perf=1','pl=1','gi=6','gs=1','cc=1','native-msdc-irq=1','a04br3-gpu-clock=380','native-io-trace=1','debug=334','a04br3-autoreset=720'):cmd+=['--kernel-arg='+v]
 if args.runtime_window:cmd+=['--kernel-arg=arw=3' if args.runtime_context else '--kernel-arg=arw=2' if args.runtime_repeat else '--kernel-arg=arw=1']
 if args.pmuv3_active:cmd+=['--kernel-arg=aq=1']
 for v in ('a04br3-stacklog','boot-ui-focus3','boot-ui-focus4','a04br3-gpu-packet','offline-hid-auto-home-count','offline-hid-auto-home','a04br3-powervrprobed','-no_corpses','-v','a04br3-proclog'):cmd+=['--omit-kernel-arg='+v]
 # UniXNU logs its ADT generator command. Keep the workflow identity out of
 # the build log as well as the public manifest.
 secrets={identity,hex(int(identity,16))}
 with subprocess.Popen(cmd,cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1) as process:
  for line in process.stdout:
   for secret in sorted(secrets,key=len,reverse=True):line=line.replace(secret,'<diagnostic-identity>')
   sys.stdout.write(line);sys.stdout.flush()
  if process.wait():raise RuntimeError('official payload build failed')
 p=out/'a04br3/payload';meta=json.loads((p/'payload.json').read_text());length=checked_args(meta['kernel_args'],args.runtime_window,args.pmuv3_active,args.runtime_repeat,args.runtime_context)
 payload=p/'payload.img'
 for key,file in (('kernel_sha256',kernel),('data_ramdisk_sha256',data),('trust_cache_sha256',trust),('gpu_addon_sha256',addon),('payload_sha256',payload)):
  if meta[key]!=sha(file):raise ValueError('generated artifact hash mismatch: '+key)
 # The official builder removes stage after writing FAT. Verify what it wrote,
 # rather than relying on files that no longer exist on disk.
 extracted=pathlib.Path(scratch.name)/'extracted';extracted.mkdir()
 for name,key in (('kernel.macho.adt','adt_sha256'),('kernel.macho','payload_kernel_sha256'),('kernel.macho.gpuaddon','gpu_addon_sha256'),('kernel.macho.trustcache','trust_cache_sha256'),('scratch-data.img','data_ramdisk_sha256')):
  target=extracted/name
  subprocess.run(['mcopy','-i',str(payload),'::/EFI/UniXNU/Payloads/'+name,str(target)],check=True,stdout=subprocess.DEVNULL)
  if sha(target)!=meta[key]:raise ValueError('FAT payload file hash mismatch: '+name)
 adt_sha=meta['adt_sha256']
 if git(root,'rev-parse','HEAD')!=source or git(device,'rev-parse','HEAD')!=revision:raise ValueError('source changed during payload build')
 record={'schema':1,'purpose':'device-diagnostic-only; not a distributable image','source_commit':source,'kernel_device_commit':kernel_device,'packaging_device_commit':revision,'kernel_provenance_sha256':sha(args.kernel_manifest),'armcpu_commit':arm,'generated_adt_sha256':adt_sha,'kernel_sha256':sha(kernel),'data_sha256':sha(data),'data_seed_manifest_sha256':sha(seed),'trust_sha256':sha(trust),'gpu':gpu_record,'payload_sha256':sha(payload),'kernel_args':meta['kernel_args'],'kernel_args_bytes_with_nul':length,'software_identity_source':'workflow secret, generated ADT; value omitted','runtime_window_requested':args.runtime_window,'runtime_repeat_requested':args.runtime_repeat,'runtime_context_requested':args.runtime_context,'pmuv3_active_requested':args.pmuv3_active,'pmuv3_experiment_assumption':'EL2/EL3/debug and recovery unverified','runtime_pmu_owner_qualified':False,'hardware_tested':False}
 (out/'diagnostic-payload-manifest.json').write_text(json.dumps(record,indent=2)+'\n');print('Recorded diagnostic payload:',payload)
if __name__=='__main__':main()
