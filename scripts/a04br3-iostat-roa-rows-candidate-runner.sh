#!/bin/bash
# Build an iostat pair from the retained ROA-success inputs; never touch a device.
set -euo pipefail
: "${RUNNER_TEMP:?}" "${BATCH_PREP_COMMIT:?}" "${1:?task directory required}"
root=$RUNNER_TEMP
task=$1
case "$BATCH_PREP_COMMIT" in *[!0-9a-f]*|'') echo 'Invalid preparation commit' >&2; exit 2 ;; esac
[ "${#BATCH_PREP_COMMIT}" -eq 40 ] || exit 2
[ "$(dirname "$task")" = "$root" ] || { echo 'Task must be a direct child of runner temp' >&2; exit 2; }
case "$(basename "$task")" in io-read-batch-iostat-roa-rows-*) ;; *) echo 'Unexpected task directory' >&2; exit 2 ;; esac
[ -d "$task" ] && [ ! -L "$task" ] || { echo 'Missing or symlinked task directory' >&2; exit 2; }
[ -s "$task/preparation.bundle" ] && [ ! -L "$task/preparation.bundle" ] || { echo "Missing source bundle" >&2; exit 3; }
[ ! -e "$task/preparation" ] && [ ! -e "$task/pair" ] || { echo 'Task already used' >&2; exit 3; }
prior="$root/io-read-batch-e8f-20261029b"
iostat="$root/io-read-batch-iostat-e8f-20261030c"
rows="$root/pipeline-retry-roa-rows-isolated"
for required in "$prior/trust-base" "$prior/official/data-32.dbr" "$prior/official/owner-data/seed.json" \
                "$iostat/inputs/kernel" "$iostat/inputs/fd" "$iostat/inputs/data" "$iostat/inputs/trust" "$iostat/inputs/gpu" \
                "$rows/powervr-metal/NativePowerVRMetal.bundle/NativePowerVRMetal" \
                "$rows/powervr-metal/trust.json" "$rows/status" \
                "$iostat/records/original-inputs.json" "$iostat/records/kernel_provenance.json" \
                "$iostat/records/firmware_provenance.json" "$iostat/records/seed_provenance.json" \
                "$iostat/records/pre_build.log" "$iostat/records/data_provenance.json"; do
    [ -f "$required" ] && [ ! -L "$required" ] || { echo "Required retained input unavailable: $required" >&2; exit 3; }
done
[ "$(cat "$rows/status")" = exit=0 ] || { echo 'ROA provenance pipeline did not succeed' >&2; exit 3; }
mkdir "$task/tmp" "$task/cache" "$task/logs" "$task/pycache" "$task/records" "$task/inputs"
clean=(env -i "PATH=$PATH" "TMPDIR=$task/tmp" "XDG_CACHE_HOME=$task/cache" "PIP_CACHE_DIR=$task/cache/pip" "PYTHONDONTWRITEBYTECODE=1" "PYTHONNOUSERSITE=1" "PYTHONPYCACHEPREFIX=$task/pycache" "GIT_CONFIG_NOSYSTEM=1" "GIT_CONFIG_GLOBAL=/dev/null" "GIT_CONFIG_SYSTEM=/dev/null" "LANG=C")
"${clean[@]}" git clone --quiet https://github.com/mouseos/UniXNU.git "$task/source"
"${clean[@]}" git -C "$task/source" checkout --quiet --detach e8f5385e4cee02bb15efa2fb97c20bb59b98d72f
"${clean[@]}" git -C "$task/source" submodule update --init --recursive
"${clean[@]}" git clone --quiet https://github.com/mouseos/UniXNU-a04br3.git "$task/device"
"${clean[@]}" git -C "$task/device" checkout --quiet --detach 8fde8ba87486338342f3be528823cb46394f2c5b
"${clean[@]}" git -C "$task/device" submodule update --init --recursive
"${clean[@]}" git clone --quiet https://github.com/mouseos/uefi-a04br3.git "$task/uefi"
"${clean[@]}" git -C "$task/uefi" checkout --quiet --detach d3d4f28a89b29553ede7d7523c6af86d7e2459e8
"${clean[@]}" git -C "$task/uefi" submodule update --init Common/Mu Common/Mu_OEM_Sample Mu_Basecore
"${clean[@]}" git clone --quiet https://github.com/mouseos/UniXNU-a04br3.git "$task/preparation"
"${clean[@]}" git -C "$task/preparation" fetch --quiet "$task/preparation.bundle" HEAD
"${clean[@]}" git -C "$task/preparation" checkout --quiet --detach "$BATCH_PREP_COMMIT"
[ "$("${clean[@]}" git -C "$task/preparation" rev-parse HEAD)" = "$BATCH_PREP_COMMIT" ] || exit 4
[ "$(shasum -a 256 "$task/preparation/scripts/a04br3-iostat-roa-rows-candidate-runner.sh" | cut -d ' ' -f1)" = "$(shasum -a 256 "$task/runner.sh" | cut -d ' ' -f1)" ] || { echo 'Runner source hash mismatch' >&2; exit 4; }
"${clean[@]}" python3 -IB - "$prior" "$iostat" "$rows" "$task" <<'PY'
import hashlib,json,pathlib,shutil,subprocess,sys,uuid
prior,iostat,rows,task=map(pathlib.Path,sys.argv[1:])
rec=task/'records'; inputs=task/'inputs'
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def copy(src,name):
 dst=inputs/name;shutil.copyfile(src,dst)
 if sha(src)!=sha(dst):raise SystemExit('copy digest changed: '+name)
 return dst
