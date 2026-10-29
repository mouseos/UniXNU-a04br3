#!/usr/bin/env python3
import copy,importlib.util,json,tempfile
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'a04br3-verify-recorded-fd.py';spec=importlib.util.spec_from_file_location('fd',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as tmp:
 r=Path(tmp);uefi=r/'uefi';unixnu=r/'unixnu';(uefi/'Resources/MuPatches').mkdir(parents=True);fd=r/'fd';fd.write_bytes(b'X'*0x300000)
 patches=('AsmMacroLib.patch','Auth-Service.patch','Boot-Manager.patch','Timer.patch','Usb-Bus.patch')
 for n in patches:(uefi/'Resources/MuPatches'/n).write_text(n)
 refs={'Common/Mu':'a','Common/Mu_OEM_Sample':'b','Mu_Basecore':'c'}
 record={'schema':1,'target':'DEBUG','toolchain':'CLANGPDB','command':['python3','build_uefi.py','-d','a04br3','-r','DEBUG','-c'],'defines':{'A04BR3_TEST_CYCLE':'1','A04BR3_CPU_STAGE_DIAG':'1','A04BR3_CHARGER_OWNER':'1'},'fd_bytes':0x300000,'fd_sha256':m.digest(fd),'uefi_commit':'source','unixnu_commit':'unixnu','darwinboot_commit':'darwin','mu_basecore_patched_tree':'tree','source_date_epoch':'123','patch_sha256':{n:m.digest(uefi/'Resources/MuPatches'/n) for n in patches},'submodules':refs}
 def git(path,*args):
  if args[0]=='status':return ''
  if args[0]=='log':return '123'
  if args[0]=='ls-tree':return '160000 commit '+('darwin' if args[-1]=='drivers/DarwinBoot' else refs[args[-1]])+'\t'+args[-1]
  if args[0]=='rev-parse':return 'source' if path==uefi else 'unixnu' if path==unixnu else 'darwin' if path==unixnu/'drivers/DarwinBoot' else refs[str(path.relative_to(uefi))]
  raise AssertionError(args)
 assert m.check(record,fd,uefi,unixnu,git,lambda path,expected:'tree')==record['fd_sha256']
 for key,value in (('uefi_commit','wrong'),('source_date_epoch','0'),('target','RELEASE'),('fd_sha256','bad'),('defines',{}),('submodules',{})):
  bad=copy.deepcopy(record);bad[key]=value
  try:m.check(bad,fd,uefi,unixnu,git,lambda path,expected:'tree')
  except ValueError:pass
  else:raise AssertionError('accepted bad '+key)
 (uefi/'Resources/MuPatches'/patches[0]).write_text('tampered')
 try:m.check(record,fd,uefi,unixnu,git,lambda path,expected:'tree')
 except ValueError:pass
 else:raise AssertionError('accepted tampered patch')
print('PASS FD provenance, source, config, patch, submodule and hash refusal')
