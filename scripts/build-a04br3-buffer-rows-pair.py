#!/usr/bin/env python3
"""Package one already SDK-built timing probe at26/380MHz. Never flash/rebuild kernel.
The reference payload supplies the exact reduced bootargs; only clock may differ.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tomllib

ROOT=Path(__file__).resolve().parents[1]
CLOCK='a04br3-gpu-clock'

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def name(argument):
    return argument.partition('=')[0]

def paired_arguments(reference, defaults, mhz):
    tokens=shlex.split(reference)
    keys=[name(token) for token in tokens]
    if len(set(keys))!=len(keys): raise ValueError('duplicate reference boot argument')
    if 'a04br3-powervrprobed=1' not in tokens: raise ValueError('reference must enable probe')
    if 'sheng-gpu-addon=1' not in tokens: raise ValueError('reference must enable addon')
    tokens=[t for t in tokens if name(t)!=CLOCK]+[f'{CLOCK}={mhz}']
    if len(' '.join(tokens).encode())>1000: raise ValueError('paired bootargs exceed1000 bytes')
    omitted=[name(t) for t in defaults if name(t) not in {name(x) for x in tokens}]
    return tokens,omitted

def verify_pair(left,right):
    fields=('kernel_sha256','payload_kernel_sha256','data_ramdisk_sha256',
            'gpu_addon_sha256','trust_cache_sha256')
    for field in fields:
        if not left.get(field) or left.get(field)!=right.get(field):
            raise ValueError('pair input differs or missing: '+field)
    for data,clock in ((left,0),(right,380)):
        tokens=shlex.split(data['kernel_args'])
        if tokens.count(f'{CLOCK}={clock}')!=1 or sum(name(t)==CLOCK for t in tokens)!=1:
            raise ValueError('unexpected clock argument')
    stripped=lambda data:[t for t in shlex.split(data['kernel_args']) if name(t)!=CLOCK]
    if stripped(left)!=stripped(right): raise ValueError('nonclock argument difference')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('reference-payload','prepared-data','kernel','probe-dir','metal-plugin','trust-base','out','unixnu-dir'):
        p.add_argument('--'+arg,type=Path,required=True)
    p.add_argument('--data-volume-uuid',required=True)
    p.add_argument('--unique-chip-id',required=True)
    a=p.parse_args()
    if a.out.exists(): p.error('output already exists')
    reference=json.loads(a.reference_payload.read_text())
    for key,path in (('kernel_sha256',a.kernel),('data_ramdisk_sha256',a.prepared_data)):
        if sha(path)!=reference.get(key): p.error('reference mismatch: '+key)
    defaults=tomllib.loads((ROOT/'board.toml').read_text())['payload']['kernel_args']
    plans=[paired_arguments(reference['kernel_args'],defaults,clock) for clock in (0,380)]
    # Verify signed input identity before/after both packaging operations.
    inputs=[a.probe_dir/'powervrprobed',a.probe_dir/'lib/libvulkan_powervr_mesa.dylib',
            a.metal_plugin/'NativePowerVRMetal.bundle/NativePowerVRMetal',a.trust_base]
    before={str(x.resolve()):sha(x) for x in inputs}
    a.out.mkdir(parents=True)
    reports=[]
    for clock,(arguments,omitted) in zip((0,380),plans):
        out=a.out/('clock26' if clock==0 else 'clock380')
        command=['bash',str(ROOT/'scripts/a04br3-build-gpu-update.sh')]
        for field in ('prepared_data','kernel','probe_dir','metal_plugin','trust_base','unixnu_dir'):
            command+=['--'+field.replace('_','-'),str(getattr(a,field).resolve())]
        command+=['--out',str(out.resolve()),'--data-volume-uuid',a.data_volume_uuid,
                  '--unique-chip-id',a.unique_chip_id]
        for arg in arguments: command+=['--kernel-arg='+arg]
        for arg in omitted: command+=['--omit-kernel-arg',arg]
        subprocess.run(command,check=True,cwd=a.unixnu_dir)
        reports.append(json.loads((out/'payload.json').read_text()))
    verify_pair(*reports)
    if before!={str(x.resolve()):sha(x) for x in inputs}: raise SystemExit('signed inputs changed')
    manifest={'source_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
              'signed_inputs_sha256':before,'clocks':[0,380],'same_probe_binary':True,
              'same_kernel_data_addon_trust':True,'only_bootarg_difference':CLOCK,
              'adt_hashes':[r['adt_sha256'] for r in reports],
              'adt_semantic_comparison':'not performed here; independently verify only random-seed differs',
              'hardware_tested':False,'macos_generation_success':True}
    (a.out/'pair.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('PASS fixed-input pair; actual active clocks, ADT semantic diff and GPU correctness still require verification')

if __name__=='__main__':main()