def load(path):return json.loads(path.read_text(encoding='utf-8'))
def cdhashes(path,label):
 values=load(path)
 if not isinstance(values,dict):raise SystemExit(label+' is not an object')
 found=sorted({value.lower() for key,value in values.items() if key.endswith('cdhash') and isinstance(value,str) and len(value)==40 and all(c in '0123456789abcdef' for c in value.lower())})
 if not found:raise SystemExit(label+' has no valid CDHashes')
 return found
seed_path=prior/'official/owner-data/seed.json'; seed=load(seed_path)
if not isinstance(seed.get('volume_uuid'),str) or not seed['volume_uuid']:
 raise SystemExit('retained seed lacks volume UUID')
plugin_dir=rows/'powervr-metal'; plugin=plugin_dir/'NativePowerVRMetal.bundle/NativePowerVRMetal'; plugin_trust=load(plugin_dir/'trust.json')
plugin_hashes=cdhashes(plugin_dir/'trust.json','ROA plugin trust record')
# Preserve the successful ROA output as evidence only.  The candidate must
# grow fresh Data from the official 32 MiB baseline, then stage the official
# signed probe/plugin and derive a new owner trust module from the original.
fresh=task/'fresh';fresh.mkdir()
baseline=copy(prior/'official/data-32.dbr','seed-source-data-32.dbr')
source_seed=copy(seed_path,'official-seed-provenance.json')
copy(prior/'trust-base','original-trustcache')
copy(rows/'powervr-metal/trust.json','roa-plugin-trust.json')
for source,name in ((iostat/'records/kernel_provenance.json','kernel_provenance.json'),(iostat/'records/firmware_provenance.json','firmware_provenance.json'),(iostat/'records/seed_provenance.json','official-pre-build-seed.json'),(iostat/'records/pre_build.log','official-pre-build.log'),(iostat/'records/data_provenance.json','official-data-provenance.json'),(iostat/'inputs/data','original-data'),(iostat/'inputs/trust','original-trust'),(iostat/'inputs/gpu','original-gpu')):
 copy(source,name)
(task/'source/build').mkdir(exist_ok=True)
grow=[sys.executable,'-IB',str(task/'source/scripts/seed-data-grow.py'),'--gib','4',str(baseline),str(fresh/'data-grown.dbr')]
subprocess.run(grow,check=True)
if not (fresh/'data-grown.dbr').is_file(): raise SystemExit('fresh Data grow produced no output')
expected=str(uuid.UUID(seed['volume_uuid'])).upper()
trust_out=fresh/'trustcache-owner'
probe_out=fresh/'powervr-probe'
shutil.copytree(rows/'powervr-probe', probe_out, symlinks=False)
gpu=fresh/'powervr-gpu-addon'
subprocess.run([sys.executable,'-IB',str(task/'device/scripts/make-a04br3-gpu-addon.py'),'--probe-dir',str(probe_out),'--metal-plugin',str(plugin_dir),'--trust-base',str(inputs/'original-trustcache'),'--trust-out',str(trust_out),'--out',str(gpu)],check=True)
subprocess.run([sys.executable,'-IB',str(task/'device/scripts/a04br3-seed-powervr-probe.py'),'--tree',str(task/'source'),'--probe-dir',str(probe_out),'--metal-plugin',str(plugin_dir),'--metal-mode','publish','--expected-volume-uuid',expected,str(fresh/'data-grown.dbr'),str(fresh/'data-staged.dbr')],check=True)
if not trust_out.is_file() or not gpu.is_file() or not (fresh/'data-staged.dbr').is_file(): raise SystemExit('fresh probe/trust/GPU seed staging failed')
seed_report=fresh/'data-staged.json'
if not seed_report.is_file():raise SystemExit('seed staging produced no report')
for source,name in ((iostat/'inputs/kernel','kernel'),(fresh/'data-staged.dbr','data'),(trust_out,'trust'),(gpu,'gpu'),(rows/'powervr-metal/trust.json','roa-plugin-trust.json'),(seed_path,'seed-provenance.json'),(prior/'official/data-32.dbr','seed-source-data-32.dbr'),(fresh/'data-grown.dbr','grown-data.dbr'),(seed_report,'seed-staging-report.json')):
 copy(source,name)
