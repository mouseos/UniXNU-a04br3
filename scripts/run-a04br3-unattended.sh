#!/usr/bin/env bash
# Unattended a04br3 test cycle without mtkclient (after scripts/run-sheng-unattended.sh of UniXNU-sheng).
#
# The test image boots with the board's test cycle on: uefi-a04br3 A04BR3TestCycleDxe (A04BR3_TEST_CYCLE=1) sets the
# LK fastboot flag (PMIC RTC_PDN1 bit 13) and keeps the TOPRGU watchdog running until XNU, and XNU (a04br3-autoreset,
# package/A04BR3AutoReset.cpp) keeps kicking it and resets after the given time. A panic, a hang or the timer therefore
# brings the tablet back to LK fastboot. Each round this script writes the image with fastboot, reboots it, checks that
# the tablet left fastboot, waits for it to come back and keeps the UART log of that round.
#
# usage: [SERIAL=<fastboot serial>] [MODE=flash|boot] [ROUNDS=1] [NEXT_IMAGE_CMD='<command printing a boot image path>']
#        [USERDATA=<userdata image, first round only>] [UART=/dev/ttyUSB0 | UART_LOG=<file appended by another collector>]
#        run-a04br3-unattended.sh BOOT_IMAGE [wait_seconds=1200]
# MODE=flash writes the boot partition and reboots (default); MODE=boot uses fastboot boot (RAM only, leaves the
# partition untouched; needs LK support). Exit status: 0 when every round returned to fastboot by itself, 2 when a round
# did not leave fastboot, 3 when it did not come back within wait_seconds. Logs: out/a04br3/unattended/<time>/round-N/.
set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
IMG=${1:?boot image}
LIMIT=${2:-1200}
MODE=${MODE:-flash}
ROUNDS=${ROUNDS:-1}
listed() { fastboot devices 2>/dev/null | awk '{print $1}' | grep -qx "$SERIAL"; }
echo "waiting for a fastboot device"
until fastboot devices 2>/dev/null | grep -q .; do sleep 2; done
SERIAL=${SERIAL:-$(fastboot devices | awk 'NR==1{print $1}')}
RUNS=$ROOT/out/a04br3/unattended/$(date +%Y%m%d-%H%M%S)
mkdir -p "$RUNS"
for round in $(seq 1 "$ROUNDS"); do
  RUN=$RUNS/round-$round
  mkdir -p "$RUN"
  if [ "$round" -gt 1 ] && [ -n "${NEXT_IMAGE_CMD:-}" ]; then
    IMG=$(bash -c "$NEXT_IMAGE_CMD" | tail -1) || { echo "NEXT_IMAGE_CMD failed" | tee "$RUN/result.txt"; exit 1; }
  fi
  sha256sum "$IMG" > "$RUN/inputs.sha256"
  if [ "$round" -eq 1 ] && [ -n "${USERDATA:-}" ]; then
    sha256sum "$USERDATA" >> "$RUN/inputs.sha256"
    fastboot -s "$SERIAL" flash userdata "$USERDATA" > "$RUN/fastboot-userdata.log" 2>&1 || { echo "userdata flash failed" | tee "$RUN/result.txt"; exit 1; }
  fi
  CAPTURE=
  if [ -n "${UART:-}" ]; then
    stty -F "$UART" 921600 cs8 -cstopb -parenb raw -echo
    cat "$UART" > "$RUN/uart.log" & CAPTURE=$!
  elif [ -n "${UART_LOG:-}" ]; then
    START=$(stat -c %s "$UART_LOG")
  fi
  case "$MODE" in
    flash) fastboot -s "$SERIAL" flash boot "$IMG" > "$RUN/fastboot.log" 2>&1 && fastboot -s "$SERIAL" reboot >> "$RUN/fastboot.log" 2>&1 ;;
    boot)  fastboot -s "$SERIAL" boot "$IMG" > "$RUN/fastboot.log" 2>&1 ;;
    *) echo "MODE must be flash or boot"; exit 1 ;;
  esac || { echo "fastboot $MODE failed" | tee "$RUN/result.txt"; exit 1; }
  # The tablet must leave fastboot within 60 s, otherwise the round did not start.
  left=0
  for _ in $(seq 1 60); do if ! listed; then left=1; break; fi; sleep 1; done
  finish() {
    [ -n "$CAPTURE" ] && kill "$CAPTURE" 2>/dev/null
    if [ -n "${UART_LOG:-}" ]; then tail -c +$((START + 1)) "$UART_LOG" > "$RUN/uart.log"; fi
  }
  if [ "$left" = 0 ]; then finish; echo "round $round: still in fastboot after $MODE" | tee "$RUN/result.txt"; exit 2; fi
  echo "round $round: running, waiting up to ${LIMIT}s for the return to fastboot"
  end=$((SECONDS + LIMIT))
  until listed; do
    if (( SECONDS > end )); then finish; echo "round $round: no return to fastboot within ${LIMIT}s" | tee "$RUN/result.txt"; exit 3; fi
    sleep 2
  done
  finish
  echo "round $round: returned to fastboot by itself after $((SECONDS - end + LIMIT))s" | tee "$RUN/result.txt"
done
echo "logs: $RUNS"
