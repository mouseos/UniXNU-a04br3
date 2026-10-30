#!/bin/bash
# Build only on the fixed UniXNU tree and its recorded NativeMsdc gitlink.
set -euo pipefail
: "${DEVICE_DIR:?Set DEVICE_DIR to a clean UniXNU-a04br3 checkout}"
: "${A04BR3_DEVICE_COMMIT:?Set A04BR3_DEVICE_COMMIT to its full commit ID}"
: "${XNU_OWNER_DATA_VOLUME_UUID:?Prepare the owner Data seed using the UniXNU workflow}"
test "$(git rev-parse HEAD)" = 22e49b19f9651e8b0e81c3fb3764d8d3a72c7ff4
test "$(git -C "$DEVICE_DIR" rev-parse HEAD)" = "$A04BR3_DEVICE_COMMIT"
test -z "$(git -C "$DEVICE_DIR" status --porcelain --untracked-files=no --ignore-submodules=all)"
test "$(git ls-tree HEAD drivers/NativeMsdc | awk '{print $3}')" = 66d40654ae43ef27090a6d6e1f552be6ea10d24b
test "$(git -C drivers/NativeMsdc rev-parse HEAD)" = 66d40654ae43ef27090a6d6e1f552be6ea10d24b
export XNU_IRQ_PC_STATS=1 XNU_MSDC_IRQ_WAIT=1 XNU_MSDC_IRQ_WORKER=1 XNU_MSDC_GIC_NS=1
export XNU_PROCESS_EXIT_DIAG=1 XNU_ROOT_TIMING=1 XNU_IO_OVERLAP_TRACE=1
./unixnu defconfig a04br3_rtc_ref_defconfig -D "$DEVICE_DIR"
./unixnu build kernel -D "$DEVICE_DIR"
tree=out/a04br3/tree
kernel=out/a04br3/kernel/kernel.macho
msdc="$tree/build/native-msdc/libNativeMsdc.a"
grep -q -- '-DNATIVE_MSDC_IRQ_WAIT=1' "$tree/build/native-msdc/compile-command.json"
grep -q -- '-DA04BR3_RTC_REF=1' "$tree/build/a04br3-board/A04BR3AutoReset.command.json"
symbols=$(mktemp -d)
trap 'rm -rf "$symbols"' EXIT
nm -g "$msdc" > "$symbols/msdc"
nm -g "$kernel" > "$symbols/kernel"
for sym in native_msdc_irq_mask_failures native_msdc_irq_stats native_msdc_worker_stats; do
  grep -q " _$sym$" "$symbols/msdc"
done
for sym in native_msdc_irq_mask_failures native_msdc_irq_stats native_msdc_worker_stats native_cs_trace_report; do
  grep -q " _$sym$" "$symbols/kernel"
done
