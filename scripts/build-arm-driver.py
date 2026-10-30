#!/usr/bin/env python3
# From UniXNU/UniXNU-sheng scripts/build-arm-driver.py; a04br3 adds the ArmCpu GICv2 define.
"""Compile the generic platform expert against the actual source-kernel ABI."""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
from a04br3_runtime_config import require_cpu_counters_disabled

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('component', choices=['ArmVirtPlatform', 'ArmDTPlatform', 'ArmCpu', 'NativeAES', 'NativeVirtioBlock', 'NativeRamBlock', 'NativeMobileDisplay', 'NativeVirtioConsole', 'NativeLegacyDisplay', 'NativeGOPPresenter', 'NativeOfflineGPU', 'NativeOfflineAccelerator', 'NativeIOGPUProviderProbe', 'NativeUfs', 'NativeHidInput', 'NativeKeyStoreEvents', 'NativeAdreno', 'NativeMsdc'])
parser.add_argument('--negative-aes-kat', action='store_true', help='Isolated own-source wrong-answer refusal control; never changes original inputs')
parser.add_argument('--verified-root', action='store_true', help='Authenticate all actual disk reads against an independently signed immutable policy')
parser.add_argument('--verified-root-bundle', action='store_true', help='Select exactly one capacity-matching policy per device, then independently authenticate it')
parser.add_argument('--negative-verified-block', action='store_true', help='Negative own-buffer control: corrupt only the completed DMA snapshot')
args = parser.parse_args()
if args.verified_root_bundle and not args.verified_root:
    parser.error('A policy bundle requires verified block mode')
if args.verified_root_bundle and args.negative_verified_block:
    parser.error('Select either bundle policy qualification or the isolated DMA snapshot fault')
if args.negative_verified_block and args.component != 'NativeVirtioBlock':
    parser.error('The isolated DMA snapshot fault exists only in the concrete virtio block transport')
if args.verified_root and args.component not in ('NativeVirtioBlock', 'NativeMsdc'):
    parser.error('Verified block mode requires the concrete read-only block transport')
if args.verified_root_bundle and args.component != 'NativeVirtioBlock':
    parser.error('A policy bundle is selected only by NativeVirtioBlock')
if args.negative_verified_block and not args.verified_root:
    parser.error('The isolated input fault requires verified block mode')
component = args.component
recipe = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
record = json.loads(recipe.read_text().strip().rstrip(','))
source = ROOT / 'drivers' / ('NativeMobileDisplay' if component in ('NativeLegacyDisplay','NativeGOPPresenter') else 'NativeMetal' if component in ('NativeOfflineGPU','NativeOfflineAccelerator','NativeIOGPUProviderProbe') else component) / {'NativeAdreno':'NativeAdrenoService.cpp'}.get(component, component + '.cpp')
out = ROOT / 'build' / {'ArmVirtPlatform': 'armvirt-platform', 'ArmDTPlatform': 'armdt-platform', 'ArmCpu': 'armcpu', 'NativeAES': 'native-aes',
                        'NativeVirtioBlock': 'native-virtio-block', 'NativeRamBlock': 'native-ram-block',
                        'NativeMobileDisplay': 'native-mobile-display', 'NativeVirtioConsole': 'native-virtio-console',
                        'NativeLegacyDisplay': 'native-legacy-display', 'NativeGOPPresenter': 'native-gop-presenter', 'NativeOfflineGPU':'native-offline-gpu','NativeOfflineAccelerator':'native-offline-accelerator','NativeIOGPUProviderProbe':'native-original-provider', 'NativeUfs':'native-ufs', 'NativeHidInput':'native-hid-input', 'NativeKeyStoreEvents':'native-keystore-events', 'NativeAdreno':'native-adreno',
                        'NativeMsdc': 'native-msdc'}[component]
if args.verified_root and component == 'NativeVirtioBlock':
    out = ROOT / ('build/native-virtio-block-verified-fault' if args.negative_verified_block else 'build/native-virtio-block-verified')
    if args.verified_root_bundle:
        out = ROOT / 'build/native-virtio-block-verified-bundle'
