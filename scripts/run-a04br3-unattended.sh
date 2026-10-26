#!/usr/bin/env bash
# Unattended a04br3 test cycle without mtkclient (after scripts/run-sheng-unattended.sh of UniXNU-sheng).
#
# The test image boots with the board's test cycle on: uefi-a04br3 (A04BR3_TEST_CYCLE=1) sets the LK fastboot flag
# (PMIC RTC_PDN1 bit 13) and arms the TOPRGU watchdog from SEC on (A04BR3PlatformSecBoardLib), A04BR3TestCycleDxe keeps
# it running until XNU, and XNU (a04br3-autoreset,
# package/A04BR3AutoReset.cpp) keeps kicking it and resets after the given time. A panic, a hang or the timer therefore
# brings the tablet back to LK fastboot. Each round this script writes the image with fastboot, reboots it, checks that
# the tablet left fastboot, waits for it to come back and keeps the UART log of that round.
#
# usage: [SERIAL=<fastboot serial>] [MODE=auto|boot|flash] [ROUNDS=1] [NEXT_IMAGE_CMD='<command printing a boot image path>']
#        [USERDATA=<userdata image, first round only>] [UART=/dev/ttyUSB0 | UART_LOG=<file appended by another collector>]
#        run-a04br3-unattended.sh BOOT_IMAGE [wait_seconds=1200]
# MODE=flash (default) writes the boot partition and reboots. MODE=boot uses fastboot boot and MODE=auto tries it first,
# but the stock LK does not hand a fastboot-booted ramdisk to the kernel slot at its header address (observed: /chosen
# initrd 0x1FE7-0x80001FE7, BootRamdiskDxe refuses it and no payload volume appears), so they do not boot a04br3. Every image is
# checked first (scripts/check-a04br3-bootimg.py). With UART= this script reads the port itself; stop other readers
# or use UART_LOG= with the file of a running collector. Each round writes result.txt and stage.txt (last stage reached from the
# UART log: sec, dxe, firmware-timeout (includes a DarwinBoot failure), exit-boot-services, bsd_init; first panic; LK reason). Exit status: 0 when every round returned to fastboot by itself, 2 when a round
# did not leave fastboot, 3 when it did not come back within wait_seconds. Logs: out/a04br3/unattended/<time>/round-N/.
set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
IMG=${1:?boot image}
LIMIT=${2:-1200}
MODE=${MODE:-flash}
ROUNDS=${ROUNDS:-1}
listed() { fastboot devices 2>/dev/null | awk '{print $1}' | grep -qx "$SERIAL"; }
DEVICE_WAIT=${DEVICE_WAIT:-900}
echo "waiting for a fastboot device (up to ${DEVICE_WAIT}s)"
until fastboot devices 2>/dev/null | grep -q .; do
  if (( SECONDS >= DEVICE_WAIT )); then echo "no fastboot device within ${DEVICE_WAIT}s" >&2; exit 4; fi
  sleep 2
done
# A wedged USB device can hang fastboot forever; the timeout keeps the cycle reporting
# (observed D-state fastboot during the PMGR relaunch round).
FB() { if command -v timeout >/dev/null; then timeout ${FB_TIMEOUT:-300} fastboot $"@"; else fastboot $"@"; fi; }
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
    FB -s "$SERIAL" flash userdata "$USERDATA" > "$RUN/fastboot-userdata.log" 2>&1 || { echo "userdata flash failed" | tee "$RUN/result.txt"; exit 1; }
  fi
  CAPTURE=
  if [ -n "${UART:-}" ]; then
    stty -F "$UART" 921600 cs8 -cstopb -parenb raw -echo
    cat "$UART" > "$RUN/uart.log" & CAPTURE=$!
  elif [ -n "${UART_LOG:-}" ]; then
    START=$(stat -c %s "$UART_LOG")
  fi
  python3 "$ROOT/scripts/check-a04br3-bootimg.py" "$IMG" > "$RUN/check.log" 2>&1 \
    || { cat "$RUN/check.log"; echo "boot image check failed" | tee "$RUN/result.txt"; exit 1; }
  if [ "$MODE" = auto ]; then
    if FB -s "$SERIAL" boot "$IMG" > "$RUN/fastboot.log" 2>&1; then
      echo "mode: boot" >> "$RUN/fastboot.log"
    else
      echo "fastboot boot refused; writing the boot partition" >> "$RUN/fastboot.log"
      { FB -s "$SERIAL" flash boot "$IMG" && FB -s "$SERIAL" reboot; } >> "$RUN/fastboot.log" 2>&1 \
        || { echo "fastboot flash failed" | tee "$RUN/result.txt"; exit 1; }
      echo "mode: flash" >> "$RUN/fastboot.log"
    fi
  else
  case "$MODE" in
    flash) FB -s "$SERIAL" flash boot "$IMG" > "$RUN/fastboot.log" 2>&1 && FB -s "$SERIAL" reboot >> "$RUN/fastboot.log" 2>&1 ;;
    boot)  FB -s "$SERIAL" boot "$IMG" > "$RUN/fastboot.log" 2>&1 ;;
    *) echo "MODE must be auto, boot or flash"; exit 1 ;;
  esac || { echo "fastboot $MODE failed" | tee "$RUN/result.txt"; exit 1; }
  fi
  # The tablet must leave fastboot within 60 s, otherwise the round did not start.
  left=0
  for _ in $(seq 1 60); do if ! listed; then left=1; break; fi; sleep 1; done
  finish() {
    [ -n "$CAPTURE" ] && kill "$CAPTURE" 2>/dev/null
    if [ -n "${UART_LOG:-}" ]; then tail -c +$((START + 1)) "$UART_LOG" > "$RUN/uart.log"; fi
    classify
  }
  # Where the round ended, from the UART log: the last stage reached, the first panic and the LK reason (stage.txt).
  classify() {
    local log=$RUN/uart.log stage=none
    [ -s "$log" ] || { echo "stage: no UART output" > "$RUN/stage.txt"; return; }
    grep -aq 'A04BR3 SEC: test cycle' "$log" && stage=sec
    grep -aq 'A04BR3TestCycleDxe: fastboot flag' "$log" && stage=dxe
    grep -aq 'no ExitBootServices within' "$log" && stage=firmware-timeout
    grep -aq 'A04BR3TestCycleDxe: ExitBootServices' "$log" && stage=exit-boot-services
    grep -aq 'a04br3-autoreset: fastboot flag' "$log" && stage=bsd_init
    {
      echo "stage: $stage"
      grep -am1 -E 'panic\(|Panicked|PANIC|Kernel panic' "$log" | sed 's/^/first panic: /'
      grep -a -i 'boot_reason\|reboot to boot loader' "$log" | tail -3 | sed 's/^/lk: /'
    } > "$RUN/stage.txt"
  }
  if [ "$left" = 0 ]; then finish; echo "round $round: still in fastboot after $MODE" | tee "$RUN/result.txt"; exit 2; fi
  echo "round $round: running, waiting up to ${LIMIT}s for the return to fastboot"
  end=$((SECONDS + LIMIT))
  until listed; do
    if (( SECONDS > end )); then finish; echo "round $round: no return to fastboot within ${LIMIT}s" | tee "$RUN/result.txt"; exit 3; fi
    sleep 2
  done
  finish
  echo "round $round: returned to fastboot by itself after $((SECONDS - end + LIMIT))s; $(head -1 "$RUN/stage.txt")" | tee "$RUN/result.txt"
done
echo "logs: $RUNS"
