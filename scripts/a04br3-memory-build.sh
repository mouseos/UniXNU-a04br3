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
export A04BR3_MEMORY_OUTPUT_ROOT="$RUNNER_TEMP"
export A04BR3_MEMORY_BENCH=1 A04BR3_POWERVR_PROBE=1 A04BR3_POWERVR_METAL=1
export A04BR3_POWERVR_METAL_MODE=publish PYTHONDONTWRITEBYTECODE=1
# Source checks occur before defconfig, tree, trust or any existing artifact write.
python3 "$DEVICE_DIR/scripts/a04br3-memory-manifest.py" preflight
mkdir -p "$A04BR3_MEMORY_OUTPUT_ROOT"
current="$A04BR3_MEMORY_OUTPUT_ROOT/current"
if [ -e "$current" ] && [ ! -L "$current" ]; then
  echo 'Refuse to replace a non-symlink current path' >&2; exit 1
fi
case "${A04BR3_REUSE_DATA_SEED:-0}" in
  0)
    for previous in "$current" "$A04BR3_MEMORY_OUTPUT_ROOT/owner-data" "$A04BR3_MEMORY_OUTPUT_ROOT/data-32.dbr" "$A04BR3_MEMORY_OUTPUT_ROOT/data.dbr" "$A04BR3_MEMORY_OUTPUT_ROOT/data-probe.dbr"; do
      if [ -e "$previous" ] || [ -L "$previous" ]; then
        echo "Existing seed inputs in RUNNER_TEMP; use A04BR3_REUSE_DATA_SEED=1 for a complete compatible seed or choose a fresh RUNNER_TEMP" >&2
        exit 1
      fi
    done ;;
  1) ;;
  *) echo "A04BR3_REUSE_DATA_SEED must be 0 or 1" >&2; exit 1 ;;
esac
# Atomic directory lock serializes cooperating memory builds using this output.
OUT_ROOT=$(python3 -c 'import os; from pathlib import Path; print(Path(os.path.expandvars(os.path.expanduser(os.environ.get("UNIXNU_OUT") or "out"))).resolve())')
mkdir -p "$OUT_ROOT"
lock="$OUT_ROOT/.a04br3-memory-build.lock"
if ! mkdir "$lock" 2>/dev/null; then
  echo "Memory build output is locked: $OUT_ROOT" >&2; exit 1
fi
staging=
cleanup() {
  # Once publication is attempted, retaining an orphan is safer than deleting
  # a run that may already be visible through current.
  if [ -n "$staging" ] && [ -d "$staging" ] && [ ! -e "$staging/.retain-after-publication-attempt" ]; then
    rm -rf -- "$staging"
  fi
  rmdir "$lock" || true
}
trap cleanup EXIT
staging=$(mktemp -d "$A04BR3_MEMORY_OUTPUT_ROOT/.a04br3-memory-run.XXXXXXXX")
if [ "${A04BR3_REUSE_DATA_SEED:-0}" = 1 ]; then
  previous="$A04BR3_MEMORY_OUTPUT_ROOT"
  if [ -L "$current" ]; then previous="$current"; fi
  for name in data.dbr data-32.dbr owner-data/seed.json; do
    if [ ! -s "$previous/$name" ]; then echo "Missing reuse input: $name" >&2; exit 1; fi
  done
  mkdir -p "$staging/owner-data"
  cp -p "$previous/owner-data/seed.json" "$staging/owner-data/seed.json"
  cp -p "$previous/data.dbr" "$staging/data.dbr"
  cp -p "$previous/data-32.dbr" "$staging/data-32.dbr"
fi
export RUNNER_TEMP="$staging" A04BR3_MEMORY_STAGED=1
./unixnu defconfig a04br3_defconfig -D "$DEVICE_DIR"
./unixnu tree -D "$DEVICE_DIR"
python3 "$DEVICE_DIR/scripts/a04br3_tree_scripts.py" --stabilize "$PWD" "$DEVICE_DIR" "$OUT_ROOT/a04br3/tree"
bash "$DEVICE_DIR/scripts/a04br3-pre-build.sh"
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
# Stage and validate the payload before publishing the complete set.
python3 "$DEVICE_DIR/scripts/a04br3-memory-publish.py" stage "$A04BR3_MEMORY_OUTPUT_ROOT" "$staging" "$OUT_ROOT/a04br3/payload"
# A failed or interrupted publication may have switched current already.
# Keep the candidate in either case; a later cleanup can inspect it safely.
: > "$staging/.retain-after-publication-attempt"
python3 "$DEVICE_DIR/scripts/a04br3-memory-publish.py" publish "$A04BR3_MEMORY_OUTPUT_ROOT" "$staging"
staging=