out.mkdir(parents=True, exist_ok=True)
if args.negative_aes_kat:
    if component != 'NativeAES':
        parser.error('The own-source KAT control requires NativeAES')
    out = ROOT / 'build/native-aes-kat-negative'
    out.mkdir(parents=True, exist_ok=True)
    text = source.read_text()
    marker = '0x76,0x49,0xab,0xac'
    if text.count(marker) != 1:
        raise SystemExit('The exact own-source NIST answer was not uniquely identified')
    (out / 'OriginalAESABI.h').write_bytes(source.with_name('OriginalAESABI.h').read_bytes())
    source = out / 'NativeAES.cpp'
    source.write_text(text.replace(marker, '0x77,0x49,0xab,0xac'))
destination = out / (component + '.o')
arguments = record['arguments']
command = []
index = 0
while index < len(arguments):
    value = arguments[index]
    if value == record['file']:
        command.append(str(source))
    elif value == '-o':
        command.extend(['-o', str(destination)])
        index += 1
    elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
        index += 1
    elif not value.startswith('-W'):
        command.append(value)
    index += 1
command.extend(['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                '-Wno-c99-designator', '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion',
                '-Wno-unnecessary-virtual-specifier', '-I', str(ROOT / '.cache/xnu-work/osfmk')])
if component in ('NativeVirtioBlock', 'NativeRamBlock', 'NativeUfs'):
    command += ['-I', str(ROOT / 'build/storage-abi/include')]
    if args.verified_root:
        command += ['-I', str(ROOT / 'kits/VerifiedRoot/include'), '-DNATIVE_VERIFIED_BLOCK=1']
    if args.negative_verified_block:
        command += ['-DNATIVE_VERIFIED_BLOCK_INJECT_FAULT=1']
    if args.verified_root_bundle:
        command += ['-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1']
if component == 'NativeUfs':
    command += ['-I', str(ROOT / 'kits/VerifiedRoot/include'), '-I', str(ROOT / 'drivers/NativeVirtioBlock'),
                '-DNATIVE_VERIFIED_BLOCK=1', '-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1', '-mgeneral-regs-only']
if component=='NativeOfflineGPU':
    command += ['-I',str(ROOT/'drivers/NativeVirtioConsole')]
if component in ('NativeLegacyDisplay','NativeHidInput'):
    command += ['-I', str(out)]
if component == 'NativeAdreno':
    command += ['-I', str(ROOT / 'kits/ArmSmmu4K'), '-I', str(ROOT / 'kits/AdrenoPM4'),
                '-I', str(ROOT / 'kits/sheng'), '-I', str(ROOT / 'kits/AdrenoFirmware'),
                '-I', str(ROOT / 'build/sheng-gmu')]
# a04br3 (MT8167, GIC-400): CONFIG_XNU_ARMCPU_GICV2=y in the defconfig reaches this script as XNU_ARMCPU_GICV2=1 and
# builds the GICv2 path of ArmCpu (github.com/mouseos/ArmCpu); without it ArmCpu builds unchanged.
if component == 'ArmCpu' and os.environ.get('XNU_ARMCPU_GICV2') == '1':
    command += ['-DARMCPU_GICV2=1']
# Explicit experiment only: the regular build is byte-for-byte unaffected.
if component == 'ArmCpu' and os.environ.get('XNU_MSDC_IRQ_WAIT') == '1':
    command += ['-DARMCPU_SPI_SERIALIZE=1']
if component == 'ArmCpu' and os.environ.get('XNU_MSDC_GIC_NS') == '1':
    if os.environ.get('XNU_MSDC_IRQ_WAIT') != '1':
        parser.error('XNU_MSDC_GIC_NS=1 requires XNU_MSDC_IRQ_WAIT=1')
    command += ['-DARMCPU_GICV2_ASSUME_NONSECURE=1']
if component == 'ArmCpu' and os.environ.get('XNU_ARMCPU_A04BR3_RUNTIME') == '1':
    if os.environ.get('XNU_ARMCPU_GICV2') != '1':
        raise SystemExit('a04br3 runtime sampling requires the GICv2 driver')
    config = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/config_cpu_counters.h'
    try:
        require_cpu_counters_disabled(config)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    command += ['-DARMCPU_RUNTIME_A04BR3=1']
