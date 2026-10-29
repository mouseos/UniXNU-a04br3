#!/usr/bin/env python3
"""Record hashes for official-build inputs; a record does not prove their origin."""
import argparse
import types
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
build_path = HERE / 'a04br3-read-batch-build.py'
build = types.ModuleType('batch_build')
build.__file__ = str(build_path)
exec(compile(build_path.read_bytes(), str(build_path), 'exec'), build.__dict__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'device', 'uefi', 'kernel', 'data', 'trust', 'gpu', 'fd', 'dtb',
                 'kernel-provenance', 'firmware-provenance', 'seed-provenance', 'pre-build-log', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    source, device, uefi = args.source.resolve(), args.device.resolve(), args.uefi.resolve()
    build.check_repo(source, build.UNIXNU)
    build.check_repo(device, build.DEVICE)
    build.check_uefi_checkout(uefi)
    paths = {name:getattr(args,name).resolve(strict=True) for name in ('kernel','data','trust','gpu','fd','dtb')}
    records = {name:getattr(args,name).resolve(strict=True) for name in ('kernel_provenance','firmware_provenance','seed_provenance','pre_build_log')}
    kp=json.loads(records['kernel_provenance'].read_text(encoding='utf-8'))
    fp=json.loads(records['firmware_provenance'].read_text(encoding='utf-8'))
    seed=json.loads(records['seed_provenance'].read_text(encoding='utf-8'))
    if (kp.get('source_commit')!=build.UNIXNU or kp.get('device_commit')!=build.DEVICE or
        kp.get('kernel_sha256')!=build.sha(paths['kernel']) or
        kp.get('kernel_build_flags',{}).get('XNU_ROOT_TIMING')!='1' or
        fp.get('uefi_commit')!=build.UEFI or fp.get('unixnu_commit')!=build.UNIXNU or
        fp.get('fd_sha256')!=build.sha(paths['fd']) or
        str(seed.get('volume_uuid','')).upper()!=str(kp.get('data_uuid','')).upper() or
        not kp.get('data_uuid')):
        raise ValueError('kernel, firmware or Data seed records do not match')
    if not records['pre_build_log'].stat().st_size:
        raise ValueError('pre-build log is empty')
    out=args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    dp={'method':'official-pre-build-from-ipsw','source_commit':build.UNIXNU,'device_commit':build.DEVICE,
        'pre_build_sha256':build.sha(device/'scripts/a04br3-pre-build.sh'),
        'seed_record_sha256':build.sha(records['seed_provenance']),
        'pre_build_log_sha256':build.sha(records['pre_build_log']),
        'evidence_limit':'hashes and declarations only; inspect recorded execution separately',
        **{name+'_sha256':build.sha(paths[name]) for name in ('data','trust','gpu')}}
    data_record=out/'data-provenance.json'
    data_record.write_text(json.dumps(dp,indent=2)+'\n',encoding='utf-8')
    manifest={'source_commit':build.UNIXNU,'device_commit':build.DEVICE,'uefi_commit':build.UEFI,
              'data_origin':'official-pre-build-from-ipsw','root_timing':1,
              **{name:str(path) for name,path in records.items()}, 'data_provenance':str(data_record),
              'files':{name:{'path':str(path),'sha256':build.sha(path)} for name,path in paths.items()}}
    (out/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print('Input records written; official build origin requires independent log verification:',out)

if __name__=='__main__':main()
