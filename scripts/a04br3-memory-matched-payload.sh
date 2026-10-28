#!/usr/bin/env bash
# Build a comparison payload from verified, separately staged iostat4 inputs.
set -euo pipefail
: "${DEVICE_DIR:?}" "${RUNNER_TEMP:?}"
base="$RUNNER_TEMP/a04br3-iostat4-baseline"
work="$RUNNER_TEMP/a04br3-memory-matched"
[ "${XNU_OWNER_DATA_VOLUME_UUID:-}" = CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36 ] || { echo 'baseline Data UUID mismatch' >&2; exit 1; }
[ -s "$work/matched-inputs.json" ] || { echo 'matched inputs not prepared' >&2; exit 1; }
python3 "$DEVICE_DIR/scripts/a04br3-memory-matched-inputs.py" --verify --baseline "$base" --bench-dir "$RUNNER_TEMP/a04br3-memory-matched-bench" --out "$work"
./unixnu build payload -D "$DEVICE_DIR" --data-ramdisk "$base/scratch-data.img" \
  --owner-trust-cache "$work/trustcache" --gpu-addon "$work/gpuaddon" \
  --kernel-arg sheng-fb-gpu=1 --kernel-arg a04br3-charger-owner=1 --kernel-arg sheng-vsync=60 \
  --kernel-arg vm_compressor=2 --kernel-arg a04br3-home-perf=1 --kernel-arg a04br3-proclog=10 \
  --kernel-arg native-msdc-irq=1 --kernel-arg a04br3-gpu-clock=380 --kernel-arg native-msdc-iostat=1 \
  --kernel-arg mb=1 --omit-kernel-arg a04br3-powervrprobed --omit-kernel-arg a04br3-gpu-packet \
  --omit-kernel-arg offline-hid-auto-home --omit-kernel-arg offline-hid-auto-home-count --omit-kernel-arg=-no_corpses

python3 "$DEVICE_DIR/scripts/a04br3-memory-matched-verify.py"
