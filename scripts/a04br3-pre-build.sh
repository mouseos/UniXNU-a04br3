#!/usr/bin/env bash
# pre_build of the UniXNU macOS workflow for a04br3 (runs in the UniXNU checkout, DEVICE_DIR = this repository):
#  1. the original trust cache bundle -> $RUNNER_TEMP/trustcache (scripts/build-a04br3-trust-cache.py, pinned hashes);
#  2. a Data seed -> $RUNNER_TEMP/data.dbr: the base scripts/prepare-owner-data-fixture.py seeds a 32 MiB APFS volume from the
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
ls -l "$RUNNER_TEMP/trustcache" "$RUNNER_TEMP/data.dbr"
