#!/usr/bin/env bash
# One development round from the SSH build of the base repository (.github/workflows/a04br3-ssh.yml of UniXNU): wait for
# payload-<tag>.img on the release a04br3-ssh-<run id>, build the boot image with the official "unixnu bootimg" and the
# uefi-a04br3 firmware, then write and boot it once with scripts/run-a04br3-unattended.sh (MODE=flash, no mtkclient).
#
# The SSH session itself rebuilds a device ref with /tmp/a04br3-rebuild.sh <ref> (written by that workflow), which publishes
# payload-<short sha>.img; this script is the host half.
#
# usage: UNIXNU=<UniXNU checkout> UEFI=<uefi-a04br3 checkout> [UART_LOG=<collector file> | UART=/dev/ttyUSB0]
#        [REPO=mouseos/UniXNU] [OUT=<directory>] scripts/a04br3-ssh-round.sh RUN_ID TAG
# LK refuses fastboot writes while it measures a low battery ("low power, need battery charging"); nothing is written then,
# so the round is retried every 300 s (up to 3 hours).
set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUN=${1:?Actions run id of a04br3-ssh.yml}
TAG=${2:?short sha of the device ref}
REPO=${REPO:-mouseos/UniXNU}
UNIXNU=${UNIXNU:?UniXNU checkout}
UEFI=${UEFI:?uefi-a04br3 checkout}
OUT=${OUT:-$ROOT/out/a04br3/ssh/$RUN-$TAG}
mkdir -p "$OUT"
until gh release download "a04br3-ssh-$RUN" -R "$REPO" -p "payload-$TAG.img" -D "$OUT" --clobber 2>/dev/null; do sleep 30; done
sha256sum "$OUT/payload-$TAG.img"
(cd "$UNIXNU" && ./unixnu bootimg -D "$ROOT" --out "$OUT/final" --uefi "$UEFI" --dtb "$UEFI/Resources/DTBs/a04br3.dtb" \
  --ramdisk "$OUT/payload-$TAG.img") || exit 1
IMG=$OUT/final/a04br3/boot/boot.img
sha256sum "$IMG"
for attempt in $(seq 1 36); do
  MODE=flash "$ROOT/scripts/run-a04br3-unattended.sh" "$IMG" 1200
  rc=$?
  last=$(ls -td "$ROOT"/out/a04br3/unattended/*/ | head -1)
  if grep -q "low power" "$last/round-1/fastboot.log" 2>/dev/null; then echo "$(date +%T) LK reports low battery, retry in 300 s"; sleep 300; continue; fi
  exit $rc
done
exit 1
