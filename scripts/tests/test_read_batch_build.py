#!/usr/bin/env python3
"""Exercise paired official CLI invocation without device access or images."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
from pathlib import Path
import sys
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('batch_build', ROOT / 'scripts/a04br3-read-batch-build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class PairBuild(unittest.TestCase):
    def test_read_only_seed_uuid_check(self):
        import plistlib
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); source=root/'source';device=root/'device'
            (source/'drivers/DarwinBoot/Test').mkdir(parents=True)
            (device/'scripts').mkdir(parents=True)
            (source/'drivers/DarwinBoot/Test/packed_ramdisk.py').write_text("def regions(data):\n return (4096, [(0, data)])\n")
            (device/'scripts/a04br3-seed-powervr-probe.py').write_text(
                "import uuid\ndef verify_volume_uuid(mount, expected):\n assert isinstance(expected, uuid.UUID)\n")
            data=root/'data';data.write_bytes(b'host fixture')
            attached=plistlib.dumps({'system-entities':[{'dev-entry':'/dev/fake-image-only'}]})
            with mock.patch.object(sys, 'platform', 'darwin'), mock.patch.object(build.subprocess, 'check_output', return_value=attached) as attach, mock.patch.object(build.subprocess,'run') as detach:
                build.verify_data_uuid(source,device,data,'00112233-4455-6677-8899-AABBCCDDEEFF')
                self.assertIn('-readonly',attach.call_args.args[0])
                self.assertEqual(detach.call_args.args[0],['hdiutil','detach','/dev/fake-image-only'])

    def test_pair_and_provenance_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source=root/'source'
            (source/'scripts').mkdir(parents=True)
            fixed = Path(os.environ.get('UNIXNU_ROOT', ROOT.parent/'UniXNU-cs-read-batch-validation')).resolve()
            self.assertEqual(subprocess.check_output(['git','-C',str(fixed),'rev-parse','HEAD'],text=True).strip(),build.UNIXNU)
            shutil.copytree(fixed/'scripts/unixnu', source/'scripts/unixnu', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            device, uefi, out = (root / name for name in ('device', 'uefi', 'out'))
            for directory in (device, uefi / 'Resources/DTBs'):
                directory.mkdir(parents=True)
            (device / 'board.toml').write_bytes((ROOT / 'board.toml').read_bytes())
            (device/'scripts').mkdir()
            (device/'scripts/a04br3-pre-build.sh').write_bytes((ROOT/'scripts/a04br3-pre-build.sh').read_bytes())
            (device/'scripts/check-a04br3-bootimg.py').write_bytes((ROOT/'scripts/check-a04br3-bootimg.py').read_bytes())
            paths = {}
            for name in ('kernel', 'data', 'trust', 'gpu', 'fd'):
                paths[name] = root / name
                paths[name].write_bytes(('input-'+name).encode() + (b'native-root-timing native-cs-read-batch' if name == 'kernel' else b''))
            paths['dtb'] = uefi / 'Resources/DTBs/a04br3.dtb'
            paths['dtb'].write_bytes(b'dtb')
            provenance = {'source_commit': build.UNIXNU, 'device_commit': build.DEVICE,
                          'armcpu_commit':build.ARMCPU, 'native_msdc_commit':build.MSDC,
                          'kernel_sha256': build.sha(paths['kernel']), 'kernel_build_flags': {'XNU_ROOT_TIMING': '1'},
                          'data_uuid':'00112233-4455-6677-8899-AABBCCDDEEFF',
                          'pmgr_build_report':{'virtual_entries':340}, 'reference_kernel_sha256':'reference',
                          'display_build_report':{'virtual_entries':358,'original_candidate_sha256':'reference'}}
            firmware = {'uefi_commit': build.UEFI, 'unixnu_commit': build.FIRMWARE_UNIXNU, 'darwinboot_commit':build.DARWINBOOT, 'fd_sha256': build.sha(paths['fd'])}
            (root/'kernel.json').write_text(json.dumps(provenance))
            (root/'firmware.json').write_text(json.dumps(firmware))
            (root/'seed.json').write_text(json.dumps({'volume_uuid':provenance['data_uuid']}))
            (root/'pre-build.log').write_text('fake pre-build execution record')
            dp={'method':'official-pre-build-from-ipsw','source_commit':build.UNIXNU,'device_commit':build.DEVICE,
                'pre_build_sha256':build.sha(device/'scripts/a04br3-pre-build.sh'),'seed_record_sha256':build.sha(root/'seed.json'),
                'pre_build_log_sha256':build.sha(root/'pre-build.log'),
                **{name+'_sha256':build.sha(paths[name]) for name in ('data','trust','gpu')}}
            (root/'data-provenance.json').write_text(json.dumps(dp))
            manifest = {'source_commit':build.UNIXNU, 'device_commit':build.DEVICE, 'uefi_commit':build.UEFI,
                        'data_origin':'official-pre-build-from-ipsw','root_timing':1,
                        'kernel_provenance':str(root/'kernel.json'), 'firmware_provenance':str(root/'firmware.json'), 'seed_provenance':str(root/'seed.json'), 'data_provenance':str(root/'data-provenance.json'), 'pre_build_log':str(root/'pre-build.log'),
                        'files':{name:{'path':str(path), 'sha256':build.sha(path)} for name,path in paths.items()}}
            (root/'inputs.json').write_text(json.dumps(manifest))
            command = ['test','--source',str(source),'--device',str(device),'--uefi',str(uefi),
                       '--inputs',str(root/'inputs.json'),'--out',str(out),'--log-dir',str(root/'logs')]
            seen = []
            generated_adts = []
            def run(argv, cwd=None, env=None, check=False, stdout=None, stderr=None):
                seen.append((argv, (env or {}).get('UNIXNU_UNIQUE_CHIP_ID')))
                if argv[1:3] == ['build','payload']:
                    target = Path(argv[argv.index('--out')+1])
                    payload_dir = target/'a04br3/payload'; payload_dir.mkdir(parents=True,exist_ok=True)
                    words = [x.split('=',1)[1] for x in argv if x.startswith('--kernel-arg=')]
                    # The actual UniXNU merge removes board arguments overridden or omitted by name.
                    import tomllib
                    board = tomllib.loads((device/'board.toml').read_text())['payload']['kernel_args']
                    keys = {x.partition('=')[0] for x in words}
                    omitted = {x.split('=',1)[1] for x in argv if x.startswith('--omit-kernel-arg=')}
                    merged = [x for x in board if x.partition('=')[0] not in keys|omitted] + words
                    adt_dir = payload_dir/'stage/EFI/UniXNU/Payloads'; adt_dir.mkdir(parents=True,exist_ok=True)
                    def prop(name, value):
                        return name.encode().ljust(32,b'\0')+struct.pack('<I',len(value))+value+b'\0'*((-len(value))%4)
                    adt=struct.pack('<II',2,0)+prop('random-seed',bytes([len(seen)])*64)+prop('unique-chip-id',struct.pack('<Q',int(env['UNIXNU_UNIQUE_CHIP_ID'],16)))
                    (adt_dir/'kernel.macho.adt').write_bytes(adt)
                    generated_adts.append(adt)
                    meta = {'kernel_args':' '.join(merged), 'kernel_sha256':build.sha(paths['kernel']),
                            'data_ramdisk_sha256':build.sha(paths['data']), 'trust_cache_sha256':build.sha(paths['trust']),
                            'gpu_addon_sha256':build.sha(paths['gpu']), 'adt_sha256':hashlib.sha256(adt).hexdigest(), 'payload_sha256':hashlib.sha256(b'payload').hexdigest()}
                    (payload_dir/'payload.json').write_text(json.dumps(meta)); (payload_dir/'payload.img').write_bytes(b'payload')
                elif argv[0] == 'mcopy':
                    Path(argv[-1]).write_bytes(generated_adts[-1])
                elif argv[1:2] == ['bootimg']:
                    Path(argv[argv.index('-o')+1]).write_bytes(b'boot')
                return mock.Mock(returncode=0)
            with mock.patch.object(build, 'check_preparation_source', return_value='prep-commit'), mock.patch.object(build, 'check_repo'), mock.patch.object(build, 'git', side_effect=lambda path,*a: build.ARMCPU if str(path).endswith('ArmCpu') else build.MSDC), mock.patch.object(build, 'check_uefi_checkout'), mock.patch.object(build, 'check_uefi_firmware'), mock.patch.object(build, 'check_firmware_unixnu'), mock.patch.object(build, 'verify_data_uuid'), mock.patch.object(build.subprocess, 'run', side_effect=run), \
                 mock.patch.object(sys, 'argv', command), contextlib.redirect_stdout(io.StringIO()):
                build.main()
            # Generate input records from artifacts using the real recorder; it does not claim proof of origin.
            record_spec=importlib.util.spec_from_file_location('batch_record', ROOT/'scripts/a04br3-read-batch-record-inputs.py')
            recorder=importlib.util.module_from_spec(record_spec);record_spec.loader.exec_module(recorder)
            record_out=root/'input-records'
            rec_argv=['record','--source',str(source),'--device',str(device),'--uefi',str(uefi),
                      '--kernel-provenance',str(root/'kernel.json'),'--firmware-provenance',str(root/'firmware.json'),
                      '--seed-provenance',str(root/'seed.json'),'--pre-build-log',str(root/'pre-build.log'),'--out',str(record_out)]
            for name,path in paths.items(): rec_argv += ['--'+name,str(path)]
            with mock.patch.object(recorder.build,'check_repo'), mock.patch.object(recorder.build,'check_uefi_checkout'), mock.patch.object(recorder.build,'check_firmware_unixnu'), mock.patch.object(sys,'argv',rec_argv), contextlib.redirect_stdout(io.StringIO()):
                recorder.main()
            recorded=json.loads((record_out/'inputs.json').read_text())
            self.assertEqual(recorded['files']['data']['sha256'], build.sha(paths['data']))
            self.assertEqual(recorded['pre_build_log'], str(root/'pre-build.log'))
            result=json.loads((out/'pair.json').read_text())
            assert result['variants']['batch']['kernel_args'] == result['variants']['baseline']['kernel_args']+' native-cs-read-batch=1'
            payloads=[cmd for cmd,_ in seen if cmd[1:3]==['build','payload']]
            assert len(payloads)==2 and payloads[0][payloads[0].index('--out')+1] != payloads[1][payloads[1].index('--out')+1]
            assert len(seen)==8 and all('fastboot' not in ' '.join(cmd) for cmd,_ in seen)
            identities=[value for cmd,value in seen if value]
            assert len(identities)==2 and len(set(identities))==1
            assert (out/'software-identity.txt').stat().st_mode & 0o077 == 0
            assert all(str(paths[name]) not in result['identity_origin'] for name in paths)
            baseline_adt=out/'baseline/kernel.macho.adt'; batch_adt=out/'batch/kernel.macho.adt'
            identity=int((out/'software-identity.txt').read_text(),16)
            self.assertEqual(build.adt_comparison(baseline_adt,identity), build.adt_comparison(batch_adt,identity))
            damaged=bytearray(batch_adt.read_bytes()); damaged[-1]^=1;batch_adt.write_bytes(damaged)
            with self.assertRaises(ValueError): build.adt_comparison(batch_adt,identity)
            # A failed bootimg leaves only an external log, no candidate directory.
            failure_out=root/'failure-output'; command[command.index('--out')+1]=str(failure_out)
            def fail_boot(argv, **kwargs):
                if argv[1:2]==['bootimg']:
                    raise subprocess.CalledProcessError(1, argv)
                return run(argv,**kwargs)
            with mock.patch.object(build, 'check_preparation_source', return_value='prep-commit'), mock.patch.object(build, 'check_repo'), mock.patch.object(build, 'git', side_effect=lambda path,*a: build.ARMCPU if str(path).endswith('ArmCpu') else build.MSDC), mock.patch.object(build, 'check_uefi_checkout'), mock.patch.object(build, 'check_uefi_firmware'), mock.patch.object(build, 'check_firmware_unixnu'), mock.patch.object(build, 'verify_data_uuid'), mock.patch.object(build.subprocess,'run',side_effect=fail_boot), mock.patch.object(sys,'argv',command):
                with self.assertRaises(subprocess.CalledProcessError): build.main()
            self.assertFalse(failure_out.exists())
            self.assertFalse(list(root.glob('.failure-output.*')))
            self.assertTrue((root/'logs/failure-output.build.log').exists())
            out2=root/'out2'; manifest['root_timing']=0; (root/'inputs.json').write_text(json.dumps(manifest))
            command[command.index('--out')+1]=str(out2)
            with mock.patch.object(build, 'check_preparation_source', return_value='prep-commit'), mock.patch.object(build, 'check_repo'), mock.patch.object(build, 'git', side_effect=lambda path,*a: build.ARMCPU if str(path).endswith('ArmCpu') else build.MSDC), mock.patch.object(build, 'check_uefi_checkout'), mock.patch.object(sys, 'argv', command):
                with self.assertRaises(ValueError): build.main()
            assert not out2.exists()
            # Mismatched bytes and self-contradictory recorded source must fail before staging.
            manifest['root_timing']=1
            for index, mutate in enumerate(('hash','origin','source')):
                candidate=json.loads(json.dumps(manifest))
                if mutate=='hash': candidate['files']['data']['sha256']='0'*64
                if mutate=='origin': candidate['data_origin']='device-backup'
                if mutate=='source': candidate['device_commit']='0'*40
                (root/'inputs.json').write_text(json.dumps(candidate))
                rejected=root/('reject-'+str(index)); command[command.index('--out')+1]=str(rejected)
                with mock.patch.object(build, 'check_preparation_source', return_value='prep-commit'), mock.patch.object(build, 'check_repo'), mock.patch.object(build, 'git', side_effect=lambda path,*a: build.ARMCPU if str(path).endswith('ArmCpu') else build.MSDC), mock.patch.object(build, 'check_uefi_checkout'), mock.patch.object(sys, 'argv', command):
                    with self.assertRaises(ValueError): build.main()
                self.assertFalse(rejected.exists())

if __name__ == '__main__': unittest.main()
