#!/usr/bin/env bash
# Run from the UniXNU source root on macOS with the normal iOS build prerequisites.
set -euo pipefail
if [ "${1:-}" != --enable-memory-bench ]; then
  echo 'usage: a04br3-memory-build.sh --enable-memory-bench [--skip-setup]' >&2; exit 2
fi
shift
if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != --skip-setup ]; }; then
  echo 'only --skip-setup is supported' >&2; exit 2
fi
export DEVICE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export RUNNER_TEMP="${RUNNER_TEMP:-$PWD/out/a04br3-memory-inputs}"
mkdir -p "$RUNNER_TEMP"
rm -f "$RUNNER_TEMP/a04br3-memory-inputs.json" "$RUNNER_TEMP/a04br3-memory-pending.json"
export A04BR3_MEMORY_BENCH=1 A04BR3_POWERVR_PROBE=1 A04BR3_POWERVR_METAL=1
export A04BR3_POWERVR_METAL_MODE=publish
./unixnu defconfig a04br3_defconfig -D "$DEVICE_DIR"
./unixnu tree -D "$DEVICE_DIR"
bash "$DEVICE_DIR/scripts/a04br3-pre-build.sh"
# Parse data as JSON, not executable shell text; never inherit a stale parent UUID.
XNU_OWNER_DATA_VOLUME_UUID=$(python3 - "$RUNNER_TEMP/owner-data/seed.json" <<'PY_UUID'
import json, sys, uuid
value = uuid.UUID(json.load(open(sys.argv[1]))['volume_uuid'])
if not value.int:
    raise SystemExit('nil Data UUID')
print(str(value).upper())
PY_UUID
)
export XNU_OWNER_DATA_VOLUME_UUID
python3 "$DEVICE_DIR/scripts/a04br3-memory-manifest.py" capture
./unixnu build kernel -D "$DEVICE_DIR"
python3 "$DEVICE_DIR/scripts/a04br3-memory-manifest.py" seal
bash "$DEVICE_DIR/scripts/a04br3-memory-payload.sh" "$@"
