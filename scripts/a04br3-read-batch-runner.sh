#!/bin/bash
# Run inside a unique SSH-runner directory; launcher redirects full output to
# "$task/io-batch-${BATCH_ATTEMPT}-run.log". Never access or operate a device.
set -euo pipefail
: "${RUNNER_TEMP:?}" "${GITHUB_REPOSITORY:?}" "${BATCH_PREP_COMMIT:?}" "${BATCH_ATTEMPT:?}" "${1:?isolated task directory required}"
original_runner_temp=$RUNNER_TEMP
task=$1
case "$BATCH_PREP_COMMIT" in *[!0-9a-f]*|'') echo 'Invalid preparation commit' >&2; exit 2 ;; esac
[ "${#BATCH_PREP_COMMIT}" -eq 40 ] || exit 2
case "$BATCH_ATTEMPT" in *[!a-zA-Z0-9-]*|'') echo 'Invalid attempt identifier' >&2; exit 2 ;; esac
case "$(basename "$task")" in io-read-batch-e8f-*) ;; *) echo 'Unexpected task directory' >&2; exit 2 ;; esac
[ "$(dirname "$task")" = "$original_runner_temp" ] || { echo 'Task must be a direct child of runner temp' >&2; exit 2; }
[ -d "$task" ] || { echo 'Task directory is missing' >&2; exit 2; }
: "${RELEASE:?launcher must supply release environment}"
[ "$RELEASE" = a04br3-ssh-40876182851 ] || exit 3
status="$task/io-batch-${BATCH_ATTEMPT}-status.txt"
runlog="$task/io-batch-${BATCH_ATTEMPT}-run.log"
finish() {
  rc=$?
  trap - EXIT
  printf 'exit=%s\n' "$rc" > "$status"
  if ! gh release upload "$RELEASE" "$status" "$runlog" -R "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
    echo 'Status/log release upload failed; local runner files retained' >&2
  fi
}
trap finish EXIT
# These clones and all outputs belong exclusively to this task. No shared
# checkout, build tree, Rust installation or original pre-build is modified.
git clone --quiet https://github.com/mouseos/UniXNU.git "$task/source"
git -C "$task/source" checkout --quiet --detach e8f5385e4cee02bb15efa2fb97c20bb59b98d72f
git clone --quiet https://github.com/mouseos/UniXNU-a04br3.git "$task/device"
git -C "$task/device" checkout --quiet --detach 8fde8ba87486338342f3be528823cb46394f2c5b
git clone --quiet https://github.com/mouseos/UniXNU-a04br3.git "$task/preparation"
git -C "$task/preparation" checkout --quiet --detach "$BATCH_PREP_COMMIT"
[ "$(git -C "$task/preparation" rev-parse HEAD)" = "$BATCH_PREP_COMMIT" ] || exit 4
[ "$(shasum -a 256 "$task/preparation/scripts/a04br3-read-batch-runner.sh" | cut -d " " -f1)" = "$(shasum -a 256 "$task/runner.sh" | cut -d " " -f1)" ] || { echo "Runner script does not match fixed preparation source" >&2; exit 4; }
git clone --quiet https://github.com/mouseos/uefi-a04br3.git "$task/uefi"
git -C "$task/uefi" checkout --quiet --detach d3d4f28a89b29553ede7d7523c6af86d7e2459e8
# Official inputs are re-fetched at pinned gitlinks. UEFI needs only these
# submodules to audit its recorded FD; none of the source worktrees is shared.
git -C "$task/source" submodule update --init --recursive
git -C "$task/device" submodule update --init --recursive
[ "$(git -C "$task/source" rev-parse HEAD)" = e8f5385e4cee02bb15efa2fb97c20bb59b98d72f ] || exit 4
[ "$(git -C "$task/device" rev-parse HEAD)" = 8fde8ba87486338342f3be528823cb46394f2c5b ] || exit 4
[ "$(git -C "$task/source/drivers/NativeMsdc" rev-parse HEAD)" = 66d40654ae43ef27090a6d6e1f552be6ea10d24b ] || exit 4
[ "$(git -C "$task/device/drivers/ArmCpu" rev-parse HEAD)" = 85d06a5248a4516c777763d195866a2362e5d7b0 ] || exit 4
[ "$(git -C "$task/uefi" rev-parse HEAD)" = d3d4f28a89b29553ede7d7523c6af86d7e2459e8 ] || exit 4
git -C "$task/uefi" submodule update --init Common/Mu Common/Mu_OEM_Sample Mu_Basecore
mkdir -p "$task/firmware" "$task/official" "$task/tmp" "$task/logs" "$task/rustup" "$task/cargo" "$task/cache"
gh release download "$RELEASE" -R "$GITHUB_REPOSITORY" -D "$task/firmware" -p 'io-batch-fixed-d3d4.fd' -p 'io-batch-fixed-d3d4-manifest.json'
[ "$(shasum -a 256 "$task/firmware/io-batch-fixed-d3d4.fd" | cut -d ' ' -f1)" = 0ab40e82a221651c9c4de1d1fa1ced5f2b5cd3d1250cf387a113d0c0635fdb4f ] || exit 6
[ "$(shasum -a 256 "$task/firmware/io-batch-fixed-d3d4-manifest.json" | cut -d ' ' -f1)" = c65d3198a1a05e2f5b42ae1168ee44f511eecc671b2f812dd00637504c81f52d ] || exit 6
export RUNNER_TEMP="$task/official" DEVICE_DIR="$task/device" UNIXNU_OUT="$task/unixnu-out"
export GITHUB_ENV="$task/official/github.env" TMPDIR="$task/tmp" XDG_CACHE_HOME="$task/cache"
export RUSTUP_HOME="$task/rustup" CARGO_HOME="$task/cargo" PIP_CACHE_DIR="$task/cache/pip" PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="$task/pycache"
export A04BR3_REUSE_DATA_SEED=0 A04BR3_POWERVR_PROBE=1 A04BR3_POWERVR_METAL=1 A04BR3_POWERVR_METAL_MODE=publish
export XNU_IRQ_PC_STATS=1 XNU_MSDC_IRQ_WAIT=1 XNU_MSDC_IRQ_WORKER=1 XNU_MSDC_GIC_NS=1 XNU_PROCESS_EXIT_DIAG=1 XNU_ROOT_TIMING=1 XNU_IO_OVERLAP_TRACE=1
unset UNIXNU_UNIQUE_CHIP_ID XNU_OWNER_DATA_VOLUME_UUID XNU_OWNER_CACHE_VOLUME_UUID RUSTUP_TOOLCHAIN || true
command -v rustup >/dev/null || { echo 'rustup missing; refusing to install into shared home' >&2; exit 5; }
cd "$task/source"
./unixnu defconfig a04br3_defconfig -D "$DEVICE_DIR"
./unixnu fetch -D "$DEVICE_DIR"
# Official pre-build creates a fresh Data seed from the pinned IPSW OS mount,
# and performs the first kernel pass. The second pass resolves PMGR/display.
bash "$DEVICE_DIR/scripts/a04br3-pre-build.sh" > "$task/logs/pre-build.log" 2>&1
[ -s "$RUNNER_TEMP/data.dbr" ] && [ -s "$RUNNER_TEMP/owner-data/seed.json" ] && [ -d "$RUNNER_TEMP/powervr-probe" ] && [ -d "$RUNNER_TEMP/powervr-metal" ] || { echo 'Official pre-build outputs are missing' >&2; exit 7; }
XNU_OWNER_DATA_VOLUME_UUID=$(python3 -I - "$RUNNER_TEMP/owner-data/seed.json" <<'PYUUID'
import json,sys,uuid
print(str(uuid.UUID(json.load(open(sys.argv[1]))['volume_uuid'])).upper())
PYUUID
)
export XNU_OWNER_DATA_VOLUME_UUID
./unixnu build kernel -D "$DEVICE_DIR"
python3 -I "$DEVICE_DIR/scripts/build-a04br3-trust-cache.py" --tree "$task/source" --out "$task/trust-base"
python3 -I "$DEVICE_DIR/scripts/make-a04br3-gpu-addon.py" --probe-dir "$RUNNER_TEMP/powervr-probe" --metal-plugin "$RUNNER_TEMP/powervr-metal" --trust-base "$task/trust-base" --trust-out "$task/trust-owner" --out "$task/gpu-addon"
python3 -I - "$task" <<'PY'
import hashlib,json,os,pathlib,subprocess,sys
r=pathlib.Path(sys.argv[1]);source=r/'source';device=r/'device';out=r/'unixnu-out/a04br3';k=out/'kernel';b=out/'tree/build'
def rev(p):return subprocess.check_output(['git','-C',str(p),'rev-parse','HEAD'],text=True).strip()
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
expected=[(source,'e8f5385e4cee02bb15efa2fb97c20bb59b98d72f'),(device,'8fde8ba87486338342f3be528823cb46394f2c5b'),(device/'drivers/ArmCpu','85d06a5248a4516c777763d195866a2362e5d7b0'),(source/'drivers/NativeMsdc','66d40654ae43ef27090a6d6e1f552be6ea10d24b')]
if any(rev(p)!=value for p,value in expected):raise SystemExit('source revision mismatch')
pm=json.loads((b/'sheng-platform-power/build-report.json').read_text());display=json.loads((b/'native-legacy-display/build-report.json').read_text())
if pm.get('virtual_entries')!=340 or display.get('virtual_entries')!=358 or pm.get('first_pass_stub') is True or display.get('first_pass_stub') is True or display.get('original_candidate_sha256')!=sha(k/'kernel.reference.macho'):raise SystemExit('PMGR/display reference mismatch')
seed=json.loads((r/'official/owner-data/seed.json').read_text());header=(b/'xnu/owner-data-volume.h').read_text()
import re
values=re.findall(r'^#define XNU_OWNER_DATA_VOLUME_UUID \"([^\"]+)\"$', header, re.M)
if values != [seed['volume_uuid'].upper()]:raise SystemExit('kernel Data UUID header mismatch')
flags=('XNU_IRQ_PC_STATS','XNU_MSDC_IRQ_WAIT','XNU_MSDC_IRQ_WORKER','XNU_MSDC_GIC_NS','XNU_PROCESS_EXIT_DIAG','XNU_ROOT_TIMING','XNU_IO_OVERLAP_TRACE')
record={'source_commit':expected[0][1],'device_commit':expected[1][1],'armcpu_commit':expected[2][1],'native_msdc_commit':expected[3][1],'kernel_sha256':sha(k/'kernel.macho'),'reference_kernel_sha256':sha(k/'kernel.reference.macho'),'pmgr_build_report':pm,'display_build_report':display,'data_uuid':seed['volume_uuid'],'kernel_build_flags':{x:os.environ[x] for x in flags},'pre_build_log_sha256':sha(r/'logs/pre-build.log'),'xcode':subprocess.check_output(['xcodebuild','-version'],text=True).strip(),'hardware_tested':False}
(r/'kernel-provenance.json').write_text(json.dumps(record,indent=2)+'\n')
PY
python3 -I "$task/preparation/scripts/a04br3-read-batch-record-inputs.py" --source "$task/source" --device "$task/device" --uefi "$task/uefi" --kernel "$UNIXNU_OUT/a04br3/kernel/kernel.macho" --data "$RUNNER_TEMP/data.dbr" --trust "$task/trust-owner" --gpu "$task/gpu-addon" --fd "$task/firmware/io-batch-fixed-d3d4.fd" --dtb "$task/uefi/Resources/DTBs/a04br3.dtb" --kernel-provenance "$task/kernel-provenance.json" --firmware-provenance "$task/firmware/io-batch-fixed-d3d4-manifest.json" --seed-provenance "$RUNNER_TEMP/owner-data/seed.json" --pre-build-log "$task/logs/pre-build.log" --out "$task/input-records"
python3 -I "$task/preparation/scripts/a04br3-read-batch-build.py" --source "$task/source" --device "$task/device" --uefi "$task/uefi" --inputs "$task/input-records/inputs.json" --out "$task/pair" --log-dir "$task/logs"
# Include enough first-party records for independent local provenance review.
[ -f "$task/pair/pair.json" ] || { echo 'Completed pair missing' >&2; exit 8; }
mkdir -p "$task/pair/source-records"
lock="$UNIXNU_OUT/a04br3/tree/build/native-metal-bridge/Cargo.lock"
[ -s "$lock" ] || { echo 'Resolved Rust lockfile missing' >&2; exit 8; }
cp "$lock" "$task/pair/source-records/Cargo.lock"
shasum -a 256 "$task/pair/source-records/Cargo.lock" > "$task/pair/source-records/Cargo.lock.sha256"
cp "$task/kernel-provenance.json" "$task/firmware/io-batch-fixed-d3d4-manifest.json" "$RUNNER_TEMP/owner-data/seed.json" "$task/input-records/inputs.json" "$task/input-records/data-provenance.json" "$task/logs/pre-build.log" "$task/pair/source-records/"
archive="$task/io-batch-${BATCH_ATTEMPT}-artifacts.tgz"
tar -C "$task" -czf "$archive" pair
gh release upload "$RELEASE" "$archive" -R "$GITHUB_REPOSITORY"
echo IO_BATCH_E8F_COMPLETE
