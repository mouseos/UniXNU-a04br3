#!/usr/bin/env bash
# Called from UniXNU source; baseline comes from pinned comparison assets.
set -euo pipefail
: "${DEVICE_DIR:?}" "${RUNNER_TEMP:?}"
base="$RUNNER_TEMP/a04br3-iostat4-baseline"
work="$RUNNER_TEMP/a04br3-memory-matched"
[ ! -e "$work" ] || { echo 'matched output already exists' >&2; exit 1; }
[ "${XNU_OWNER_DATA_VOLUME_UUID:-}" = CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36 ] || { echo 'baseline Data UUID mismatch' >&2; exit 1; }
[ "${XNU_OWNER_CACHE_VOLUME_UUID:-}" = 8846FE78-2910-4297-AF97-D59BB6F8FC16 ] || { echo 'baseline Cache UUID mismatch' >&2; exit 1; }
python3 "$DEVICE_DIR/scripts/build-a04br3-memory-bench.py" --out "$RUNNER_TEMP/a04br3-memory-matched-bench"
python3 "$DEVICE_DIR/scripts/a04br3-memory-matched-inputs.py" --baseline "$base" --bench-dir "$RUNNER_TEMP/a04br3-memory-matched-bench" --out "$work"
