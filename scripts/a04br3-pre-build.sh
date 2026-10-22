#!/usr/bin/env bash
# pre_build of the UniXNU macOS workflow for a04br3 (runs in the UniXNU checkout, DEVICE_DIR = this repository):
#  1. the original trust cache bundle -> $RUNNER_TEMP/trustcache (scripts/build-a04br3-trust-cache.py, pinned hashes);
#  2. a Data seed -> $RUNNER_TEMP/data.dbr (its volume UUID -> XNU_OWNER_DATA_VOLUME_UUID of the kernel step): the base scripts/prepare-owner-data-fixture.py seeds a 32 MiB APFS volume from the
#     IPSW OS volume's private/var (attached read-only with hdiutil), drivers/DarwinBoot/Test/packed_ramdisk.py packs it as DBRDISK1.
# Workflow input: pre_build = bash "$DEVICE_DIR/scripts/a04br3-pre-build.sh"
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
python3 "$TREE/drivers/DarwinBoot/Test/packed_ramdisk.py" "$RUNNER_TEMP/owner-data/owner-data-32.cdr" "$RUNNER_TEMP/data.dbr"
# The kernel's owner Data mount (CONFIG_XNU_OWNER_DATA_MOUNT) selects this seed's volume by UUID.
DATA_UUID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["volume_uuid"])' "$RUNNER_TEMP/owner-data/seed.json")
test -n "$DATA_UUID"
echo "XNU_OWNER_DATA_VOLUME_UUID=$DATA_UUID" >> "${GITHUB_ENV:-/dev/null}"
export XNU_OWNER_DATA_VOLUME_UUID=$DATA_UUID
echo "Data seed volume UUID $DATA_UUID"
ls -l "$RUNNER_TEMP/trustcache" "$RUNNER_TEMP/data.dbr"
# First kernel pass: the PMGR bridge (scripts/build-platform-power.py) checks itself against a previous kernel of this
# tree and links a no-op on a new tree, as UniXNU-tb128fu documents; the workflow's kernel step then links the bridge.
hdiutil detach "$MOUNT" || true
trap - EXIT
./unixnu build kernel
