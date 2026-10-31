#!/usr/bin/env bash
# pre_build of the UniXNU macOS workflow for a04br3 (runs in the UniXNU checkout, DEVICE_DIR = this repository):
#  1. the original trust cache bundle -> $RUNNER_TEMP/trustcache (scripts/build-a04br3-trust-cache.py, pinned hashes);
#  2. a Data seed -> $RUNNER_TEMP/data.dbr (its volume UUID -> XNU_OWNER_DATA_VOLUME_UUID of the kernel step): the base scripts/prepare-owner-data-fixture.py seeds a 32 MiB APFS volume from the
#     IPSW OS volume's private/var (attached read-only with hdiutil), drivers/DarwinBoot/Test/packed_ramdisk.py packs it as DBRDISK1.
#  3. opt-in, A04BR3_POWERVR_PROBE=1: the PowerVR user-space probe (scripts/build-a04br3-powervr-probe.py) staged into that seed
#     (scripts/a04br3-seed-powervr-probe.py), and $RUNNER_TEMP/trustcache replaced by the owner trust bundle that trusts it. The
#     payload step must then take it as an owner bundle: workflow input owner_trust_cache = true (the board allow-list of
#     [payload] trust_cache_sha256 cannot list a bundle that changes with every build). A04BR3_POWERVR_VULKAN=1 also builds Mesa
#     (scripts/build-pvr-darwin.py --target ios) and the probe's Vulkan stage.
#  4. opt-in, A04BR3_POWERVR_METAL=1 (implies the two above): the Metal plugin NativePowerVRMetal.bundle (scripts/build-a04br3-metal-plugin.py:
#     UniXNU's vendor-neutral NativeMetal plugin with package/powervr/user/NativePowerVRMetalBoard.mm and the Mesa driver), staged with the
#     probe and trusted by the same owner module. A04BR3_POWERVR_METAL_MODE=selftest (default) runs the bridge self-test in backboardd and
#     keeps the CPU renderer; =publish makes backboardd composite on the GPU. The kernel needs a04br3-gpu-metal=1 (and sheng-fb-gpu=1 for
#     the GPU copy into the scanout). The bridge needs the pinned metal2vulkan source and Rust 1.98.0 with the aarch64-apple-ios target;
#     both are fetched here.
# A04BR3_REUSE_DATA_SEED=1: rebuild from the retained policy-seeded data-32.dbr,
# preserving its UUID without carrying forward obsolete APFS pages from data.dbr.
# A04BR3_POWERVR_METAL_PIPELINE_DIAG=1: opt in to the existing bounded pipeline
# failure callback in the Metal plugin (requires PROBE=1 and METAL=1).
# A04BR3_POWERVR_METAL_ROA_COLOR=1: opt in to the existing GE8300 COLOR ROA
# implementation in this bundle (requires PROBE=1 and METAL=1; default off).
# Workflow input: pre_build = bash "$DEVICE_DIR/scripts/a04br3-pre-build.sh"
#   with the probe: pre_build = A04BR3_POWERVR_PROBE=1 bash "$DEVICE_DIR/scripts/a04br3-pre-build.sh", owner_trust_cache = true
set -euo pipefail
: "${DEVICE_DIR:?}" "${RUNNER_TEMP:?}"
# Default (the official macos.yml pre_build without variables): the GPU configuration every GPU image of this tablet ran:
# the PowerVR probe, the Metal plugin in publish mode (backboardd composites on the GPU) and the GPU addon
# ($RUNNER_TEMP/gpu-addon, taken by the payload step with the owner trust bundle, $RUNNER_TEMP/trustcache.owner).
# Explicit A04BR3_POWERVR_PROBE / A04BR3_POWERVR_METAL (a04br3-ssh.yml) keep their meaning; A04BR3_GPU=0 builds the
# CPU-only seed of earlier images.
if [ -z "${A04BR3_POWERVR_PROBE+x}" ] && [ -z "${A04BR3_POWERVR_METAL+x}" ]; then
  case "${A04BR3_GPU:-1}" in
    1) export A04BR3_POWERVR_PROBE=1 A04BR3_POWERVR_METAL=1 A04BR3_POWERVR_METAL_MODE="${A04BR3_POWERVR_METAL_MODE:-publish}"
       export A04BR3_GPU_ADDON="${A04BR3_GPU_ADDON:-1}" ;;
    0) ;;
    *) echo "A04BR3_GPU must be 0 or 1" >&2; exit 1 ;;
  esac
