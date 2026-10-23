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
# Workflow input: pre_build = bash "$DEVICE_DIR/scripts/a04br3-pre-build.sh"
#   with the probe: pre_build = A04BR3_POWERVR_PROBE=1 bash "$DEVICE_DIR/scripts/a04br3-pre-build.sh", owner_trust_cache = true
set -euo pipefail
: "${DEVICE_DIR:?}" "${RUNNER_TEMP:?}"
python3 "$DEVICE_DIR/scripts/build-a04br3-trust-cache.py" --tree "$PWD" --out "$RUNNER_TEMP/trustcache"
./unixnu tree -D "$DEVICE_DIR"
TREE=$PWD/out/a04br3/tree
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
if [ "${A04BR3_POWERVR_PROBE:-0}" = 1 ]; then
  PROBE_VULKAN=()
  if [ "${A04BR3_POWERVR_VULKAN:-0}" = 1 ]; then
    python3 "$DEVICE_DIR/scripts/build-pvr-darwin.py" --target ios
    PROBE_VULKAN=(--vulkan)
  fi
  # ${a[@]+...}: an empty array under set -u in the bash 3.2 of macOS.
  python3 "$DEVICE_DIR/scripts/build-a04br3-powervr-probe.py" --out "$RUNNER_TEMP/powervr-probe" ${PROBE_VULKAN[@]+"${PROBE_VULKAN[@]}"} \
    --trust-base "$RUNNER_TEMP/trustcache" --trust-out "$RUNNER_TEMP/trustcache-owner"
  python3 "$DEVICE_DIR/scripts/a04br3-seed-powervr-probe.py" --tree "$TREE" --probe-dir "$RUNNER_TEMP/powervr-probe" \
    "$RUNNER_TEMP/data.dbr" "$RUNNER_TEMP/data-probe.dbr"
  mv "$RUNNER_TEMP/data-probe.dbr" "$RUNNER_TEMP/data.dbr"
  mv "$RUNNER_TEMP/trustcache-owner" "$RUNNER_TEMP/trustcache"
  echo "PowerVR probe staged; the payload step needs owner_trust_cache = true (--owner-trust-cache)"
fi
# The kernel's owner Data mount (CONFIG_XNU_OWNER_DATA_MOUNT) selects this seed's volume by UUID.
DATA_UUID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["volume_uuid"])' "$RUNNER_TEMP/owner-data/seed.json")
test -n "$DATA_UUID"
echo "XNU_OWNER_DATA_VOLUME_UUID=$DATA_UUID" >> "${GITHUB_ENV:-/dev/null}"
export XNU_OWNER_DATA_VOLUME_UUID=$DATA_UUID
echo "Data seed volume UUID $DATA_UUID"
ls -l "$RUNNER_TEMP/trustcache" "$RUNNER_TEMP/data.dbr"
# First kernel pass: the PMGR bridge (scripts/build-platform-power.py) and the display (scripts/build-a04br3-legacy-display.py)
# check themselves against a previous kernel of this tree and link no-ops on a new tree, as UniXNU-tb128fu documents for the
# bridge; the workflow's kernel step then links both.
hdiutil detach "$MOUNT" || true
trap - EXIT
./unixnu build kernel
