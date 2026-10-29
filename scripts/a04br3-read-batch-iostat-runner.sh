#!/bin/bash
# Isolated incremental iostat payload/boot build; no device access.
set -euo pipefail
: "${RUNNER_TEMP:?}" "${BATCH_PREP_COMMIT:?}" "${1:?task directory required}"
root=$RUNNER_TEMP
task=$1
case "$BATCH_PREP_COMMIT" in *[!0-9a-f]*|'') echo 'Invalid preparation commit' >&2; exit 2 ;; esac
[ "${#BATCH_PREP_COMMIT}" -eq 40 ] || exit 2
[ "$(dirname "$task")" = "$root" ] || { echo 'Task must be a direct child of runner temp' >&2; exit 2; }
case "$(basename "$task")" in io-read-batch-iostat-*) ;; *) echo 'Unexpected task directory' >&2; exit 2 ;; esac
[ -d "$task" ] && [ ! -L "$task" ] || { echo 'Missing or symlinked task directory' >&2; exit 2; }
prior="$root/io-read-batch-e8f-20261029b"
[ -d "$prior" ] && [ ! -L "$prior" ] && [ -s "$prior/input-records/inputs.json" ] || { echo 'Recorded fixed inputs unavailable' >&2; exit 3; }
[ ! -e "$task/preparation" ] && [ ! -e "$task/pair" ] || { echo 'Task already used' >&2; exit 3; }
mkdir "$task/tmp" "$task/cache" "$task/logs" "$task/pycache" "$task/records" "$task/inputs"
# No inherited Python, Git, SDK or credentials variables reach the children.
# Source, device and UEFI clones, submodule metadata, CLI caches and Git objects
# are private to this task, even when the CLI runs submodule update or git add.
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
"${clean[@]}" git -C "$task/preparation" checkout --quiet --detach "$BATCH_PREP_COMMIT"
[ "$("${clean[@]}" git -C "$task/preparation" rev-parse HEAD)" = "$BATCH_PREP_COMMIT" ] || exit 4
[ "$(shasum -a 256 "$task/preparation/scripts/a04br3-read-batch-iostat-runner.sh" | cut -d ' ' -f1)" = "$(shasum -a 256 "$task/runner.sh" | cut -d ' ' -f1)" ] || { echo 'Runner source hash mismatch' >&2; exit 4; }
# Freeze and pin the previously retrieved input record and every provenance
# document before validation. Large inputs remain read-only until the builder
# copies and rechecks each SHA-256 in its own private staging directory.
"${clean[@]}" python3 -I - "$prior" "$task" <<'PY'
import hashlib,json,pathlib,shutil,sys
prior,task=map(pathlib.Path,sys.argv[1:]); rec=task/'records'
def frozen(src,dst,expected):
 data=src.read_bytes()
 if hashlib.sha256(data).hexdigest()!=expected:raise SystemExit('fixed input record mismatch: '+src.name)
 dst.write_bytes(data)
original=prior/'input-records/inputs.json'; target=rec/'original-inputs.json'
frozen(original,target,'8e7fa7df4f4fd06f48a66715d9733aea0d6c6e127d104e53f21e0ce6d7be946c')
manifest=json.loads(target.read_bytes())
records={'kernel_provenance':'0a5ee057b285a6b0777979e416150abba2c2119b0a5e932dc3547201960266a6','firmware_provenance':'c65d3198a1a05e2f5b42ae1168ee44f511eecc671b2f812dd00637504c81f52d','data_provenance':'743a99e30ac6fa1a613a309b292ed456fee0b5e5ec7b961805b84327a64cec93','seed_provenance':'bd75ec1b2171421689f199541fb313521df5d780f0f5b1a4e2162a583e96e010','pre_build_log':'d855665aed2ccda07b06e1f5f0e5ca1651899d9c1a4eb1061ab04450fc8520fb'}
for key,digest in records.items():
 dst=rec/(key+'.json' if key!='pre_build_log' else 'pre_build.log')
 frozen(pathlib.Path(manifest[key]),dst,digest);manifest[key]=str(dst)
# Freeze all large inputs before UUID and provenance validation. The builder
# subsequently makes and checks its own copies before using them.
for name,entry in manifest['files'].items():
 if name=='dtb':
  entry['path']=str(task/'uefi/Resources/DTBs/a04br3.dtb');continue
 src=pathlib.Path(entry['path']);dst=task/'inputs'/name
 shutil.copyfile(src,dst)
 h=hashlib.sha256()
 with dst.open('rb') as f:
  for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
 if h.hexdigest()!=entry['sha256']:raise SystemExit('fixed input mismatch: '+name)
 entry['path']=str(dst)
(rec/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
PY
"${clean[@]}" python3 -I "$task/preparation/scripts/a04br3-read-batch-build.py" \
  --source "$task/source" --device "$task/device" --uefi "$task/uefi" \
  --inputs "$task/records/inputs.json" --out "$task/pair-building" --log-dir "$task/logs" --measurement iostat
# Ship exactly the independently hash-pinned records used by the builder.
cp -R "$task/records" "$task/pair-building/source-records"
diff -qr "$task/records" "$task/pair-building/source-records" >/dev/null
[ ! -e "$task/pair" ] || { echo 'Completed output already exists' >&2; exit 5; }
mv "$task/pair-building" "$task/pair"
printf 'IOSTAT_PAIR_BUILD_COMPLETE\n'
