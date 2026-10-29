#!/usr/bin/env python3
"""Create an addon from existing verified workflow binaries; never re-sign them."""
import hashlib,importlib.util,json,os,pathlib,plistlib,struct,subprocess,sys,tempfile,uuid
ROOT=pathlib.Path(__file__).resolve().parents[1]
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def trust_hashes(blob,helper):
 if not helper.valid_like_the_loader(blob):raise ValueError('invalid existing trust bundle')
 count=struct.unpack_from('<I',blob,0)[0]; offsets=list(struct.unpack_from('<%dI'%count,blob,4))
 values=set()
 for start in offsets:
  n=struct.unpack_from('<I',blob,start+20)[0]
  for i in range(n):values.add(blob[start+24+24*i:start+44+24*i].hex())
 return values
def verify_seed(root,data,seed,files):
 if sys.platform!='darwin':raise RuntimeError('read-only Data seed verification requires macOS')
 sys.path.insert(0,str(root/'drivers/DarwinBoot/Test'));import packed_ramdisk
 logical,extents=packed_ramdisk.regions(data.read_bytes())
 with tempfile.TemporaryDirectory(prefix='a04br3-gpu-seed-check-') as temp:
  raw=pathlib.Path(temp)/'seed.raw';mount=pathlib.Path(temp)/'mount';mount.mkdir()
  with raw.open('wb') as f:
   f.truncate(logical)
   for offset,chunk in extents:f.seek(offset);f.write(chunk)
  result=plistlib.loads(subprocess.check_output(['hdiutil','attach','-readonly','-imagekey','diskimage-class=CRawDiskImage','-nobrowse','-owners','on','-mountpoint',str(mount),'-plist',str(raw)]))
  device=result['system-entities'][0]['dev-entry']
  try:
   info=plistlib.loads(subprocess.check_output(['diskutil','info','-plist',str(mount)]))
   if uuid.UUID(info['VolumeUUID'])!=uuid.UUID(json.loads(seed.read_text())['volume_uuid']):raise ValueError('mounted seed UUID mismatch')
   for relative,_,content in files:
    if (mount/'ngpu'/relative).read_bytes()!=content:raise ValueError('seed GPU file differs: '+relative)
   if (mount/'ngpu/addons/NativePowerVRMetal.publish').read_text().strip()!='1':raise ValueError('GPU publish marker missing')
  finally:subprocess.run(['hdiutil','detach',device],check=True)
def prepare(root,runner,output):
 addon_helper=load('addon',ROOT/'scripts/make-a04br3-gpu-addon.py');probe_helper=load('probe',ROOT/'scripts/build-a04br3-powervr-probe.py')
 probe=runner/'powervr-probe';metal=runner/'powervr-metal';trust=runner/'trustcache';data=runner/'data.dbr';seed=runner/'owner-data/seed.json';report=runner/'data-probe.json'
 seed_report=json.loads(report.read_text())
 if seed_report.get('metal_mode')!='publish' or seed_report['output']['encoded_sha256']!=sha(data):raise ValueError('Data seed provenance or GPU publish mode differs')
 files=addon_helper.collect(probe,metal);names=[n for n,_,_ in files]
 needed={'bin/powervrprobed','addons/NativePowerVRVulkan.bundle/libvulkan_powervr_mesa.dylib','addons/NativePowerVRMetal.bundle/NativePowerVRMetal','addons/NativePowerVRMetal.bundle/libvulkan_powervr_mesa.dylib','addons/NativePowerVRMetal.bundle/Info.plist'}
 if len(set(names))!=len(names) or not needed.issubset(names):raise ValueError('required GPU addon files missing')
 for n in needed:
  if '/private/var/ngpu/'+n not in seed_report['staged']:raise ValueError('seed staging record lacks '+n)
 signed=set(addon_helper.verified_hashes(probe,metal));trusted=trust_hashes(trust.read_bytes(),probe_helper)
 if not signed.issubset(trusted):raise ValueError('GPU binary signatures absent from trust cache')
 verify_seed(root,data,seed,files)
 blob=probe_helper.pack_addon(files)
 if probe_helper.unpack_addon(blob)!=files:raise ValueError('GPU addon roundtrip failed')
 if output.exists():raise ValueError('refuse to replace GPU addon')
 output.write_bytes(blob)
 return {'addon_sha256':sha(output),'seed_staging_manifest_sha256':sha(report),'signed_cdhashes':sorted(signed),'files':{n:hashlib.sha256(content).hexdigest() for n,_,content in files},'data_seed_verified_read_only':True}