shutil.copytree(plugin_dir, inputs/'metal-plugin', symlinks=False)
if sha(plugin) != sha(inputs/'metal-plugin'/'NativePowerVRMetal.bundle'/'NativePowerVRMetal'):
 raise SystemExit('plugin directory copy changed executable')
kernel=inputs/'kernel'
if b'native-msdc-iostat' not in kernel.read_bytes():raise SystemExit('retained iostat kernel lacks diagnostic string')
manifest={'source_commit':'e8f5385e4cee02bb15efa2fb97c20bb59b98d72f','device_commit':'8fde8ba87486338342f3be528823cb46394f2c5b','uefi_commit':'d3d4f28a89b29553ede7d7523c6af86d7e2459e8','data_origin':'official-pre-build-from-ipsw','root_timing':1,'files':{}}
for name in ('kernel','data','trust'):
 manifest['files'][name]={'path':str(inputs/name),'sha256':sha(inputs/name)}
manifest['files']['gpu']={'path':str(inputs/'gpu'),'sha256':sha(inputs/'gpu')}
manifest['metal_plugin']={'path':str(inputs/'metal-plugin'),'executable_sha256':sha(inputs/'metal-plugin'/'NativePowerVRMetal.bundle'/'NativePowerVRMetal'),'trust_sha256':sha(inputs/'roa-plugin-trust.json')}
manifest.update({'kernel_provenance':str(inputs/'kernel_provenance.json'),'firmware_provenance':str(inputs/'firmware_provenance.json'),'seed_provenance':str(inputs/'official-pre-build-seed.json'),'pre_build_log':str(inputs/'official-pre-build.log'),'data_provenance':str(inputs/'official-data-provenance.json')})
fd=copy(iostat/'inputs/fd','fd')
manifest['files']['fd']={'path':str(fd),'sha256':sha(fd)}
manifest['files']['dtb']={'path':str(task/'uefi/Resources/DTBs/a04br3.dtb'),'sha256':sha(task/'uefi/Resources/DTBs/a04br3.dtb')}
# Recompute the addon builder's signed-input set from the current files; do
# not trust a prior pipeline's metadata.  This is also the exact set that must
# have been passed into the derived owner trust bundle.
import importlib.util
spec=importlib.util.spec_from_file_location('candidate_gpu_addon',task/'device/scripts/make-a04br3-gpu-addon.py')
addon=importlib.util.module_from_spec(spec);spec.loader.exec_module(addon)
verified_hashes=addon.verified_hashes(probe_out,plugin_dir)
probe_hashes=cdhashes(probe_out/'trust.json','probe trust record')
if verified_hashes != sorted(set(plugin_hashes) | set(probe_hashes)):raise SystemExit('verified hashes differ from probe and plugin records')
copy(probe_out/'trust.json','probe-trust.json')
def owner_hashes(bundle):
 raw=bundle.read_bytes()
 if len(raw)<8:raise SystemExit('derived owner trust is truncated')
 count=int.from_bytes(raw[:4],'little')
 if count<1 or len(raw)<4+4*count:raise SystemExit('derived owner trust has invalid module table')
 starts=[int.from_bytes(raw[4+4*i:8+4*i],'little') for i in range(count)]+[len(raw)]
 start,end=starts[0],starts[1]
 if start<4+4*count or end>len(raw) or end-start<24 or int.from_bytes(raw[start:start+4],'little')!=2:raise SystemExit('derived owner trust first module is invalid')
 entries=int.from_bytes(raw[start+20:start+24],'little')
 if end-start != 24+24*entries:raise SystemExit('derived owner trust entry table is invalid')
 return sorted(raw[start+24+24*i:start+44+24*i].hex() for i in range(entries))