fi
case "${A04BR3_GPU_ADDON:-0}" in
  0) ;;
  1) if [ "${A04BR3_POWERVR_PROBE:-0}" != 1 ] || [ "${A04BR3_POWERVR_METAL:-0}" != 1 ]; then
       echo "A04BR3_GPU_ADDON=1 requires A04BR3_POWERVR_PROBE=1 and A04BR3_POWERVR_METAL=1" >&2; exit 1
     fi ;;
  *) echo "A04BR3_GPU_ADDON must be 0 or 1" >&2; exit 1 ;;
esac
case "${A04BR3_POWERVR_PIPELINE_TIMING:-0}" in
  0) ;;
  1) if [ "${A04BR3_POWERVR_PROBE:-0}" != 1 ] || [ "${A04BR3_POWERVR_METAL:-0}" != 1 ]; then
       echo "Pipeline timing requires A04BR3_POWERVR_PROBE=1 and A04BR3_POWERVR_METAL=1" >&2; exit 1
     fi ;;
  *) echo "A04BR3_POWERVR_PIPELINE_TIMING must be 0 or 1" >&2; exit 1 ;;
esac
case "${A04BR3_POWERVR_METAL_PIPELINE_DIAG:-0}" in
  0) ;;
  1)
    if [ "${A04BR3_POWERVR_PROBE:-0}" != 1 ] || [ "${A04BR3_POWERVR_METAL:-0}" != 1 ]; then
      echo "Metal pipeline diagnostics require A04BR3_POWERVR_PROBE=1 and A04BR3_POWERVR_METAL=1" >&2
      exit 1
    fi
    ;;
  *) echo "A04BR3_POWERVR_METAL_PIPELINE_DIAG must be 0 or 1" >&2; exit 1 ;;
esac
case "${A04BR3_POWERVR_METAL_ROA_COLOR:-0}" in
  0) ;;
  1)
    if [ "${A04BR3_POWERVR_PROBE:-0}" != 1 ] || [ "${A04BR3_POWERVR_METAL:-0}" != 1 ]; then
      echo "Metal COLOR ROA requires A04BR3_POWERVR_PROBE=1 and A04BR3_POWERVR_METAL=1" >&2
      exit 1
    fi
    ;;
  *) echo "A04BR3_POWERVR_METAL_ROA_COLOR must be 0 or 1" >&2; exit 1 ;;
esac
case "${A04BR3_MEMORY_BENCH:-0}" in
  0) ;;
  1) if [ "${A04BR3_MEMORY_STAGED:-0}" != 1 ]; then
       echo "Memory benchmark pre-build must use an isolated run directory" >&2; exit 1
     fi
     if [ "${A04BR3_POWERVR_PROBE:-0}" != 1 ] || [ "${A04BR3_POWERVR_METAL:-0}" != 1 ] || [ "${A04BR3_POWERVR_METAL_MODE:-selftest}" != publish ]; then
       echo "Memory benchmark requires PowerVR probe and Metal publish mode" >&2; exit 1
     fi ;;
  *) echo "A04BR3_MEMORY_BENCH must be 0 or 1" >&2; exit 1 ;;
esac
# SSH incremental builds opt in; default builds still create a fresh fixture.
read_seed_uuid() {
  python3 - "$RUNNER_TEMP/owner-data/seed.json" <<'PY_UUID'
import json
from pathlib import Path
import sys
import uuid
try:
    record = json.loads(Path(sys.argv[1]).read_text())
    if not isinstance(record, dict) or not isinstance(record.get('volume_uuid'), str):
        raise ValueError('seed.json must be an object with a string volume_uuid')
    value = uuid.UUID(record['volume_uuid'])
    if not value.int:
        raise ValueError('volume_uuid must not be nil')
except (OSError, ValueError, TypeError) as error:
    raise SystemExit('Invalid Data seed metadata: ' + str(error))
print(str(value).upper())
PY_UUID
}
case "${A04BR3_REUSE_DATA_SEED:-0}" in
  0)
    for previous in "$RUNNER_TEMP/owner-data" "$RUNNER_TEMP/data-32.dbr" "$RUNNER_TEMP/data.dbr" "$RUNNER_TEMP/data-probe.dbr"; do
      if [ -e "$previous" ]; then
        echo "Existing seed inputs in RUNNER_TEMP; use A04BR3_REUSE_DATA_SEED=1 for a complete compatible seed or choose a fresh RUNNER_TEMP" >&2
        exit 1
      fi
    done ;;
  1)
    if [ "${A04BR3_POWERVR_PROBE:-0}" != 1 ]; then
      echo "Data seed reuse requires A04BR3_POWERVR_PROBE=1 for mounted volume UUID validation" >&2
      exit 1
    fi
    if [ ! -f "$RUNNER_TEMP/data.dbr" ] || [ ! -s "$RUNNER_TEMP/data.dbr" ]; then
      echo "Data seed reuse requires an existing nonempty $RUNNER_TEMP/data.dbr" >&2
      exit 1
    fi
    if [ ! -f "$RUNNER_TEMP/data-32.dbr" ] || [ ! -s "$RUNNER_TEMP/data-32.dbr" ]; then
      echo "Data seed reuse requires the retained nonempty $RUNNER_TEMP/data-32.dbr baseline" >&2
      exit 1
    fi
    DATA_UUID=$(read_seed_uuid)
    ;;
  *) echo "A04BR3_REUSE_DATA_SEED must be 0 or 1" >&2; exit 1 ;;
