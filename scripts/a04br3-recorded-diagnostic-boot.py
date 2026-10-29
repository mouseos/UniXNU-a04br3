#!/usr/bin/env python3
"""Pack a non-distributable diagnostic boot from recorded firmware and payload."""
import argparse,hashlib,importlib.util,json,os,pathlib,subprocess,sys,tempfile
DEVICE=pathlib.Path(__file__).resolve().parents[1]
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def git(path,*args):return subprocess.check_output(['git','-C',str(path),*args],text=True).strip()
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for name in ('unixnu','uefi','uefi-unixnu','fd-dir','payload-dir','kernel-manifest','output'):p.add_argument('--'+name,type=pathlib.Path,required=True)
 a=p.parse_args();root=a.unixnu.resolve(strict=True);uefi=a.uefi.resolve(strict=True);fwunixnu=a.uefi_unixnu.resolve(strict=True);fdir=a.fd_dir.resolve(strict=True);pdir=a.payload_dir.resolve(strict=True);km=a.kernel_manifest.resolve(strict=True);out=a.output.resolve()
 if out.exists():raise ValueError('refusing to overwrite output')
 payload=pdir/'payload.img';pm_path=pdir/'diagnostic-payload-manifest.json';pm=json.loads(pm_path.read_text());kp=json.loads(km.read_text());fm=json.loads((fdir/'manifest.json').read_text());fd=fdir/'SILICIUM_UEFI.fd'
 if pm['purpose']!='device-diagnostic-only; not a distributable image' or pm['hardware_tested'] is not False:raise ValueError('payload is not marked as untested diagnostic')
 if pm['payload_sha256']!=sha(payload) or pm['kernel_provenance_sha256']!=sha(km) or pm['kernel_sha256']!=kp['kernel_sha256']:raise ValueError('payload/kernel provenance mismatch')
 if git(root,'rev-parse','HEAD')!=pm['source_commit'] or kp['source_commit']!=pm['source_commit'] or kp['armcpu_commit']!=pm['armcpu_commit'] or kp['device_commit']!=pm['kernel_device_commit']:raise ValueError('source revision mismatch')
 dev=git(DEVICE,'rev-parse','HEAD');payload_dev=pm['packaging_device_commit']
 if git(DEVICE,'ls-tree','HEAD','drivers/ArmCpu').split()[2]!=pm['armcpu_commit']:raise ValueError('ArmCpu gitlink mismatch')
 if dev!=payload_dev:
  subprocess.run(['git','-C',str(DEVICE),'merge-base','--is-ancestor',payload_dev,dev],check=True)
  allowed={'scripts/a04br3-verify-recorded-fd.py','scripts/tests/test_recorded_fd_validation.py','scripts/a04br3-recorded-diagnostic-boot.py','scripts/tests/test_recorded_diagnostic_boot.py'}
  if not set(git(DEVICE,'diff','--name-only',payload_dev,dev).splitlines()).issubset(allowed):raise ValueError('unexpected source changes since payload build')
 validator=load('fd_validator',DEVICE/'scripts/a04br3-verify-recorded-fd.py');builder=load('fd_builder',uefi/'Platforms/Benesse/a04br3Pkg/scripts/build-recorded-fd.py')
 validator.check(fm,fd,uefi,fwunixnu,git,builder.patched_tree)
 checks=load('payload_validator',DEVICE/'scripts/a04br3-recorded-diagnostic-payload.py');checks.checked_args(pm['kernel_args'],pm.get('runtime_window_requested',False),pm.get('pmuv3_active_requested',False),pm.get('runtime_repeat_requested',False))
 if pm.get('runtime_window_requested',False) and pm.get('runtime_pmu_owner_qualified') is not False:raise ValueError('runtime access qualification not recorded')
 if not pm['gpu']['data_seed_verified_read_only']:raise ValueError('GPU Data verification missing')
 helper=load('gpu_probe',DEVICE/'scripts/build-a04br3-powervr-probe.py')
 with tempfile.TemporaryDirectory(prefix='a04br3-payload-verify-') as temp:
  tmp=pathlib.Path(temp)
  mapping=(('kernel.macho.adt','generated_adt_sha256'),('kernel.macho','kernel_sha256'),('kernel.macho.gpuaddon',None),('kernel.macho.trustcache','trust_sha256'),('scratch-data.img','data_sha256'))
  for name,key in mapping:
   target=tmp/name;subprocess.run(['mcopy','-i',str(payload),'::/EFI/UniXNU/Payloads/'+name,str(target)],check=True,stdout=subprocess.DEVNULL)
   expected=pm['gpu']['addon_sha256'] if key is None else pm[key]
   if sha(target)!=expected:raise ValueError('payload FAT mismatch: '+name)
  files=helper.unpack_addon((tmp/'kernel.macho.gpuaddon').read_bytes());found={n:hashlib.sha256(data).hexdigest() for n,_,data in files}
  if found!=pm['gpu']['files']:raise ValueError('GPU addon contents differ from provenance')
  gpu=load('gpu_verify',DEVICE/'scripts/a04br3-prepare-diagnostic-gpu.py')
  if not set(pm['gpu']['signed_cdhashes']).issubset(gpu.trust_hashes((tmp/'kernel.macho.trustcache').read_bytes(),helper)):raise ValueError('GPU CDHashes absent from payload trust cache')
 os.umask(0o077);out.mkdir(parents=True)
 sys.path.insert(0,str(root/'scripts'))
 from unixnu import bootimg,cli
 from unixnu.config import Context
 ctx=Context(cli.parser().parse_args(['bootimg','-D',str(DEVICE),'--out',str(out),'--uefi',str(uefi)]));ctx.uefi=uefi
 dtb=uefi/'Resources/DTBs/a04br3.dtb';boot=out/'boot.img';bootimg.build(ctx,uefi,fd,dtb,payload,boot)
 subprocess.run([sys.executable,str(DEVICE/'scripts/check-a04br3-bootimg.py'),str(boot)],check=True)
 record={'schema':1,'purpose':'device-diagnostic-only; not a distributable image','source_commit':pm['source_commit'],'kernel_device_commit':pm['kernel_device_commit'],'payload_device_commit':payload_dev,'boot_device_commit':dev,'armcpu_commit':pm['armcpu_commit'],'uefi_commit':fm['uefi_commit'],'uefi_manifest_sha256':sha(fdir/'manifest.json'),'fd_sha256':sha(fd),'dtb_sha256':sha(dtb),'kernel_sha256':pm['kernel_sha256'],'generated_adt_sha256':pm['generated_adt_sha256'],'payload_manifest_sha256':sha(pm_path),'payload_sha256':sha(payload),'gpu_addon_sha256':pm['gpu']['addon_sha256'],'boot_sha256':sha(boot),'kernel_args':pm['kernel_args'],'kernel_args_bytes_with_nul':pm['kernel_args_bytes_with_nul'],'cpu_measurement':'experimental active runtime sampling requested; no hardware result; EL2/EL3/debug ownership unverified' if pm.get('pmuv3_active_requested',False) else 'secondary startup busy sample only; CPU0 and runtime not measured; runtime worker refuses all CPUs' if pm.get('runtime_window_requested',False) else 'secondary startup busy sample only; CPU0 and runtime not measured','runtime_window_requested':pm.get('runtime_window_requested',False),'runtime_repeat_requested':pm.get('runtime_repeat_requested',False),'pmuv3_active_requested':pm.get('pmuv3_active_requested',False),'runtime_pmu_owner_qualified':False,'ownership':'higher EL and debug unverified','hardware_tested':False}
 (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print('Recorded diagnostic boot:',boot)
if __name__=='__main__':main()
