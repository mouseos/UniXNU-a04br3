# A04BR3 charger watchdog owner — review candidate

This is a default-off board service, not a passive/read-only probe or a charging fix.
No runtime trial has qualified it. Chandra approved the revised source and host tests; target SDK and runtime qualification remain pending. The linked code has not yet had a target-SDK kernel build. Chandra source/side-effect review passed; target SDK and runtime qualification are still required before
including `a04br3-charger-owner=1` in any test image. No default boot argument enables it.
The board archive links the code; without that exact argument its init returns before
allocation, mapping, GPIO access, or I2C access.

## Evidence and limits

TI BQ2416xx SLUSAO0H Rev H, sections 8.3.8–8.3.9, 8.5.1 and 8.6:
https://www.ti.com/lit/ds/symlink/bq24161.pdf (this family datasheet includes BQ24160).

* An I2C read/write starts the 30 s watchdog. A TMR_RST write services it; ordinary
  reads are not a substitute. Expiry can reload DEFAULT settings, including regulation
  voltage and safety-timer state. It does not by itself establish that charging stopped.
* Reading register00 consumes historical FAULT if the fault no longer exists.
* RESET reads as one; that read value does not prove a reset was performed.
* The family ID does not uniquely distinguish BQ24160 from BQ24160A. The latter lacks
  this watchdog. This implementation targets the board's declared BQ24160 only; ID
  family validation does not independently prove the physical variant.

The stock board DTS (`a04br3.dts`, I2C2) and controller driver identify:

| Resource | Contract |
| --- | --- |
| Charger | I2C2 at 0x1100b000, 7-bit address 0x6b |
| Fuel gauge | Same bus, address 0x0b; not accessed by this service |
| SDA/SCL | GPIO60/61 ALT1 |
| Charger enable | GPIO9 output low |
| USB/AC detect | GPIO34/35 input low = detected |
| Touch | Separate I2C0 0x11009000, 0x48, GPIO58/59 |
| I2C2 gate | TOP group1 status offset0x24 bit16, clear = enabled |
| I2C2 mux | INFRACFG offset0x80 bit3; exclusive I2C2 selection -> ahb_infra_d2 |

Sources used as hardware/protocol evidence are the original
`CT3_GPL_LGPL_Src/kernel-4.4/arch/arm64/boot/dts/mediatek/{a04br3.dts,mt8167.dtsi}`,
`drivers/i2c/busses/i2c-mt65xx.c`, `drivers/clk/mediatek/{clk-mt8167.c,clk-gate.c}`,
`drivers/misc/mediatek/power/{bq24160.c,mt8167/charging_hw_bq24160.c}`, and
`drivers/power/mediatek/sts_battery_common.c`. The board wires are also represented
in the committed uefi-a04br3 `Resources/DTBs/a04br3.dts`. The host tests execute the
new shared policy/PIO implementation; they do not establish electrical correctness.
The GPL driver accepts any nonnegative i2c_transfer result; that completion bug is
not reused. Our combined read requires the controller completion and exactly one
received FIFO byte, with explicit NACK/busy/timeout failures.

The observed LK dump was `30 00 8c a4 45 51 00 38`: IN charging, CE/HZ clear,
IN limit 1.5 A, charge setting 1.3 A, regulation 4.32 V. These are settings, not
measured current. The USB limit field being 100 mA does not establish a limit on
IN charging. Both known dumps are after LK initialization, not during XNU.
The observed fastboot charging recovery and later low-voltage refusal do not
identify watchdog expiry, cable capacity, thermal limitation, or load as the cause.

## Implemented ownership and side effects

`A04BR3Charger.cpp` starts one kernel thread from the board hook after AutoReset.
Mapping/lock/thread failure causes no charger I2C transaction. Stage 1 records inherited
GPIO/clock/pinmux/controller state. The shared BoardCore then initializes exclusively
owned I2C2 resources: TOP gate bit16, INFRA mux bit3, GPIO60/61 ALT1/input-enable/
bias-disable, open-drain PIO, divider2 and computed timing <=100 kHz. GPIO EN9
and USB/AC detection remain observed, not driven. No shared clock/PLL/rail, APDMA,
touch I2C0, PMIC, or controller reset is modified. A busy controller is refused.
The source-derived AHB parent selectors1/2/4/12 cover all non-null entries; MAINPLL
rate is decoded from its live PCW/postdivider with GPL fractional14 semantics.
Unknown/off/unstable parents are reported distinctly, never assigned a guessed rate.