esac
if [ "${A04BR3_MEMORY_BENCH:-0}" = 1 ]; then
  python3 "$DEVICE_DIR/scripts/a04br3-memory-manifest.py" preflight
fi
./unixnu tree -D "$DEVICE_DIR"
# Match UniXNU Context's expanduser/expandvars/resolve semantics for UNIXNU_OUT.
OUT_ROOT=$(python3 -c 'import os; from pathlib import Path; print(Path(os.path.expandvars(os.path.expanduser(os.environ.get("UNIXNU_OUT") or "out"))).resolve())')
TREE=$OUT_ROOT/a04br3/tree
if [ "${A04BR3_MEMORY_BENCH:-0}" = 1 ]; then
  export PYTHONDONTWRITEBYTECODE=1
  python3 "$DEVICE_DIR/scripts/a04br3_tree_scripts.py" --stabilize "$PWD" "$DEVICE_DIR" "$TREE"
fi
rm -f "$RUNNER_TEMP/a04br3-memory-inputs.json"
python3 "$DEVICE_DIR/scripts/build-a04br3-trust-cache.py" --tree "$PWD" --out "$RUNNER_TEMP/trustcache"
MOUNT=
SEED_SCRATCH=
SEED_INPUT="$RUNNER_TEMP/data.dbr"
SEED_OUTPUT="$RUNNER_TEMP/data-probe.dbr"
SEED_IDENTITY=()
if [ "${A04BR3_REUSE_DATA_SEED:-0}" = 1 ]; then
  SEED_IDENTITY=(--expected-volume-uuid "$DATA_UUID")
  # DBRDISK1 preserves every nonzero page, including obsolete APFS/COW blocks.
  # Grow the immutable, policy-seeded baseline instead of editing prior outputs.
  SEED_SCRATCH=$(mktemp -d "$RUNNER_TEMP/a04br3-seed-rebuild.XXXXXX")
  trap 'rm -rf "$SEED_SCRATCH"' EXIT
  SEED_INPUT="$SEED_SCRATCH/data-fresh.dbr"
  SEED_OUTPUT="$SEED_SCRATCH/data-staged.dbr"
  python3 "$TREE/scripts/seed-data-grow.py" --gib 4 "$RUNNER_TEMP/data-32.dbr" "$SEED_INPUT"
fi
if [ "${A04BR3_REUSE_DATA_SEED:-0}" != 1 ]; then
  OS_DMG=$(./unixnu ipsw fetch OS -D "$DEVICE_DIR" | grep '\.dmg$' | tail -1)
  test -f "$OS_DMG"
  MOUNT=$TREE/.cache/firmware/target-volumes/os-mount
  mkdir -p "$MOUNT"
  hdiutil attach -readonly -nobrowse -owners on -mountpoint "$MOUNT" "$OS_DMG"
  trap 'hdiutil detach "$MOUNT" || true' EXIT
  python3 "$TREE/scripts/prepare-owner-data-fixture.py" --output "$RUNNER_TEMP/owner-data"
  # launchd's disabled jobs from board.toml (sheng-disable-watchdogd, sheng-disable-jobs) into the seed, root-owned.
  python3 "$DEVICE_DIR/scripts/a04br3-seed-launch-policy.py" --board "$DEVICE_DIR/board.toml" "$RUNNER_TEMP/owner-data/owner-data-32.cdr"
  python3 "$TREE/drivers/DarwinBoot/Test/packed_ramdisk.py" "$RUNNER_TEMP/owner-data/owner-data-32.cdr" "$RUNNER_TEMP/data-32.dbr"
  # Grow the 32 MiB seed to 4 GiB (UniXNU scripts/seed-data-grow.py, as UniXNU-tb128fu does with 8 GiB; the volume UUID is
  # unchanged); NativeMsdc keeps the changed pages in its store window (native-msdc-persist=2).
  python3 "$TREE/scripts/seed-data-grow.py" --gib 4 "$RUNNER_TEMP/data-32.dbr" "$RUNNER_TEMP/data.dbr"
