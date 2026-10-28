#!/usr/bin/env python3
"""Use pinned baseline with synthetic signed-bundle fixtures; no signature claim."""
import hashlib,io,json,os,runpy,tarfile,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[2]
m=runpy.run_path(str(root/'scripts/make-a04br3-pipeline-addon.py'))
h=runpy.run_path(str(root/'scripts/build-a04br3-powervr-probe.py'))
base=Path(os.environ['A04BR3_BASELINE'])
old=h['unpack_addon']((base/'kernel.macho.gpuaddon').read_bytes())
assert len(old)==6
info=next(d for n,_,d in old if n==m['PREFIX']+'Info.plist')
def archive(path,info_bytes=info):
 files={'trust.json':json.dumps({'cdhash':'12'*20,'driver_cdhash':'ab'*20}).encode(),
        'NativePowerVRMetal.bundle/Info.plist':info_bytes,
        'NativePowerVRMetal.bundle/NativePowerVRMetal':b'fixture PVR_PIPELINE_TIMING',
        'NativePowerVRMetal.bundle/_CodeSignature/CodeResources':b'new resource seal',
        'NativePowerVRMetal.bundle/libvulkan_powervr_mesa.dylib':b'fixture pvr_cache=not_queried'}
 with tarfile.open(path,'w:gz') as t:
  for n,v in files.items():
   x=tarfile.TarInfo(n);x.size=len(v);t.addfile(x,io.BytesIO(v))
 return json.loads(files['trust.json'])
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);a=p/'b.tar.gz';meta=archive(a)
 rec={'source_commit':m['SOURCE'],'device_commit':m['DEVICE'],'mesa_commit':m['MESA'],
      'bundle_signature_verified':True,'shader_capture_enabled':False,'pipeline_timing_enabled':True,
      'bundle_archive_sha256':hashlib.sha256(a.read_bytes()).hexdigest(),'metal_cdhashes':meta}
 prov=p/'provenance.json';prov.write_text(json.dumps(rec))
 report=m['build'](base,a,prov,p/'out',h)
 new=h['unpack_addon']((p/'out/gpuaddon').read_bytes())
 assert [(n,mode) for n,mode,_ in new]==[(n,mode) for n,mode,_ in old]
 changed=[n for (n,_,a1),(_,_,b1) in zip(old,new) if a1!=b1]
 assert set(changed)=={m['PREFIX']+x for x in ('NativePowerVRMetal','_CodeSignature/CodeResources','libvulkan_powervr_mesa.dylib')}
 assert report['data_seed_sha256']==m['PINS']['scratch-data.img']
 assert h['valid_like_the_loader']((p/'out/trustcache').read_bytes())
 rec['shader_capture_enabled']=True;prov.write_text(json.dumps(rec))
 try:m['build'](base,a,prov,p/'bad',h)
 except ValueError:pass
 else:raise AssertionError('capture-enabled provenance accepted')
 rec['shader_capture_enabled']=False;meta=archive(a,b'different plist');rec['metal_cdhashes']=meta;rec['bundle_archive_sha256']=hashlib.sha256(a.read_bytes()).hexdigest();prov.write_text(json.dumps(rec))
 try:m['build'](base,a,prov,p/'bad2',h)
 except ValueError:pass
 else:raise AssertionError('different Info.plist accepted')
print('PASS: six baseline entries/modes, three replacements, trust, capture gate and plist mismatch')
