#!/usr/bin/env bash
# Invoke from the UniXNU source root after the board's opt-in pre-build and kernel.
# Inputs are produced by a04br3-pre-build.sh, never supplied as user file paths.
set -euo pipefail
: "${DEVICE_DIR:?}" "${RUNNER_TEMP:?}"
python3 - "$RUNNER_TEMP" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
marker = json.loads((root / 'a04br3-memory-inputs.json').read_text())
for key, name in [('data_sha256', 'data.dbr'), ('trust_sha256', 'trustcache')]:
    if hashlib.sha256((root / name).read_bytes()).hexdigest() != marker[key]:
        raise SystemExit('memory payload input changed since opt-in pre-build: ' + name)
PY
./unixnu build payload -D "$DEVICE_DIR" \
  --owner-trust-cache "$RUNNER_TEMP/trustcache" --data-ramdisk "$RUNNER_TEMP/data.dbr" \
  --kernel-arg a04br3-membench=1 --kernel-arg sheng-fb-gpu=1 \
  --kernel-arg a04br3-charger-owner=1 --kernel-arg sheng-vsync=60 \
  --kernel-arg a04br3-home-perf=1 --kernel-arg a04br3-proclog=10 \
  --kernel-arg native-msdc-irq=1 --kernel-arg a04br3-gpu-clock=380 \
  --omit-kernel-arg a04br3-powervrprobed --omit-kernel-arg a04br3-gpu-packet \
  --omit-kernel-arg offline-hid-auto-home --omit-kernel-arg offline-hid-auto-home-count \
  --omit-kernel-arg=-no_corpses "$@"
