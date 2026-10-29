#!/usr/bin/env python3
"""Check a recorded source-built a04br3 FD before packing a boot image."""
import argparse,hashlib,json,pathlib,subprocess,sys

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def check(record,fd,uefi,git):
 if record.get('schema')!=1 or record.get('target')!='DEBUG' or record.get('toolchain')!='CLANGPDB':raise ValueError('unexpected FD build configuration')
 if record.get('command')!=['python3','build_uefi.py','-d','a04br3','-r','DEBUG','-c']:raise ValueError('unexpected FD build command')
 if record.get('defines')!={'A04BR3_TEST_CYCLE':'1','A04BR3_CPU_STAGE_DIAG':'1','A04BR3_CHARGER_OWNER':'1'}:raise ValueError('unexpected FD defines')
 if not fd.is_file() or fd.stat().st_size!=0x300000 or record.get('fd_bytes')!=0x300000 or digest(fd)!=record.get('fd_sha256'):raise ValueError('FD bytes do not match manifest')
 if git(uefi,'rev-parse','HEAD')!=record.get('uefi_commit'):raise ValueError('UEFI source commit differs')
 if git(uefi,'log','-1','--format=%ct')!=record.get('source_date_epoch'):raise ValueError('UEFI source timestamp differs')
 if git(uefi,'status','--porcelain','--untracked-files=no','--ignore-submodules=all'):raise ValueError('UEFI tracked source is dirty')
 patches=record.get('patch_sha256',{});expected={'AsmMacroLib.patch','Auth-Service.patch','Boot-Manager.patch','Timer.patch','Usb-Bus.patch'}
 if set(patches)!=expected:raise ValueError('FD patch set differs')
 for name in expected:
  if digest(uefi/'Resources/MuPatches'/name)!=patches[name]:raise ValueError('FD patch hash differs: '+name)
 subs=record.get('submodules',{});needed={'Common/Mu','Common/Mu_OEM_Sample','Mu_Basecore'}
 if set(subs)!=needed:raise ValueError('FD submodule set differs')
 for name in needed:
  if git(uefi,'ls-tree','HEAD',name).split()[2]!=subs[name] or git(uefi/name,'rev-parse','HEAD')!=subs[name]:raise ValueError('FD submodule differs: '+name)
 return record['fd_sha256']

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fd',type=pathlib.Path,required=True);p.add_argument('--manifest',type=pathlib.Path,required=True);p.add_argument('--uefi',type=pathlib.Path,required=True);a=p.parse_args()
 def git(path,*args):return subprocess.check_output(['git','-C',str(path),*args],text=True).strip()
 try:print('PASS recorded FD provenance:',check(json.loads(a.manifest.read_text()),a.fd,a.uefi.resolve(strict=True),git))
 except (OSError,ValueError,KeyError,IndexError,subprocess.CalledProcessError) as e:raise SystemExit('FD provenance verification failed: '+str(e))
if __name__=='__main__':main()
