#!/bin/bash
# Build a bounded diagnostic Metal bundle without changing the shared SSH tree.
set -euo pipefail
if [ "$#" -ne 3 ]; then echo 'usage: build-pipeline-failure-isolated.sh TREE VULKAN_DYLIB NEW_OUTPUT' >&2; exit 2; fi
tree=$1; driver=$2; output=$3
here=$(cd "$(dirname "$0")/.." && pwd)
device_commit=$(git -C "$here" rev-parse --verify 'HEAD^{commit}')
cmp -s "$0" <(git -C "$here" show "$device_commit:scripts/build-pipeline-failure-isolated.sh") || { echo 'running script differs from captured device commit' >&2; exit 2; }
[ ! -e "$output" ] || { echo 'output already exists' >&2; exit 2; }
mkdir -p "$output/tree/scripts" "$output/tree/drivers/NativeMetal" "$output/tree/package" "$output/tree/build/native-metal-bridge/ios" "$output/inputs"
git -C "$tree/drivers/NativeMetal" archive 043a94f5f04f2ea06ee3706dad06ff6d266b2c39 | tar -x -C "$output/tree/drivers/NativeMetal"
mkdir -p "$output/tree/drivers/NativeMobileDisplay"
git -C "$tree/drivers/NativeMobileDisplay" show 279cf9bd8e8b1751dcf991f46176dcc0c63d325d:NativeGOPWire.h > "$output/tree/drivers/NativeMobileDisplay/NativeGOPWire.h"
git -C "$here" archive "$device_commit" package/powervr scripts/build-a04br3-metal-plugin.py | tar -x -C "$output/tree"
cp "$tree/scripts/build-vulkan-metal-plugin.py" "$output/tree/scripts/"
cp -c "$tree/build/native-metal-bridge/ios/libnative_metal_bridge.a" "$output/tree/build/native-metal-bridge/ios/"
cp "$tree/build/native-metal-bridge/ios/libnative_metal_bridge.json" "$output/tree/build/native-metal-bridge/ios/"
cp -c "$driver" "$output/inputs/libvulkan_powervr_mesa.dylib"
[ "$(shasum -a 256 "$output/tree/drivers/NativeMetal/NativeBridgeMetalFrontend.mm" | cut -d ' ' -f 1)" = a892b7ddd0882ac8a3461c7fcbc6f939e4d686917671e4c686e3fe87db90f1cc ] || { echo 'NativeMetal frontend differs from pinned source' >&2; exit 2; }
[ "$(shasum -a 256 "$output/tree/build/native-metal-bridge/ios/libnative_metal_bridge.a" | cut -d ' ' -f 1)" = 7dcf78a7dc2d05caa7010b48e85224bab5cb087297f656d15502f517ed73b819 ] || { echo 'bridge archive differs from matched build' >&2; exit 2; }
[ "$(shasum -a 256 "$output/inputs/libvulkan_powervr_mesa.dylib" | cut -d ' ' -f 1)" = 8098ef68747a7f351215cb93ec5082bfade0043454f24db1a6bf96abb89d7bc1 ] || { echo 'Vulkan driver differs from matched build' >&2; exit 2; }
[ "$(shasum -a 256 "$output/tree/scripts/build-vulkan-metal-plugin.py" | cut -d ' ' -f 1)" = c5c088c12ff6529addcc4aa8d738efc5cad39a09564d65a066e234453d45700b ] || { echo 'plugin builder differs from pinned source' >&2; exit 2; }
python3 "$output/tree/scripts/build-a04br3-metal-plugin.py" --skip-bridge --pipeline-timing --pipeline-failure-diag --vulkan "$output/inputs/libvulkan_powervr_mesa.dylib" --out "$output/bundle"
codesign --verify --strict "$output/bundle/NativePowerVRMetal.bundle"
python3 - "$output" "$device_commit" <<'PY'
import hashlib,json,pathlib,subprocess,sys
out=pathlib.Path(sys.argv[1]);device_commit=sys.argv[2];driver=out/'inputs/libvulkan_powervr_mesa.dylib'
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
paths={'driver_input':driver,'bridge':out/'tree/build/native-metal-bridge/ios/libnative_metal_bridge.a',
       'frontend':out/'tree/drivers/NativeMetal/NativeBridgeMetalFrontend.mm',
       'display_wire':out/'tree/drivers/NativeMobileDisplay/NativeGOPWire.h',
       'board':out/'tree/package/powervr/user/NativePowerVRMetalBoard.mm',
       'plugin_builder':out/'tree/scripts/build-vulkan-metal-plugin.py',
       'device_builder':out/'tree/scripts/build-a04br3-metal-plugin.py',
       'plugin':out/'bundle/NativePowerVRMetal.bundle/NativePowerVRMetal',
       'bundled_driver':out/'bundle/NativePowerVRMetal.bundle/libvulkan_powervr_mesa.dylib'}
record={'device_commit':device_commit,
        'native_metal_commit':'043a94f5f04f2ea06ee3706dad06ff6d266b2c39',
        'mesa_commit_expected':'132ac2441b96e716e6ca69fb2ea7fd3b9888fc69',
        'mesa_source_verified_by_this_script':False,
        'pipeline_timing':True,'pipeline_failure_diagnostic':True,'shader_capture':False,
        'also_enables':['exception output','no-copy failure callback'],
        'reused_bridge':True,'runtime_verified':False,'sha256':{k:digest(v) for k,v in paths.items()},
        'source_files_sha256':{str(p.relative_to(out/'tree')):digest(p) for folder in ('drivers/NativeMetal','drivers/NativeMobileDisplay','package/powervr','scripts') for p in sorted((out/'tree'/folder).rglob('*')) if p.is_file()},
        'bridge_metadata_sha256':digest(out/'tree/build/native-metal-bridge/ios/libnative_metal_bridge.json'),
        'constructor_sha256':{p.name:digest(p) for p in sorted(out.glob('bundle.*-enable.mm'))},
        'xcode':subprocess.check_output(['xcodebuild','-version'],text=True).strip(),
        'sdk':subprocess.check_output(['xcrun','--sdk','iphoneos','--show-sdk-build-version'],text=True).strip(),
        'sdk_path':subprocess.check_output(['xcrun','--sdk','iphoneos','--show-sdk-path'],text=True).strip(),
        'clang_path':subprocess.check_output(['xcrun','--find','clang++'],text=True).strip(),
        'clang_version':subprocess.check_output(['xcrun','clang++','--version'],text=True).strip(),
        'bundle_files_sha256':{str(p.relative_to(out/'bundle')):digest(p) for p in sorted((out/'bundle').rglob('*')) if p.is_file()},
        'trust':json.loads((out/'bundle/trust.json').read_text())}
(out/'provenance.json').write_text(json.dumps(record,indent=2)+'\n')
PY
echo 'isolated pipeline failure diagnostic bundle complete'