fi
if [ "${A04BR3_POWERVR_PROBE:-0}" = 1 ]; then
  DATA_UUID=$(read_seed_uuid)
  SEED_IDENTITY=(--expected-volume-uuid "$DATA_UUID")
  if [ "${A04BR3_POWERVR_METAL:-0}" = 1 ]; then A04BR3_POWERVR_VULKAN=1; fi
  PROBE_VULKAN=()
  if [ "${A04BR3_POWERVR_VULKAN:-0}" = 1 ]; then
    # Mesa's host tools (the a04br3-ssh.yml step "Install PowerVR Vulkan host tools"), for the generic macos.yml, which
    # installs only the kernel's tools; build-pvr-darwin.py finds the keg-only formulae itself.
    if command -v brew >/dev/null; then
      for formula in pkgconf bison flex spirv-llvm-translator spirv-tools; do
        brew list --formula "$formula" >/dev/null 2>&1 || brew install "$formula"
      done
    fi
    python3 "$DEVICE_DIR/scripts/build-pvr-darwin.py" --target ios
    PROBE_VULKAN=(--vulkan)
  fi
  METAL_TRUST=()
  METAL_SEED=()
  if [ "${A04BR3_POWERVR_METAL:-0}" = 1 ]; then
    # The pinned translator (UniXNU scripts/data/gpu-metal-vulkan-inputs.json) where build-native-metal-bridge.py reads it.
    M2V=$TREE/.cache/gpu-reference/metal2vulkan
    M2V_PIN=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["translator"]["commit"])' "$TREE/scripts/data/gpu-metal-vulkan-inputs.json")
    if [ ! -d "$M2V/.git" ]; then
      git clone -q --filter=blob:none https://github.com/steelbrain/metal2vulkan "$M2V"
    fi
    git -C "$M2V" -c advice.detachedHead=false checkout -q "$M2V_PIN"
    command -v rustup >/dev/null || { curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain none; }
    export PATH="$HOME/.cargo/bin:$PATH"
    rustup toolchain install 1.98.0 --profile minimal --target aarch64-apple-ios
    METAL_DIAGNOSTICS=()
    if [ "${A04BR3_POWERVR_PIPELINE_TIMING:-0}" = 1 ]; then
      METAL_DIAGNOSTICS+=(--pipeline-timing)
    fi
    if [ "${A04BR3_POWERVR_METAL_PIPELINE_DIAG:-0}" = 1 ]; then
      METAL_DIAGNOSTICS+=(--pipeline-failure-diag)
    fi
    if [ "${A04BR3_POWERVR_METAL_ROA_COLOR:-0}" = 1 ]; then
      METAL_DIAGNOSTICS+=(--roa-color)
    fi
    python3 "$TREE/scripts/build-a04br3-metal-plugin.py" ${METAL_DIAGNOSTICS[@]+"${METAL_DIAGNOSTICS[@]}"} --out "$RUNNER_TEMP/powervr-metal" \
      --vulkan "$DEVICE_DIR/build/mesa-pvr-ios/src/imagination/vulkan/libvulkan_powervr_mesa.dylib"
    METAL_TRUST=(--extra-trust "$RUNNER_TEMP/powervr-metal/trust.json")
    METAL_SEED=(--metal-plugin "$RUNNER_TEMP/powervr-metal" --metal-mode "${A04BR3_POWERVR_METAL_MODE:-selftest}")
  fi
  MEMORY_SEED=()
  if [ "${A04BR3_MEMORY_BENCH:-0}" = 1 ]; then
    python3 "$DEVICE_DIR/scripts/build-a04br3-memory-bench.py" --out "$RUNNER_TEMP/a04br3-memory-bench"
    METAL_TRUST+=(--extra-trust "$RUNNER_TEMP/a04br3-memory-bench/trust.json")
    MEMORY_SEED=(--memory-bench "$RUNNER_TEMP/a04br3-memory-bench/a04br3-memory-copy-bench")
  fi
  # ${a[@]+...}: an empty array under set -u in the bash 3.2 of macOS.
  python3 "$DEVICE_DIR/scripts/build-a04br3-powervr-probe.py" --out "$RUNNER_TEMP/powervr-probe" ${PROBE_VULKAN[@]+"${PROBE_VULKAN[@]}"} \
    --trust-base "$RUNNER_TEMP/trustcache" --trust-out "$RUNNER_TEMP/trustcache-owner" ${METAL_TRUST[@]+"${METAL_TRUST[@]}"}
  python3 "$DEVICE_DIR/scripts/a04br3-seed-powervr-probe.py" --tree "$TREE" --probe-dir "$RUNNER_TEMP/powervr-probe" \
    ${METAL_SEED[@]+"${METAL_SEED[@]}"} ${MEMORY_SEED[@]+"${MEMORY_SEED[@]}"} ${SEED_IDENTITY[@]+"${SEED_IDENTITY[@]}"} "$SEED_INPUT" "$SEED_OUTPUT"
  if [ "${A04BR3_GPU_ADDON:-0}" = 1 ]; then
    # The NGPUADD2 addon of the same signed files (scripts/make-a04br3-gpu-addon.py), from the original pinned bundle
    # ($RUNNER_TEMP/trustcache is not replaced yet). Its trust output is not used: the owner bundle of the seed above
    # already carries the same CDHashes.
    ADDON_SCRATCH=$(mktemp -d "$RUNNER_TEMP/a04br3-gpu-addon.XXXXXX")
    python3 "$DEVICE_DIR/scripts/make-a04br3-gpu-addon.py" --probe-dir "$RUNNER_TEMP/powervr-probe" \
      --metal-plugin "$RUNNER_TEMP/powervr-metal" --trust-base "$RUNNER_TEMP/trustcache" \
      --trust-out "$ADDON_SCRATCH/trustcache-addon" --out "$ADDON_SCRATCH/gpu-addon"
    mv -f "$ADDON_SCRATCH/gpu-addon" "$RUNNER_TEMP/gpu-addon"
    rm -rf "$ADDON_SCRATCH"
  else
    rm -f "$RUNNER_TEMP/gpu-addon"
  fi
  mv "$SEED_OUTPUT" "$RUNNER_TEMP/data.dbr"
  mv "$RUNNER_TEMP/trustcache-owner" "$RUNNER_TEMP/trustcache"
  # The generic macos.yml takes a trust cache next to this marker as an owner bundle (as owner_trust_cache = true).
  : > "$RUNNER_TEMP/trustcache.owner"
  echo "PowerVR probe staged; the payload step needs owner_trust_cache = true (--owner-trust-cache)"