One lock covers the entire snapshot and watchdog write on I2C2. Touch uses I2C0;
its mailbox/button locks are not an I2C2 arbiter. A future fuel-gauge client must
share this owner/lock rather than using an independent raw bus implementation.
This service never accesses PWRAP. Current AutoReset RTC, GPU VCORE reads, and touch
VGP1 setup use WACS2 without a common transaction lock; periodic PWRAP access must
not be added here. This avoids both new PMIC ownership and rail-policy changes.

The already running owner thread captures register00 first, then01–07. The first
snapshot, valid mask, timestamp, and initial FAULT remain in memory; subsequent
observed fault codes are accumulated. Failed/partial transfers remain unknown,
not zero-valued successful registers. Even an unsuccessful first read may have
started watchdog responsibility; logs explicitly retain that possibility.

Only a matching inherited LK policy passes the whitelist. Comparison ignores the
read-only RESET bit and dynamic detection status, but requires the reviewed charge,
regulation, input-selection and safety/temperature configuration. Fault, mismatch,
no valid charging source, NACK, FIFO mismatch, timeout, or owner deadline miss stops
the owner. It never installs 1.3 A/4.32 V, clears CE/HZ, changes input/current limits,
disables safety timers, forces source selection, or pretends the battery is full.
The whitelist now also requires register06 VINDPM_IN/USB low6 bits zero and register01 OTG_LOCK bit3 zero, as in the observed LK dump. Register06 high status bits are not configuration.
The sole allowed charger data write is register00 TMR_RST with unchanged SUPPLY_SEL.
The register-pointer write phase of a read is not a configuration data write.

Sampling/pet cadence is 10 s, bounded by a 20 s owner-gap check; each transaction
has both a 2000-poll and 10 ms bound, and each complete snapshot has a 1 s bound.
A stopped owner never automatically retries or kicks a stale snapshot. Stopping
is not restoration: hardware DEFAULT fallback may follow within 30 s and alter
settings. A late/hung kernel has that same hardware consequence. That behavior,
including safety-timer consequences, requires review; it is not represented as a
safe or side-effect-free exit from a diagnostic session.

## Firmware ownership and unverified handoff timing

The canonical `uefi-a04br3/Platforms/Benesse/a04br3Pkg` adds
`A04BR3_CHARGER_OWNER=1`; default0 excludes the DXE module from both components
and FV/APRIORI. The module is immediately after TimerDxe, before other board
consumers. EBS and periodic timer registration must succeed before initialization
or BQ access. The first sample is immediate; subsequent samples/pets are10 s apart.
Each callback executes at TPL_NOTIFY to serialize MMIO with EBS. EBS sets exited
and disarms the owner, without charger access, allocation, timer API, or final pet. EBS does finalize the boot-local inline handoff record in the loader-owned final ADT copy.
Later callbacks cannot access it. The log reports last-kick and EBS timestamps.
The monotonic full-width up-counter prerequisite follows the existing recovery module;
TOPRGU recovery watchdog and charger watchdog remain distinct owners/hardware.

The policy/PIO, board-init and handoff headers are byte-identical in the two board
repositories. Device `package/A04BR3ChargerCore.h`, `A04BR3ChargerBoardCore.h`, and `A04BR3ChargerHandoff.h` is the review source;
UEFI `Include` holds versioned copies, not an unrecorded sibling include.
Run the firmware `Tests/sync_charger_core.py --device-root /absolute/device/root`
for verification; only explicit `--write` synchronizes device -> firmware. Review
both repos together and record their commit pins before enabling. No generic
upstream source or default firmware/kernel arguments are changed.

Runtime continuity is NOT yet proven: LK -> early DXE may already exceed30 s, and
EBS -> kernel thread must fit the remaining interval (at most30 s after last pet,
nominally >=20 s at EBS with punctual callbacks). Firmware and kernel logs must
measure both gaps. Kernel first00 is first for that owner, not pre-firmware FAULT
history: firmware may already have consumed a latched fault. Preserve both owners'
first samples and valid masks. A faulted/stopped owner is never silently resumed.
If DEFAULT is already present, either owner refuses policy mismatch, not repairs it.
This implementation does not claim a kernel-only pet fixes power continuity.
Chandra approved paired source/host behavior; runtime enable still requires the matched SDK-built kernel and fixed-source FD;
GPU-only kernel work takes precedence. Main owns all runtime image/flash operations.