if owner_hashes(trust_out) != verified_hashes:raise SystemExit('derived owner trust does not actually encode exactly the four verified GPU CDHashes')
provenance={'schema':4,'kind':'iostat-roa-rows-candidate','fixed_sources':{'unixnu':'e8f5385e4cee02bb15efa2fb97c20bb59b98d72f','device':'8fde8ba87486338342f3be528823cb46394f2c5b','uefi':'d3d4f28a89b29553ede7d7523c6af86d7e2459e8'},'original_pre_build':{'kernel_provenance':sha(inputs/'kernel_provenance.json'),'firmware_provenance':sha(inputs/'firmware_provenance.json'),'seed_provenance':sha(inputs/'official-pre-build-seed.json'),'data_provenance':sha(inputs/'official-data-provenance.json'),'pre_build_log':sha(inputs/'official-pre-build.log')},'original_inputs':{name:{'path':str(inputs/('original-'+name)),'sha256':sha(inputs/('original-'+name))} for name in ('data','trust','gpu')},'staged_seed':{'expected_volume_uuid':expected,'source_data':{'path':str(inputs/'seed-source-data-32.dbr'),'sha256':sha(inputs/'seed-source-data-32.dbr')},'source_data_sha256':sha(inputs/'seed-source-data-32.dbr'),'grown_data':{'path':str(inputs/'grown-data.dbr'),'sha256':sha(inputs/'grown-data.dbr')},'grown_data_sha256':sha(inputs/'grown-data.dbr'),'data':{'path':str(inputs/'data'),'sha256':sha(inputs/'data')},'data_sha256':sha(inputs/'data'),'report':{'path':str(inputs/'seed-staging-report.json'),'sha256':sha(inputs/'seed-staging-report.json')},'grow_script_sha256':sha(task/'source/scripts/seed-data-grow.py'),'seed_stager_sha256':sha(task/'device/scripts/a04br3-seed-powervr-probe.py'),'metal_mode':'publish','cli':['--metal-mode','publish','--expected-volume-uuid',expected]},'plugin':{'path':str(inputs/'metal-plugin'),'executable_sha256':sha(inputs/'metal-plugin'/'NativePowerVRMetal.bundle'/'NativePowerVRMetal'),'trust_record':{'path':str(inputs/'roa-plugin-trust.json'),'sha256':sha(inputs/'roa-plugin-trust.json')},'probe_trust_record':{'path':str(inputs/'probe-trust.json'),'sha256':sha(inputs/'probe-trust.json')},'verified_hashes':verified_hashes},'derived_trust':{'base':{'path':str(inputs/'original-trustcache'),'sha256':sha(inputs/'original-trustcache')},'base_sha256':sha(inputs/'original-trustcache'),'output':{'path':str(inputs/'trust'),'sha256':sha(inputs/'trust')},'sha256':sha(inputs/'trust'),'decoded_owner_hashes':owner_hashes(trust_out)},'gpu_addon':{'path':str(inputs/'gpu'),'sha256':sha(inputs/'gpu'),'builder_sha256':sha(task/'device/scripts/make-a04br3-gpu-addon.py')}}
(rec/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
manifest['candidate_provenance']=str(rec/'provenance.json')
(rec/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
PY
# The normal builder's pre-build contract remains unchanged.  The copied
# candidate builder verifies the original records and this independent staging
# contract before it consumes the new Data/trust/GPU inputs.
"${clean[@]}" python3 -IB "$task/preparation/scripts/a04br3-iostat-roa-rows-candidate-build.py" \
  --source "$task/source" --device "$task/device" --uefi "$task/uefi" \
  --inputs "$task/records/inputs.json" --out "$task/pair-building" --log-dir "$task/logs" --measurement iostat
cp -R "$task/records" "$task/pair-building/source-records"
diff -qr "$task/records" "$task/pair-building/source-records" >/dev/null
[ ! -e "$task/pair" ] || { echo 'Completed output already exists' >&2; exit 5; }
mv "$task/pair-building" "$task/pair"
printf 'IOSTAT_ROA_ROWS_PAIR_BUILD_COMPLETE\n'
