#!/usr/bin/env python3
"""Compose the original six-entry GPU addon with a verified timing bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    'scratch-data.img': '1013af9803111b6898e47090c483e5b5a84bf5f451827c21fc257d7a2cbd258f',
    'kernel.macho.gpuaddon': '35a77e01114467f0f9d4c16fbfa24e76903968ff68e8d82812acd2980ee00fa3',
    'kernel.macho.trustcache': '7123f78d892a993771586eebf27a76abdcd2bf68afe60bc97b7002c49df8dc1c',
}
SOURCE = 'b9824adabfa7206dc07978f183474960856085fc'
DEVICE = '0482e1b087fc7bd49d34007acb24f39f1dfa057e'
MESA = '0c0bdbd5a4f9f16d79666211080e5948debfe3a2'
PREFIX = 'addons/NativePowerVRMetal.bundle/'
NAMES = ('Info.plist', 'NativePowerVRMetal', '_CodeSignature/CodeResources', 'libvulkan_powervr_mesa.dylib')

def sha(data): return hashlib.sha256(data).hexdigest()

def build(baseline, archive, provenance, out, helper):
    original = {n: (baseline/n).read_bytes() for n in PINS}
    for name, expected in PINS.items():
        if sha(original[name]) != expected: raise ValueError('baseline hash mismatch: '+name)
    record = json.loads(provenance.read_text())
    if (record.get('source_commit'),record.get('device_commit'),record.get('mesa_commit')) != (SOURCE,DEVICE,MESA):
        raise ValueError('source provenance mismatch')
    if record.get('bundle_signature_verified') is not True or record.get('shader_capture_enabled') is not False or record.get('pipeline_timing_enabled') is not True:
        raise ValueError('signature or diagnostic provenance missing')
    if sha(archive.read_bytes()) != record.get('bundle_archive_sha256'): raise ValueError('bundle archive hash mismatch')
    files = {}
    allowed = {'trust.json', *('NativePowerVRMetal.bundle/'+n for n in NAMES)}
    with tarfile.open(archive,'r:gz') as tar:
        for item in tar:
            if item.isdir(): continue
            if not item.isfile() or item.name not in allowed or item.name in files or item.size > 100*1024*1024:
                raise ValueError('invalid bundle archive member: '+item.name)
            handle=tar.extractfile(item)
            if handle is None: raise ValueError('unreadable bundle member')
            files[item.name]=handle.read()
    if set(files) != allowed: raise ValueError('bundle archive member set mismatch')
    trust_meta=json.loads(files['trust.json'])
    if trust_meta != record.get('metal_cdhashes'): raise ValueError('signed bundle metadata differs')
    hashes=[]
    for key in ('cdhash','driver_cdhash'):
        value=trust_meta.get(key)
        if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{40}',value): raise ValueError('invalid '+key)
        hashes.append(value)
    executable=files['NativePowerVRMetal.bundle/NativePowerVRMetal']
    driver=files['NativePowerVRMetal.bundle/libvulkan_powervr_mesa.dylib']
    if b'PVR_PIPELINE_TIMING' not in executable or b'pvr_cache=not_queried' not in driver:
        raise ValueError('timing markers absent from signed bundle')
    old=helper['unpack_addon'](original['kernel.macho.gpuaddon'])
    if len(old)!=6 or len({name for name,_,_ in old})!=6: raise ValueError('unexpected baseline addon')
    oldmap={name:(mode,data) for name,mode,data in old}
    for name in NAMES:
        if PREFIX+name not in oldmap: raise ValueError('baseline bundle entry absent: '+name)
    if oldmap[PREFIX+'Info.plist'][1] != files['NativePowerVRMetal.bundle/Info.plist']:
        raise ValueError('Info.plist differs; exact bundle seal and baseline must be reconciled')
    replacements={PREFIX+name:files['NativePowerVRMetal.bundle/'+name] for name in NAMES if name!='Info.plist'}
    new=[(name,mode,replacements.get(name,data)) for name,mode,data in old]
    addon=helper['pack_addon'](new)
    if helper['unpack_addon'](addon)!=new: raise ValueError('addon roundtrip mismatch')
    trust=helper['owner_bundle'](original['kernel.macho.trustcache'],sorted(set(hashes)))
    out.mkdir(parents=True,exist_ok=False)
    (out/'gpuaddon').write_bytes(addon);(out/'trustcache').write_bytes(trust)
    report={'baseline_sha256':PINS,'source_commit':SOURCE,'kernel_device_commit':DEVICE,'mesa_commit':MESA,
            'bundle_archive_sha256':sha(archive.read_bytes()),'new_cdhashes':sorted(set(hashes)),
            'entries':[{'path':n,'mode':m,'before_sha256':sha(oldmap[n][1]),'after_sha256':sha(d)} for n,m,d in new],
            'addon_sha256':sha(addon),'trust_sha256':sha(trust),'data_seed_sha256':PINS['scratch-data.img'],
            'bundle_signature_verified_on_macos':True,'runtime_gpu_mode_verified':False}
    (out/'composition.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--provenance',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    helper=runpy.run_path(str(ROOT/'scripts/build-a04br3-powervr-probe.py'))
    print(json.dumps(build(a.baseline,a.archive,a.provenance,a.out,helper),indent=2))
if __name__=='__main__': main()