For comparison, first retain baseline existing LK/boot logs with the service disabled.
The enabled owner records GPIO/controller inheritance before its first charger read;
then compare its first raw capture and subsequent samples: source
STAT, FAULT, CE/HZ, voltage/current settings and battery telemetry at matched stages.
Do not call these a simultaneous nonintrusive comparison: read-clear/WDT effects
change the observation. Battery voltage requires a separately reviewed fuel-gauge
transaction with PEC validation; no battery voltage/current is fabricated here.

## Checks

Run `python3 scripts/tests/test_a04br3_charger.py` directly. It compiles the actual
policy/PIO header with ASan/UBSan and exercises first FAULT, partial snapshots,
policy mismatch/defaults, faults, deadlines, single allowed write, FIFO mismatch,
NACK, busy bus and timeout. It also syntax-checks the actual board C++ for AArch64
with host declaration substitutes. This is not an SDK kernel build or device test.

Firmware host checks (from the UEFI repository):

```
python3 Platforms/Benesse/a04br3Pkg/Tests/sync_charger_core.py --device-root /absolute/UniXNU-a04br3
python3 Platforms/Benesse/a04br3Pkg/Tests/test_charger.py
```

The latter compiles the actual DXE .cpp and shared headers with mocked boot
services/MMIO under ASan/UBSan. It checks event failure before any access,
initialization masks/timing, first FAULT preservation, monotonic deadline stop,
and zero MMIO after EBS. A host mock proves logic, not electrical/controller
operation. A candidate FD build is separate from Chandra review and runtime proof.

## P2 revision: validated handoff, transport and shared mux

A fresh kernel Owner can no longer erase a firmware fault/stop. Kernel enable
requires an exact128-byte sealed `/chosen/a04br3-charger-owner` record from this
boot. The ADT builder reserves zeros (not a valid state); the opt-in firmware
attaches to the FINAL loader-owned ADT copy and finalizes it at EBS. It records
stopped/active state, first/latest raw samples and valid masks, observed FAULT
history, last successful pet CNTPCT, EBS CNTPCT and CNTFRQ. A stopped record,
missing/zero/bad version or seal, nonzero historical FAULT, frequency mismatch,
backward/future counter or >=20 s pet age rejects before kernel register mapping.
An accepted record preserves remaining deadline until first successful kernel
pet; resource initialization and scheduler delay cannot restart a fresh20 s gap.
The ns origin is translated using the shared physical counter, not by equating
EFI and mach_absolute_time origins. The seal detects corruption, not authenticity.
Only the trusted source loader/firmware owns this boot-local record; no fixed
scratch PA, NVRAM write, invented hardware property, or persistent stale marker.

The generic DarwinBoot change is a board-neutral `DARWINBOOT_BOARD_HANDOFF=0`
interface, invoked only on opt-in before GetMemoryMap/EBS. The board callback
cannot resize/allocate the final tree. On loader failure it detaches BEFORE the
window is freed. EBS record writes lie inside the kernel window that existing
Entry.S cleans before entry. Its source commit must be pinned along with device
and UEFI after review; this is a third repository change, not a local build patch.
Board protocol header copies must match the loader interface byte-for-byte.
A repeated EBS attempt does not clear the stop or publish a fresh last-pet time.

I2C0 touch and I2C2 charger now use the same actual `a04br3_infra_update` function
and IOLock for INFRACFG+0x80 RMW. The lock is initialized in early board_init;
touch starts in the later IOKitBSDInit late-display hook. Missing lock refuses,
never falls back to raw RMW. Only masks bit1/bit3 are admitted; no speculative
SET/CLR aliases. Firmware init holds TPL_NOTIFY; current board firmware has no
other +0x80 writer (DisplayInit uses +0x220/+0x228). This contract must be revisited
if another clock client is added. Host tests execute actual initialize() racing
the real shared mux function, with both touch and charger call sites checked.

Standard-mode EXT_CONF offset0x28 is now programmed/read back as0x8001, matching
GPL I2C_ST_START_CON at <=100 kHz. Readback mismatch rejects before BQ access.
Run `python3 scripts/tests/test_a04br3_register_lock.py` for shared-writer tests.
Firmware test accepts `--device-root /absolute/UniXNU-a04br3` to exercise the actual
ADT generator rather than just its minimal standalone fixture. All runtime and
Chandra approval limitations above still apply.
