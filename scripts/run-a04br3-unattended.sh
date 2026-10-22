#!/usr/bin/env bash
# Unattended a04br3 test cycle without mtkclient (after scripts/run-sheng-unattended.sh of UniXNU-sheng).
#
# The test payload boots with a04br3-autoreset=<seconds> (package/A04BR3AutoReset.cpp): the kernel sets the LK fastboot flag
# (PMIC RTC_PDN1 bit 13) and arms the TOPRGU watchdog, so the timer, a panic or a hang all bring the tablet back to LK
# fastboot. This script waits for fastboot, writes the next image, reboots it, cuts the UART log of that run and waits for
# the tablet to return to fastboot by itself.
#
# usage: [SERIAL=<fastboot serial>] [MODE=flash|boot] [USERDATA=<userdata image>] [UART=/dev/ttyUSB0 | UART_LOG=<file>]
#        run-a04br3-unattended.sh BOOT_IMAGE [wait_seconds=600]
# UART: capture the console (921600 8N1) directly. UART_LOG: an existing capture file another collector appends to; the
# run's bytes are cut from it by offset. Logs go to out/a04br3/unattended/<time>/.
set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
IMG=${1:?boot image}
LIMIT=${2:-600}
MODE=${MODE:-flash}
present() { fastboot devices 2>/dev/null | grep -q "^${SERIAL:-}"; }
echo "waiting for a fastboot device"
until fastboot devices 2>/dev/null | grep -q .; do sleep 2; done
SERIAL=${SERIAL:-$(fastboot devices | awk 'NR==1{print $1}')}
RUN=$ROOT/out/a04br3/unattended/$(date +%Y%m%d-%H%M%S)
mkdir -p "$RUN"
sha256sum "$IMG" ${USERDATA:+"$USERDATA"} > "$RUN/inputs.sha256"
if [ -n "${USERDATA:-}" ]; then
  fastboot -s "$SERIAL" flash userdata "$USERDATA" 2>&1 | tee "$RUN/fastboot-userdata.log" || exit 1
fi
CAPTURE_PID=
if [ -n "${UART:-}" ]; then
  stty -F "$UART" 921600 cs8 -cstopb -parenb raw -echo
  cat "$UART" > "$RUN/uart.log" & CAPTURE_PID=$!
elif [ -n "${UART_LOG:-}" ]; then
  START=$(stat -c %s "$UART_LOG")
fi
case "$MODE" in
  flash) fastboot -s "$SERIAL" flash boot "$IMG" 2>&1 | tee "$RUN/fastboot-boot.log" && fastboot -s "$SERIAL" reboot ;;
  boot)  fastboot -s "$SERIAL" boot "$IMG" 2>&1 | tee "$RUN/fastboot-boot.log" ;;
  *) echo "MODE must be flash or boot"; exit 1 ;;
esac || exit 1
sleep 8
while present; do sleep 1; done
echo "test image running; waiting up to ${LIMIT}s for the tablet to return to fastboot"
end=$((SECONDS + LIMIT)); manual=0
until present; do
  if (( SECONDS > end && manual == 0 )); then
    manual=1
    echo "AUTORESET DID NOT HAPPEN within ${LIMIT}s" | tee "$RUN/result.txt"
  fi
  sleep 2
done
[ -n "$CAPTURE_PID" ] && kill "$CAPTURE_PID"
if [ -n "${UART_LOG:-}" ]; then tail -c +$((START + 1)) "$UART_LOG" > "$RUN/uart.log"; fi
(( manual )) || echo "returned to fastboot by itself" | tee "$RUN/result.txt"
echo "log: $RUN/uart.log"