# a04br3 eMMC (MSDC0, github.com/mouseos/NativeMsdc). CONFIG_XNU_NATIVE_MSDC_BLOCK=y builds the userdata windows as
# IOBlockStorageDevice subclasses; their kernel link needs the audited storage aliases (board.toml, the NativeMsdc bindings
# entry). Without it the driver serves the persistent store only and links on its own. With the RAM block linked and no
# NativeUfs, the store is also exported under the native_ufs_store_* names that NativeRamBlock imports.
# --verified-root (window block devices required) builds the windows verified against the owner-signed policy region in
# userdata and defines native_ufs_block_authenticates for NativeVirtioBlock's root authorization; the kernel link then
# needs verified_root_key, vr_open, vr_verify_block and vr_xnu_crypto (libA04BR3RootKey.a). The object stays in
# build/native-msdc.
if component == 'NativeMsdc':
    if os.environ.get('XNU_MSDC_IRQ_WAIT') == '1':
        command += ['-DNATIVE_MSDC_IRQ_WAIT=1']
    block_devices = os.environ.get('XNU_NATIVE_MSDC_BLOCK') == '1'
    if os.environ.get('XNU_MSDC_IRQ_WORKER') == '1':
        if os.environ.get('XNU_MSDC_IRQ_WAIT') != '1' or not block_devices or not args.verified_root:
            parser.error('XNU_MSDC_IRQ_WORKER=1 requires XNU_MSDC_IRQ_WAIT=1, block devices and --verified-root')
        command += ['-DNATIVE_MSDC_WORKER_EXPERIMENT=1']
    adjacency = os.environ.get('XNU_MSDC_QUEUE_ADJACENCY', '0')
    if adjacency not in ('0', '1'):
        parser.error('XNU_MSDC_QUEUE_ADJACENCY must be 0 or 1')
    if adjacency == '1':
        if (os.environ.get('XNU_MSDC_IRQ_WAIT') != '1' or
            os.environ.get('XNU_MSDC_IRQ_WORKER') != '1' or not block_devices or not args.verified_root):
            parser.error('XNU_MSDC_QUEUE_ADJACENCY requires IRQ wait, worker, block devices and --verified-root')
        command += ['-DNATIVE_MSDC_QUEUE_ADJACENCY=1']
    command += ['-DNATIVE_MSDC_BLOCK_DEVICES=' + ('1' if block_devices else '0')]
    if block_devices:
        command += ['-I', str(ROOT / 'build/storage-abi/include')]
    if args.verified_root:
        if not block_devices:
            parser.error('NativeMsdc --verified-root needs the window block devices (XNU_NATIVE_MSDC_BLOCK=1)')
        command += ['-I', str(ROOT / 'kits/VerifiedRoot/include'), '-DNATIVE_VERIFIED_BLOCK=1']
    if os.environ.get('XNU_NATIVE_RAM_BLOCK') == '1' and os.environ.get('XNU_NATIVE_UFS') != '1':
        command += ['-DNATIVE_MSDC_UFS_STORE_ABI=1']
    # Bindings of an earlier build describe an older object; build-native-virtio-bindings.py writes them again.
    for stale in ('bindings.json', 'original-bindings.S', 'original-bindings.o'):
        (out / stale).unlink(missing_ok=True)
(out / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
result = subprocess.run(command, cwd=record['directory'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
(out / 'compile.log').write_text(result.stdout)
if result.returncode:
    raise SystemExit(result.stdout[-3500:])
data = destination.read_bytes()
magic, cpu, subtype, kind, count, size = struct.unpack_from('<6I', data)
if (magic, cpu, subtype, kind) != (0xfeedfacf, 0x100000c, 0, 1):
    raise SystemExit('Unexpected platform object architecture')
offset = 32
deployment = None
for _ in range(count):
    cmd, length = struct.unpack_from('<II', data, offset)
    if cmd == 0x32:
        deployment = struct.unpack_from('<II', data, offset + 8)
    offset += length
if deployment != (2, 0x110000):
    raise SystemExit(f'Unexpected platform deployment {deployment}')
archive = out / ('lib' + component + '.a')
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, destination], check=True)
print(f'{component}: -Werror build and actual ARM64/iOS 17 object audit passed')
