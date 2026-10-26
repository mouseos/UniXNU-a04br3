#!/usr/bin/env python3
"""Exercise the actual shell helper's Python body with mocked macOS/payload tools."""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT.parent / 'UniXNU'
SOURCE = ROOT / 'scripts/a04br3-build-gpu-update.sh'
BODY = SOURCE.read_text().split("<<'PY_GPU_UPDATE'\n", 1)[1].rsplit('\nPY_GPU_UPDATE', 1)[0]
namespace = {'__name__': 'gpu_update_under_test'}
exec(compile(BODY, str(SOURCE), 'exec'), namespace)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
UUID = '12345678-1234-5678-9abc-123456789abc'


def case(failure=None):
    with tempfile.TemporaryDirectory(prefix='gpu-update-test-') as directory:
        root = Path(directory)
        base, device, out = root / 'UniXNU', root / 'device', root / 'update'
        packed_dir = base / 'drivers/DarwinBoot/Test'
        packed_dir.mkdir(parents=True)
        shutil.copyfile(BASE / 'drivers/DarwinBoot/Test/packed_ramdisk.py', packed_dir / 'packed_ramdisk.py')
        (base / 'unixnu').touch()
        shutil.copytree(BASE / 'scripts/unixnu', base / 'scripts/unixnu', ignore=shutil.ignore_patterns('__pycache__'))
        (device / 'scripts').mkdir(parents=True)
        shutil.copyfile(ROOT / 'scripts/a04br3-seed-powervr-probe.py', device / 'scripts/a04br3-seed-powervr-probe.py')
        (device / 'board.toml').write_text('[board]\nname="a04br3"\n[payload]\nsize_mib=126\nkernel_args=["-v", "sheng-gpu-addon=1", "new-default=1"]\n')
        import struct
        data = root / 'prepared.dbr'
        data.write_bytes(struct.pack('<8sIIQQ', b'DBRDISK1', 1, 4096, 4096, 0))
        kernel = root / 'kernel.macho'
        kernel.write_bytes(b'unchanged kernel')
        trust = root / 'original-trust'
        trust.write_bytes(b'original pinned trust')
        arguments = '-v sheng-gpu-addon=1 old-option=1'
        snapshots = {p: p.read_bytes() for p in (data, kernel, trust)}
        argv = ['gpu-update', str(device), '--unixnu-dir', str(base), '--prepared-data', str(data),
                '--data-volume-uuid', UUID, '--kernel', str(kernel),
                '--probe-dir', str(root / 'probe'), '--metal-plugin', str(root / 'metal'),
                '--trust-base', str(trust), '--unique-chip-id', '1234', '--out', str(out)]
        argv += ['--omit-kernel-arg', 'new-default', '--kernel-arg=old-option=1']
        if failure == 'arguments':
            argv += ['--omit-kernel-arg', 'old-option']
        if failure == 'output-alias':
            argv[argv.index('--out') + 1] = str(base / 'out/a04br3/payload/update')
        if failure == 'length':
            argv += ['--kernel-arg=long=' + 'a' * 1000]
        calls = []
        payload_dir = base / 'out/a04br3/payload'

        def check_output(command):
            calls.append(command)
            if command[0] == 'hdiutil':
                assert '-readonly' in command
                return plistlib.dumps({'system-entities': [{'dev-entry': '/dev/mock'}]})
            assert command[:3] == ['diskutil', 'info', '-plist']
            return plistlib.dumps({'VolumeUUID': '87654321-1234-5678-9abc-123456789abc' if failure == 'uuid' else UUID})

        def run(command, **kwargs):
            calls.append(command)
            if command[0] == 'hdiutil':
                assert command == ['hdiutil', 'detach', '/dev/mock']
            elif command[0] == sys.executable:
                assert Path(command[1]).name == 'make-a04br3-gpu-addon.py'
                assert command[command.index('--trust-base') + 1] == str(trust)
                (out / 'gpu-update.addon').write_bytes(b'NGPUADD2 mock')
                (out / 'trustcache-owner').write_bytes(b'combined trust')
            else:
                assert command[:3] == [str(base / 'unixnu'), 'build', 'payload']
                assert '--skip-setup' not in command and 'kernel' not in command
                assert command[command.index('--kernel') + 1] == str(kernel)
                assert command[command.index('--data-ramdisk') + 1] == str(data)
                assert command[command.index('--omit-kernel-arg') + 1] == 'new-default'
                assert [a.split('=', 1)[1] for a in command if a.startswith('--kernel-arg=')] == ['old-option=1']
                if failure == 'capacity':
                    raise subprocess.CalledProcessError(1, command)
                payload_dir.mkdir(parents=True)
                image = payload_dir / 'payload.img'
                image.write_bytes(b'official FAT image mock')
                result = dict(skip_setup=False, data_ramdisk_sha256=sha(data), kernel_sha256=sha(kernel),
                              gpu_addon_sha256=sha(out / 'gpu-update.addon'), trust_cache_sha256=sha(out / 'trustcache-owner'),
                              kernel_args=arguments, payload_sha256=sha(image), payload_bytes=image.stat().st_size)
                if failure == 'metadata':
                    result['data_ramdisk_sha256'] = 'wrong'
                (payload_dir / 'payload.json').write_text(json.dumps(result))
            return subprocess.CompletedProcess(command, 0)

        with mock.patch.object(sys, 'argv', argv), mock.patch.dict(os.environ), \
             mock.patch('subprocess.check_output', side_effect=check_output), mock.patch('subprocess.run', side_effect=run):
            os.environ.pop('UNIXNU_OUT', None)
            try:
                namespace['main']()
            except (SystemExit, Exception) as error:
                assert type(error).__name__ in ('SystemExit', 'CalledProcessError', 'Error'), repr(error)
                assert failure
            else:
                assert failure is None
        for path, content in snapshots.items():
            assert path.read_bytes() == content
        if failure:
            assert not (out / 'update.json').exists() and not (out / 'payload.img').exists()
            if failure in ('arguments', 'length', 'output-alias'):
                assert not calls
            if failure == 'uuid':
                assert calls[-1] == ['hdiutil', 'detach', '/dev/mock'] and not out.exists()
        else:
            result = json.loads((out / 'update.json').read_text())
            assert not result['kernel_rebuilt'] and not result['hardware_tested']
            assert result['data_volume_uuid'] == UUID.upper()
            assert json.loads((out / 'payload.json').read_text())['skip_setup'] is False
            assert not (out / 'original-payload.json').exists()


for failure in (None, 'arguments', 'length', 'output-alias', 'uuid', 'capacity', 'metadata'):
    case(failure)
print('PASS actual helper: readonly UUID verification, explicit Data/kernel inputs, official boot argument validation/overrides, addon/trust forwarding, payload-only/no skip-setup, metadata/capacity failures, unchanged inputs')