else
  rm -f "$RUNNER_TEMP/gpu-addon" "$RUNNER_TEMP/trustcache.owner"
fi
# The kernel's owner Data mount (CONFIG_XNU_OWNER_DATA_MOUNT) selects this seed's volume by UUID.
DATA_UUID=$(read_seed_uuid)
test -n "$DATA_UUID"
echo "XNU_OWNER_DATA_VOLUME_UUID=$DATA_UUID" >> "${GITHUB_ENV:-/dev/null}"
export XNU_OWNER_DATA_VOLUME_UUID=$DATA_UUID
# Also write it to a file that SSH rebuild scripts source, because a kept-alive SSH runner
# does not inherit GITHUB_ENV exports from the original pre_build step.
mkdir -p "$RUNNER_TEMP"
echo "export XNU_OWNER_DATA_VOLUME_UUID=$DATA_UUID" > "$RUNNER_TEMP/a04br3-data.env"
echo "Data seed volume UUID $DATA_UUID"
ls -l "$RUNNER_TEMP/trustcache" "$RUNNER_TEMP/data.dbr"
# First kernel pass: the PMGR bridge (scripts/build-platform-power.py) and the display (scripts/build-a04br3-legacy-display.py)
# check themselves against a previous kernel of this tree and link no-ops on a new tree, as UniXNU-tb128fu documents for the
# bridge; the workflow's kernel step then links both.
if [ -n "$MOUNT" ]; then
  hdiutil detach "$MOUNT" || true
  trap - EXIT
fi
if [ -n "$SEED_SCRATCH" ]; then
  rm -rf "$SEED_SCRATCH"
  trap - EXIT
fi
./unixnu build kernel
