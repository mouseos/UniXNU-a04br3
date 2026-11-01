#!/usr/bin/env bash
# Invoke from the UniXNU source root after the board's opt-in pre-build and kernel.
# Inputs are produced by a04br3-pre-build.sh, never supplied as user file paths.
set -euo pipefail
: "${DEVICE_DIR:?}" "${RUNNER_TEMP:?}"
python3 "$DEVICE_DIR/scripts/a04br3-memory-manifest.py" verify
# The command line is limited to 1023 bytes (UniXNU check_args). mb=1 is the short form of a04br3-membench=1
# (package/A04BR3ProbeLaunch.c); a04br3-charger-owner is not passed: the kernel takes ownership from the firmware's
# /chosen/a04br3-charger-owner and ignores the argument (package/A04BR3Charger.cpp).
./unixnu build payload -D "$DEVICE_DIR" \
  --kernel "$RUNNER_TEMP/kernel.macho" --owner-trust-cache "$RUNNER_TEMP/trustcache" --data-ramdisk "$RUNNER_TEMP/data.dbr" \
  --kernel-arg mb=1 --kernel-arg sheng-fb-gpu=1 \
  --kernel-arg sheng-vsync=60 \
  --kernel-arg a04br3-home-perf=1 --kernel-arg a04br3-proclog=10 \
  --kernel-arg native-msdc-irq=1 --kernel-arg native-msdc-iostat=1 --kernel-arg a04br3-gpu-clock=380 \
  --omit-kernel-arg a04br3-powervrprobed --omit-kernel-arg a04br3-gpu-packet \
  --omit-kernel-arg offline-hid-auto-home --omit-kernel-arg offline-hid-auto-home-count \
  --omit-kernel-arg=-no_corpses "$@"
