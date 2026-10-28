# UniXNU-a04br3

UniXNU device support for the Benesse TAB-A04-BR3 (a04br3), a tablet with the MediaTek MT8167 SoC
(four Cortex-A35 cores, GIC-400, 2 GiB DRAM, eMMC). Work in progress: board file, kernel configuration,
Apple device tree generator and GPT layout. The hardware reaches the iPadOS home screen with four CPUs,
eMMC DMA and the CPU framebuffer renderer. PowerVR Vulkan transfer/readback works; Metal UI acceleration
and interactive performance remain under development.

| Repository | Contents |
| --- | --- |
| [UniXNU-a04br3](https://github.com/mouseos/UniXNU-a04br3) | this device repository |
| [uefi-a04br3](https://github.com/mouseos/uefi-a04br3) | Mu-Silicium UEFI port (MT8167Pkg, Benesse/a04br3Pkg) |

## Public WIP source pairing

The default branches are not yet a qualified pair. At this checkpoint the device `main`
(`b25fe48a282d9e94bba13e98bae38f4bcfbbab73`) still validates a ramdisk address of `0x80000000`,
while UEFI `main` (`8dbdd50713b8fbb79a5f5837a032b46a4a939723`) uses `0x60000000`.
For new builds and unattended runs, use device `wip/display` commit
`dde2dab72621c02696857abc12b29977cdc447a2` or a reviewed descendant, with the UEFI pin above.
That device commit includes the fastboot argv fix; `ab37700` does not. The fixed device
commit is published on `wip/display`; the RAM trial remains unqualified on hardware.
Packet11's payload was actually generated with `ab37700bdd60b56dc855293f28184124da3a8c60`;
retain that SHA as artifact history, not as the unattended-runner recommendation.
Use the device boot-image validator from the same pinned device checkout. These pins identify
source inputs, not a completed hardware qualification. The device WIP is to be merged normally
into `main` only after the RAM trial passes; no default-branch switch or force push is needed.
The historical image pins below describe an earlier checkpoint, not today's default-branch pair.

The updated `a04br3-ssh.yml` workflow pins `device_ref` to the RAM600-compatible device
commit `dde2dab72621c02696857abc12b29977cdc447a2`, including the FB fix, instead of the incompatible old `main`.
SSH rebuilds without an explicit ref
retain the initially checked-out commit. This is a source pairing safeguard; RAM hardware
qualification is still pending. PowerVR inputs are opt-in:
`powervr_probe=true`, `powervr_metal=true`, `powervr_metal_mode=publish` selects GPU publication;
`powervr_metal_roa_color=true` additionally forwards `A04BR3_POWERVR_METAL_ROA_COLOR=1` to
prebuild for framebuffer-fetch pipelines. ROA defaults off and requires both probe and Metal.
The workflow retains these settings for its initial build and SSH incremental rebuilds.
The generic `macos.yml` uses its existing `pre_build` commands instead; export the same prebuild
environment variables explicitly there. Neither mode is a claim that Metal UI or larger RAM
is qualified. Initial Data/trust inputs are generated from the pinned source/IPSW build;
users do not need device backup files. GPU-only updates that retain persistent Data use the
prepared-Data helper documented below, rather than regenerating the seed on every update.

### Explicit build profiles

All GPU options remain off by default. The SSH workflow profiles below are combinations
of explicit inputs, not additional implicit defaults:

| Profile | powervr_probe | powervr_metal | powervr_metal_mode | powervr_metal_roa_color |
| --- | --- | --- | --- | --- |
| CPU framebuffer | false | false | selftest | false |
| Vulkan probe | true | false | selftest | false |
| Metal self-test | true | true | selftest | false |
| Experimental Metal publication with COLOR ROA | true | true | publish | true |

For the experimental publication profile, run the updated workflow from its reviewed
base revision and explicitly provide the fixed device input:

```sh
gh workflow run a04br3-ssh.yml --repo mouseos/UniXNU --ref 42d15c7e9f5891057fbdc2f4908c5347512d2492 \
  -f device_ref=dde2dab72621c02696857abc12b29977cdc447a2 \
  -f powervr_probe=true -f powervr_metal=true \
  -f powervr_metal_mode=publish -f powervr_metal_roa_color=true
```

The base ref above contains the updated workflow; record the run's actual source SHA.
Do not substitute an older workflow that lacks the ROA input. Generate firmware with
UEFI pinned to `8dbdd50713b8fbb79a5f5837a032b46a4a939723` and validate the resulting boot
image using the pinned device's `scripts/check-a04br3-bootimg.py` before deployment.
This profile does not enable require-fetch, async creation, or pipeline diagnostics.

Host regression checks are direct script invocations from their respective checkouts:

```sh
# UniXNU checkout
python3 scripts/tests/test_a04br3_ssh_profile.py
# UniXNU-a04br3 checkout
bash -n scripts/run-a04br3-unattended.sh
python3 scripts/tests/test_a04br3_unattended.py
python3 scripts/tests/test_a04br3_uart.py
```

`test_a04br3_unattended.py` executes its assertions at module scope. Run it directly;
`unittest discover` reporting zero test cases is not a validation result for this script.
For unattended runs, set `RUNS_ROOT` to an absolute host directory outside the source checkout
(for this bring-up, `port/gsi/logs/ios-feasibility/unattended` in the parent workspace).
Keep `MODE=flash`: the default deliberately does not retry the known-broken `fastboot boot` path.

## Build

The board needs changes to the UniXNU base that are not upstream yet (all behind default-off flags, prepared as pull
requests): the 16550 console (XNU_NS16550_UART), the board hook (XNU_BOARD_INIT), the MSDC personality
(XNU_NATIVE_MSDC), MediaTek image headers in bootimg (mtk_header) and an authenticated DriverKit header listing. Use the
fork [mouseos/UniXNU](https://github.com/mouseos/UniXNU) until they are merged, and the ArmCpu fork with the GICv2 path
(submodule drivers/ArmCpu of this repository).

    git clone --recurse-submodules https://github.com/mouseos/UniXNU
    git clone --recurse-submodules https://github.com/mouseos/UniXNU-a04br3
    git clone --recurse-submodules https://github.com/mouseos/uefi-a04br3
    git -C UniXNU-a04br3 checkout --detach dde2dab72621c02696857abc12b29977cdc447a2
    git -C UniXNU-a04br3 submodule update --init --recursive
    git -C uefi-a04br3 checkout --detach 8dbdd50713b8fbb79a5f5837a032b46a4a939723
    git -C uefi-a04br3 submodule update --init --recursive
    git -C UniXNU checkout --detach b9017ce1cdf750910a23cc190381f7663f80d37c
    git -C UniXNU submodule update --init --recursive
    cd UniXNU
    ./unixnu defconfig a04br3_defconfig -D ../UniXNU-a04br3
    ./unixnu fetch
    ./unixnu build package
    ./unixnu build firmware --uefi ../uefi-a04br3

The earlier image checkpoint was built from these revisions (Actions run 39850069847 of mouseos/UniXNU, firmware built locally
with the same command); check them out to reproduce it. The fork main is upstream UniXNU with the pull request branches
`pr/*` of mouseos/UniXNU merged; the first image (tag `a04br3-image-1`) predates that rebase.

| Repository | Commit |
| --- | --- |
| mouseos/UniXNU | fe43c401ef5e0d29f2d8b88945017e4b1a65421b (main) |
| mouseos/UniXNU-a04br3 | 2eef74107d26890d22da00a7e14df22ab897c848 |
| mouseos/uefi-a04br3 | 45a04ea93e99e587c5c01f93e2b6b14e443679b3 |

The kernel, userdata and payload steps need macOS. They run in the macOS build workflow of the fork with
device_repository = mouseos/UniXNU-a04br3, defconfig = a04br3_defconfig, steps = kernel userdata payload and
pre_build = bash "$DEVICE_DIR/scripts/a04br3-pre-build.sh" (trust cache bundle and Data seed from the IPSW). The
repository secret UNIXNU_UNIQUE_CHIP_ID holds the platform seed. Then, with the downloaded payload:

    ./unixnu bootimg --uefi ../uefi-a04br3 --dtb ../uefi-a04br3/Resources/DTBs/a04br3.dtb

## Partition layout

`scripts/make-a04br3-gpt.py` writes both GPT copies from `scripts/a04br3-gpt.json`. Compared with the stock
layout, the Android partitions `factory`, `metadata`, `system` and `cache` are removed, `boot` grows to
128 MiB and `userdata` runs from LBA 557056 to the start of `flashinfo`. The bootloader partitions,
`logo` (the LK splash) and the calibration partitions keep their stock positions.

## eMMC

[NativeMsdc](https://github.com/mouseos/NativeMsdc) (`drivers/NativeMsdc` of the UniXNU base, `CONFIG_XNU_NATIVE_MSDC=y`) drives the eMMC on
MSDC0 (`/arm-io/mmc0`). It does nothing unless `native-msdc-root` or `native-msdc-persist` is set. The userdata windows
of the root image (`native-msdc-root`, `CONFIG_XNU_NATIVE_MSDC_BLOCK=y`) and NativeRamBlock (the Data RAM disk) subclass
the original `IOBlockStorageDevice`. Without the full storage audit, `build-native-virtio-bindings.py` binds them with
`scripts/data/native-storage-interface-audit.json` of UniXNU, which `scripts/record-storage-interface.py` derives from the
kernelcache pinned in `board.toml` and the public IOStorageFamily source.

## Flashing

## Root authentication

iPadOS mounts its root only when the volume is authenticated. a04br3 uses the owner root chain of UniXNU-sheng and
UniXNU-tb128fu (`CONFIG_XNU_OWNER_ROOT_AUDIT`, `CONFIG_XNU_OWNER_ROOT_TRUST`): NativeMsdc (built `--verified-root`)
checks every 4096-byte block of the OS and SystemOS windows against an envelope signed with the owner key and answers
the root authorization query of the verified NativeVirtioBlock for windows whose signed volume UUID matches.

- `package/root-key/owner-public.pem` is the owner anchor; `scripts/build-a04br3-root-key.py` puts it into the kernel
  (`CONFIG_XNU_VERIFIED_ROOT_ARCHIVE`) with VerifiedRoot and a self-test on `package/root-key/selftest.json`.
- `scripts/make-a04br3-root-policy.py --userdata userdata-root.img --private-key owner.pem --out DIR` signs the
  envelopes (about 73 MiB) from the windows of the userdata image; `make-a04br3-flash.py --root-policy DIR` writes them
  into userdata right after the image. They change only with the IPSW or the key, so fastboot rounds never rewrite them.
- The private key stays with its owner. To build with another key, generate an RSA-3072 key pair (exponent 65537),
  replace `package/root-key/owner-public.pem` and run `make-a04br3-root-policy.py --selftest` once.

The tablet must be bootloader-unlocked (LK shows "Orange State"); a locked LK refuses the unsigned boot image and
fastboot writes.

`scripts/make-a04br3-flash.py --boot <out>/a04br3/boot/boot.img --userdata <userdata-root.img> --out DIR` checks the
boot image (`scripts/check-a04br3-bootimg.py`) and writes `DIR/write.mtk`: boot, userdata, both GPT copies, then a
reset. Run it once with mtkclient (`mtk script DIR/write.mtk`, tablet powered off and connected in BROM/preloader
mode); paths must not contain spaces. `<out>` is the `--out` of `unixnu bootimg`, the userdata image is the
`unixnu-userdata-a04br3_defconfig` artifact of the macOS workflow. Later images go through fastboot (below).

## Unattended test cycle

After the first installation with mtkclient, every later image goes through the stock LK fastboot mode without
mtkclient and without touching the tablet:

- The stock LK enters fastboot when bit 13 of the PMIC RTC register RTC_PDN1 (0x802C) is set and clears it (REA of the
  stock LK).
- Firmware (uefi-a04br3, `A04BR3_TEST_CYCLE = "1"` in `[firmware.defines]`): the SEC hook A04BR3PlatformSecBoardLib
  arms the TOPRGU watchdog for 31 s and sets that bit; A04BR3TestCycleDxe kicks the watchdog until ExitBootServices
  (at most 120 s) and arms it again there. A hang in the firmware or a missing payload resets into fastboot.
- Kernel (boot argument `a04br3-autoreset=<seconds>`, `package/A04BR3AutoReset.cpp`, called from bsd_init through the
  board hook `unixnu_board_init`): sets the bit again, keeps kicking the watchdog and resets after the given time. A
  panic or a hang before or after bsd_init therefore also returns to fastboot.
- Host: `scripts/run-a04br3-unattended.sh BOOT_IMAGE` writes the image with fastboot, reboots, waits for the return to
  fastboot and keeps the UART log of each round (UART0, 921600 8N1; `UART=/dev/ttyUSB0`, or `UART_LOG=` of a running
  collector). `ROUNDS=` and `NEXT_IMAGE_CMD=` chain rounds. Each round writes `result.txt` and `stage.txt` (last stage
  reached: sec, dxe, firmware-timeout, exit-boot-services, bsd_init; first panic; LK reason) under
  `out/a04br3/unattended/`.

Both parts are on in this board file for bring-up. For an image that boots normally, set `A04BR3_TEST_CYCLE = "0"`
and remove `a04br3-autoreset` from `kernel_args`; nothing else changes.

### Reset on request

With `a04br3-autoreset` the kernel also polls UART0 receive and resets into LK fastboot when the host sends the line
`A04BR3!RST`: `scripts/a04br3-reset.sh` (`UART=/dev/ttyUSB0`). A round can end as soon as its evidence is on the log
instead of waiting for the timer.

### Screenshot over UART

`scripts/a04br3-screenshot.py --log <UART log> out.png` sends `A04BR3!SHOT`; the kernel (same thread as the reset request)
sends the frame buffer as CRC-checked, run-length coded rows on UART0 and the script writes a PNG, asking again for rows a
kernel log line broke.

### Home and input diagnostics

With the same UART service enabled, `scripts/a04br3-home.sh` sends `A04BR3!HOME` to the
NativeHidInput Home mailbox. It wakes or leaves an app through a normal key press/release;
it does not reset the tablet. The collector keeps running. Older kernels without this
command require the physical Home mapping.

`a04br3-proclog=60` gives a compact process/display heartbeat. Add `a04br3-stacklog=1`
only when full UI thread stacks are needed; they are disabled during ordinary use.
`a04br3-input-selftest=<seconds>` enables one synthetic swipe for unattended input
tests. It is disabled in the board defaults so it does not interfere with real fingers.
The first 32 contacts log their HID report count and return code as well as coordinates.

### Fast rebuilds over SSH

A full Actions build takes about 25 minutes. The base workflow `.github/workflows/a04br3-ssh.yml` of UniXNU builds once,
then keeps the runner (`keep_minutes`, at most 330) with an upterm SSH session whose command is posted as the commit
status `a04br3/ssh` (only the owner's key in `authorized_keys`). In the session, `/tmp/a04br3-rebuild.sh <device ref>`
rebuilds the kernel and payload of that ref incrementally (about 4 minutes) and publishes `payload-<short sha>.img` on the
prerelease `a04br3-ssh-<run id>`. On the host, `scripts/a04br3-ssh-round.sh RUN_ID TAG` (with `UNIXNU=` and `UEFI=`
checkouts) downloads that payload, builds the boot image with the official `unixnu bootimg` and runs one unattended round.
Every fix is committed and pushed first, so each payload comes from a pushed ref.


## Display

`CONFIG_XNU_NATIVE_LEGACY_DISPLAY=y` and `scripts/build-a04br3-legacy-display.py` (from UniXNU-tb128fu
`build-native-legacy-display.py`) put the iPadOS display on the firmware frame buffer: UniXNU `NativeLegacyDisplay`, a
subclass of the original `IOMobileFramebufferLegacy`, on uefi-a04br3's GOP (800x1280, 32 bpp), QuartzCore's CPU renderer
copying each frame, and `NativeHidInput` with a Home button. The probes are `package/LegacyDisplayProbe.cpp`,
`package/HidInputProbe.cpp` and `package/TouchState.cpp` (from UniXNU-tb128fu, without its touch, GPU and RPM parts); the
kernel arguments are the `offline-legacy-*`, `offline-mobile-graphics-load` and `offline-hid-*` entries of `board.toml`.
The original classes are checked against this board's previous kernel (`out/a04br3/kernel/kernel.macho`), so a new tree
needs two kernel builds: `scripts/a04br3-pre-build.sh` runs the first, which links no-op display probes.

## launchd without a display

The launchd-side parts of UniXNU-tb128fu and UniXNU-sheng that do not need a boot-progress page:

- AppleKeyStore stand-in (`offline-key-events=1`, `sheng-aks=1`): `package/KeyStoreProbe.cpp` (from UniXNU-tb128fu) and
  UniXNU `drivers/NativeKeyStoreEvents`, linked in `libA04BR3Board.a` and started from `unixnu_board_init`
  (`package/A04BR3Board.cpp`). The other boards start it from their display probe; the a04br3 copy of that probe does not.
- launchd disabled jobs (`sheng-disable-watchdogd=1`, `sheng-disable-jobs=...`): `scripts/a04br3-seed-launch-policy.py`
  writes `/private/var/db/com.apple.xpc.launchd/disabled.plist` into the Data seed at build time with the bytes of
  `package/BootWatchdogPolicy.c` (from UniXNU-tb128fu). The kernel form, `unixnu_board_launch` with
  `CONFIG_XNU_BOARD_LAUNCH=y`, needs the UniXNU change in `upstream/UniXNU/0001-native-board-launch-hook.patch`; UniXNU
  calls the policy today only from its boot-progress page.
- `-no_corpses` and `ml-timeout-lock-panic-scale=40`: stock XNU boot arguments.

## PowerVR user-space probe

`powervrprobed` (`package/powervr/user`) tests the NativePowerVR user client from user space with IOKit only: the ABI
handshake, every device query, buffer objects mapped into the process, a VM context with a GPU mapping, a transfer and a
compute context, and the submission and fence refusals that need no GPU work. Built with `--vulkan`, it also loads Mesa's
PowerVR driver (`scripts/build-pvr-darwin.py`), clears an image on the GPU and reads it back. Every step is one UART line
`powervrprobed: PASS|FAIL ...`, written to `/dev/console`, and a summary line `powervrprobed: done: N passed, M failed`.

- Start: the kernel spawns `/private/var/ngpu/bin/powervrprobed` once before launchd (`package/A04BR3ProbeLaunch.c`,
  boot argument `a04br3-powervrprobed=1`). This needs the board launch hook (`CONFIG_XNU_BOARD_LAUNCH`, the
  `native-board-launch-hook` proposal for UniXNU). launchd on iPadOS 17 does not run new owner jobs.
- Build: in the macOS workflow, set `pre_build = A04BR3_POWERVR_PROBE=1 bash "$DEVICE_DIR/scripts/a04br3-pre-build.sh"`
  (add `A04BR3_POWERVR_VULKAN=1` for the Mesa stage) and `owner_trust_cache = true`. The pre-build step stages the probe
  into the Data seed and replaces the trust cache with an owner bundle carrying its CDHashes.
- Incremental builds: `build-a04br3-powervr-probe.py --trust-base TRUSTCACHE --trust-out OWNER --addon ADDON`, then
  `unixnu build payload --owner-trust-cache OWNER --gpu-addon ADDON` with the kernel argument `sheng-gpu-addon=1`.
- Host check: `python3 scripts/build-a04br3-powervr-probe.py --check`.
- Terminal PPP packing regression: after building Mesa, run `python3 scripts/check-pvr-terminate-units.py`
  (or pass `--mesa PATH --ppp-header PATH` for other build directories). This runs the actual function and generated
  packers with ASan/UBSan on the host, without GPU access.

The Vulkan stage first checks a fresh linear render-pass clear without a draw, then a linear triangle with CPU direct
readback, then separately fenced transfer clear and copy, and finally the required optimal-image triangle/readback.
Supported direct-render tests must pass their complete pixel checks; unsupported linear attachments are explicitly skipped.
Two default-off Darwin diagnostic switches match only BVNC 22.40.54.30:
`PVR_DARWIN_VENDOR_ISP_OVERLAP=1` compares the vendor ISP_CTL bit 19 policy (REA `libsrv_um.so` `FUN_0012877c`);
`PVR_DARWIN_TERMINATE_TILE_UNITS=1` compares 16-pixel terminal PPP clipping for the tile16 / SIPF-v1 device.
The owner probe and the a04br3 Metal board hook enable the terminal-clip switch and explicitly clear the overlap switch.
The terminal clip matches the vendor GLES path and has passed full-image clear, linear triangle, transfer and optimal
triangle readback on the device (130 steps, zero failures). Other devices keep the default path. GPU-composited UI and
performance still need separate validation; the overlap comparison has not been exercised on hardware.

## Optional GPU buffer guard

The Darwin winsys supports a default-off `PVR_DARWIN_BUFFER_GUARD=1` path on BVNC 22.40.54.30 with 4 KiB pages and
host import. It keeps imported CPU pointer ranges unchanged. An affected buffer gets an additional GPU virtual page backed
by a separate private BO, with independent map/unmap references and rollback. Buffer requirements can then omit the old
extra physical padding page, allowing exact-size caller allocations such as the GOP framebuffer. Image bindings and other
winsys implementations keep their existing behavior. This does not implement general robust buffer access.

The owner probe and a04br3 Metal board hook opt in. Run `python3 scripts/check-pvr-buffer-guard.py` after applying the
pinned Mesa patch to exercise the actual requirements/binding/map functions with ASan/UBSan (228 fixtures plus the actual
CreateBuffer alignment check). Host tests and Mesa compilation passed; real host import and accelerated presentation
still require device validation. The existing void unmap API retains its existing error behavior.

## Optional existing GPU clock source

The default GPU clock remains the 26 MHz crystal. The boot argument `a04br3-gpu-clock=380` opts into the existing
MMPLL parent (TOP MFG selector 7). It does not program a PLL or PMIC voltage. Before switching, the board checks the
MMPLL enable/power state, reads its PCW/postdivider twice, and reads the shared MT6392 VCORE enable/status and active
selector through PWRAP. Missing, unstable or unsupported states retain the crystal. A successful mux readback is required
before starting the firmware; both firmware clock fields receive the calculated frequency.

The stock state observed on this board is MMPLL CON0 `0x11`, CON1 `0x020e9d8a`, PWR `5`, and VCORE selector `0x4e`
(configured 1.1875 V). The Linux MT8167 PLL formula yields 380,000,062 Hz. This is a configured clock/rail state,
not a measurement of analog voltage or performance. `a04br3-gpu-clock-diagnostic=1` logs the startup raw values.
The opt-in path is under hardware validation. No change is made for other boards or normal boot arguments.
Run `python3 scripts/check-a04br3-gpu-clock.py` in a normal clone to exercise the actual helpers and initializer
with ASan/UBSan (151 host fixtures, no device or proprietary dump required).

## GPU compositing (PowerVR Metal plugin)

QuartzCore renders through the native Metal frontend of UniXNU on the GE8300: Metal, metal2vulkan, Mesa's PowerVR Vulkan
driver, the NativePowerVR user client. The plugin is UniXNU's vendor-neutral `drivers/NativeMetal/NativeVulkanMetalPlugin.mm`;
this repository adds the board hooks, the accelerator and the Mesa and kernel pieces. The tablet passes Vulkan transfer/readback
and loads the Metal plugin in backboardd. Full-target 3D rendering and Metal UI composition are still under test; publication
alone is not a successful accelerated frame.

| Piece | Where |
| --- | --- |
| Accelerator `AGXAccelerator` (IOAcceleratorES subclass, staged) that the original Metal discovery finds | `package/powervr/NativePowerVRAccelerator.cpp`, built by UniXNU `build-native-adreno-accelerator.py --source ... --into` (board.toml) |
| Publication (IOGPUFamily load, `MetalPluginName NativePowerVRMetal`, sandbox grant) and its connections: GPU (open type 0x5056), display hand-off (0x4148), UART markers (0x4147xxxx) | `package/powervr/NativePowerVRMetal.cpp`, from `unixnu_board_launch()` |
| Host page import (`NativePowerVRMethodImportHostMemory`), hand-off forwarding to NativeGOPPresenter | `package/powervr/NativePowerVRGpu.cpp`, `NativePowerVRABI.h` |
| `VK_EXT_external_memory_host` (IOSurfaces, Metal no-copy buffers, the scanout) | `patches/mesa/pvr-darwin-winsys.patch` |
| Plugin board hooks: Mesa environment, UART text, hand-off transport | `package/powervr/user/NativePowerVRMetalBoard.mm` |
| Plugin build, seed staging, trust | `scripts/build-a04br3-metal-plugin.py`, `a04br3-seed-powervr-probe.py --metal-plugin`, `build-a04br3-powervr-probe.py --extra-trust` |

- Build: `pre_build = A04BR3_POWERVR_PROBE=1 A04BR3_POWERVR_METAL=1 bash "$DEVICE_DIR/scripts/a04br3-pre-build.sh"` with
  `owner_trust_cache = true`. `A04BR3_POWERVR_METAL_MODE=selftest` (default) runs the bridge self-test in backboardd and keeps
  the CPU renderer; `publish` makes backboardd composite on the GPU. The step fetches the pinned metal2vulkan and Rust 1.98.0.
- Kernel arguments (not in board.toml yet): `a04br3-gpu-metal=1` publishes the accelerator; `sheng-fb-gpu=1` lets the GPU copy
  the composited frame into the LK frame buffer (NativeLegacyDisplay offers frames to NativeGOPPresenter, which the plugin serves).
  Optional: `a04br3-gpu-metal-grant=<names>` (default `backboardd`), `a04br3-gpu-metal-opens=<n>` (default 40).
- UART: `powervr-metal: ...` for the publication and every GPU connection, `powervr-metal[<process>]: <line>` for the plugin's
  lines: `M s.. c.. r.. i.. q.. k<ok>/<failed> p<ok>/<failed>` (self-test: compute, render, host import, QuartzCore kernels and
  render pairs), `P<step> e<hex>` and `PF <frames>/<failures> ...` (scanout), `ST..`/`PUB ..` (status every 20 s).
- Offline pipeline check: `scripts/make-pvr-m2v-profile.py` writes a Khronos profile of the driver for the metal2vulkan command line.

### GPU updates preserving Data

The normal clean build above prepares its own Data seed. For subsequent GPU iterations, UniXNU's official `NGPUADD2` addon
can replace the signed GPU files before launchd without regenerating that seed. Keep the original prepared `data.dbr`, its
volume UUID (`XNU_OWNER_DATA_VOLUME_UUID`), the unique chip ID and the same skip-setup option. Do not feed a previously combined
owner trust bundle as the base: regenerate the original pinned bundle with `build-a04br3-trust-cache.py`.

Run from the UniXNU checkout after updating the device source and generating its tree. These examples use the Actions
layout (`device/` inside UniXNU); for the sibling checkouts in the clean-build instructions, replace `device/` with
`../UniXNU-a04br3/`:

```sh
python3 device/scripts/build-pvr-darwin.py --target ios --reset-source
python3 out/a04br3/tree/scripts/build-a04br3-metal-plugin.py \
  --vulkan "$PWD/device/build/mesa-pvr-ios/src/imagination/vulkan/libvulkan_powervr_mesa.dylib" --out "$RUNNER_TEMP/powervr-metal"
python3 device/scripts/build-a04br3-powervr-probe.py --vulkan --out "$RUNNER_TEMP/powervr-probe"
python3 device/scripts/build-a04br3-trust-cache.py --tree "$PWD" --out "$RUNNER_TEMP/trustcache-original"
python3 device/scripts/make-a04br3-gpu-addon.py --probe-dir "$RUNNER_TEMP/powervr-probe" \
  --metal-plugin "$RUNNER_TEMP/powervr-metal" --trust-base "$RUNNER_TEMP/trustcache-original" \
  --trust-out "$RUNNER_TEMP/trustcache-update" --out "$RUNNER_TEMP/gpu-update.addon"
```

Rebuild the kernel when its source changed. Build the payload with `--gpu-addon "$RUNNER_TEMP/gpu-update.addon"`,
`--owner-trust-cache "$RUNNER_TEMP/trustcache-update"` and the original `--data-ramdisk`, plus the original identity/options.
The exporter includes the complete signed Metal bundle (including its resource seal), the probe and the standalone Vulkan
bundle at their existing union-mount paths. It refuses output overwrites and validates the original trust-cache pin.
Check payload capacity and run `scripts/check-a04br3-bootimg.py` after packaging; the boot partition limit is 128 MiB.

### prepared Data を保持する GPU 増分更新

初回は上記のソースビルドで policy・設定・モード sidecar を含む Data を生成します。
初回 payload の `scratch-data.img` に使われた **準備済みの Data** を固定入力として保存してください。
`--skip-setup` を使った場合は、その処理後の seed が対象です。端末バックアップの抽出は不要です。
UUID が同じでも seed の内容が変わると永続 overlay は破棄されるため、増分更新では Data を再ステージしません。

UniXNU checkout から、ソースビルド済み kernel と新しい署名済み probe・Metal 出力を指定します。
元の pinned trust bundle を使い、前回の結合済み owner trust を渡さないでください。

```sh
bash device/scripts/a04br3-build-gpu-update.sh \
  --prepared-data "$STABLE_PREPARED_DATA" --data-volume-uuid "$DATA_VOLUME_UUID" \
  --kernel "$BUILT_KERNEL" --unique-chip-id "$CHIP_ID" \
  --probe-dir "$RUNNER_TEMP/powervr-probe" --metal-plugin "$RUNNER_TEMP/powervr-metal" \
  --trust-base "$RUNNER_TEMP/trustcache-original" --out "$RUNNER_TEMP/gpu-update"
```

boot 引数は現在の `board.toml` を既定とし、必要なら公式 CLI と同じ
`--kernel-arg=NAME=VALUE`・`--omit-kernel-arg NAME` を繰り返し指定します。
旧 payload の引数は FAT 内 `EFI/UniXNU/unixnu.toml` で確認できます。
`payload.json` は FAT に埋め込まれていないため、紛失した report の再作成や指定は要求しません。
helper は Data を読み取り専用で mount して実 UUID を照合し、addon/trust の生成と公式
`unixnu build payload` のみを実行します。kernel の再ビルド、Data への設定再適用、書込みは行いません。
入力は公式 payload 出力ディレクトリの外に保存し、既存の更新出力は上書きしません。
出力 `update.json` は指定入力の hash と seed identity の記録であり、過去のビルド報告書や
バイナリ完全再現性の証明ではありません。公式容量検証後、boot.img の 128 MiB 検証も別途必要です。
Data に保持する policy・sidecar の変更が必要な場合は新しい初回 seed として扱います。

### GE8300 の順序保証を再現する

以下は既定オフの実験機能です。通常のクリーンビルドと上の GPU 更新手順を使い、
`data.dbr`・元の trust cache・ドライバを、固定された IPSW とこのソースから生成してください。
別の開発環境のファイルや、端末のバックアップから抽出したファイルは必要ありません。
既存 Data を保つ更新時だけ、最初のビルドが生成した Data seed と UUID を再利用します。

カーネルには `a04br3-gpu-bo-wc=1 a04br3-gpu-bo-map=1` を指定します。
元の IOBuffer を WC で確保し、その所有 descriptor をユーザー空間へ直接 map する組合せです。
board.toml の機種固有引数もこの組合せと `a04br3-gpu-tq-low=1` を選択します。
これは過去の ROA 成功条件を復元する設定で、Metal UI や host-import の動作保証ではありません。
`a04br3-gpu-bo-sync=1`、SIPF1 dump/touch、強制 TQ Clip はこの試験には使いません。

GPU 単体の順序保証試験は、上の probe 生成コマンドへ `--roa-color` を追加し、
生成した probe とドライバから `make-a04br3-gpu-addon.py` で addon と owner trust bundle を作ります。
payload の追加引数は次のとおりです。

```sh
--kernel-arg a04br3-gpu-metal=0 --kernel-arg sheng-fb-gpu=0 \
--kernel-arg a04br3-powervrprobed=1 --kernel-arg a04br3-gpu-tq-low=1 \
--kernel-arg a04br3-gpu-bo-wc=1 --kernel-arg a04br3-gpu-bo-map=1
```

成功条件は `ROA overlap exact readback` の3ケースがそれぞれ4096/4096画素一致し、
期待値が128、135、1003505728となることです。1回の draw に128 instances を重ねるため、
draw 間の barrier で順序保証を代用できません。GLSL と再生成スクリプトは
`package/powervr/user/shaders/` にあり、`scripts/tests/check_powervr_roa_color.py` で照合できます。

Metal UI の別試験では、Metal plugin 生成に
`--roa-color --require-fetch-for-apple3 --pipeline-failure-diag` を追加します。
payload は `a04br3-gpu-metal=1 sheng-fb-gpu=1 a04br3-powervrprobed=0` に切り替えます。
Rust bridge を変更した場合は `--skip-bridge` を使わず再ビルドしてください。
`--require-fetch-for-apple3` は実際の framebuffer read 対応が確認できない場合に限って
Apple3 の互換性 waiver を外し、backend 本来の対応と Apple2 は保持します。
診断時の `IM main` / `slide` は、backboardd 本体の実際のロード位置です。

メモリpoolの別試験は、plugin生成に `--pool-block-mib 16` を追加します。
未指定時はpoolサイズを上書きせず、環境変数も未指定なら既定64 MiBです。
指定できる値は4・8・16・32・64 MiBで、constructorが `NMB_POOL_BLOCK_MIB` を設定します。
初回はNativeMetalの `0008-optional-memory-pool-size.patch` を含むRust bridgeが必要です。
実際のblockサイズはdeviceの割当上限にも制限されます。小さいblockはpoolの余剰領域を減らせる一方、
blockの半分を超える要求をdedicated allocationにする閾値も下がります（64 MiBでは32 MiB超、
16 MiBでは8 MiB超）。そのため専用割当の個数や管理コストが増える可能性があります。
既定値は変更しません。async生成との同時試験ではglobal wired countの減少を観測しましたが、
個々の設定の寄与とUI描画改善は未確認です。
builder検証は `python3 scripts/tests/check_powervr_metal_diagnostics.py` で実行できます。

通常pageoutのメモリ圧縮を試す場合は、payload生成へ `--kernel-arg vm_compressor=2` を追加します。
これは固定XNUソースの `VM_PAGER_COMPRESSOR_NO_SWAP` で、通常のin-core compressorを有効にし、
active swapを有効にはしません。独自のメモリ管理patchや物理RAMサイズの変更ではありません。
現行の既定mode 8はfreezer用の圧縮で、通常pageoutのcompressorはactiveではありません。
wired GPUバッファは圧縮できず、CPU負荷・圧縮storeの消費が増える可能性があります。
4Kページの圧縮・復元とUI改善の実機試験が必要なため、boardの既定起動引数には追加しません。

R32_UINT の単体試験通過は、Metal UI や XR 二平面形式の動作成功を意味しません。
画面の確認は framebuffer と実際の present/retire を使い、shader 作成成功だけで完了扱いしません。
この機種では CPU 描画の最初の画面まで起動後約13分かかった例があるため、
無出力だけで停止を判定せず、panic 等がなければ十分な観測時間を確保してください。
追加引数で1000 byteを超える場合は、UniXNU の `--omit-kernel-arg` で不要な診断・自動入力設定を明示的に外し、
上限を拡大しないでください。例は `--omit-kernel-arg offline-hid-auto-home --omit-kernel-arg offline-hid-auto-home-count` です。

## Credits

- [UniXNU](https://github.com/UniXNU/UniXNU) and [UniXNU-sheng](https://github.com/UniXNU/UniXNU-sheng): build
  driver, board file format, ADT generator structure (scripts/make-a04br3-adt.py is adapted from make-sheng-adt.py).
- [ios-sweet](https://github.com/dipa-studio/ios-sweet): security status properties for the activation path.
- The earlier a04br3 ports by the same maintainer, the source of the MT8167 values:
  [xnu_kernel_benesse_a04br3](https://github.com/mouseos/xnu_kernel_benesse_a04br3),
  [xnu_bootshim_benesse_a04br3](https://github.com/mouseos/xnu_bootshim_benesse_a04br3),
  [xnu_device_benesse_a04br3](https://github.com/mouseos/xnu_device_benesse_a04br3),
  [edk2-mtk](https://github.com/mouseos/edk2-mtk) (branch a04br3-upstream-port).

### Reviewed loader memory-map diagnostic source pins

These pins identify the reviewed default-off descriptor diagnostic source, not a
hardware-qualified larger-RAM configuration. The earlier image checkpoint above
is unchanged. No board default enables the diagnostic and no owned-memory size,
UEFI reservation or resource ownership changes are included.

| Repository | Commit | Branch |
| --- | --- | --- |
| mouseos/UniXNU | d8c9980ad36068dea0c67d6be65e511dfa148543 | diag/darwinboot-memory-map |
| mouseos/DarwinBoot | 022a00e42e4f9d531753f5504cb41d840ca94c3f | diag/darwinboot-memory-map |
| mouseos/uefi-a04br3 | d331be804970a5534e0fec973e0c6bdf993e3ad5 | diag/darwinboot-memory-map |

The base pin records both the DarwinBoot gitlink and its mouseos fork URL, so a
fresh recursive checkout does not depend on a local remote override. Use the
one-build `DARWINBOOT_MEMORY_MAP_DIAG=1` firmware-defines override documented in
that base's `docs/guide/boot.md`; it calls the official firmware build path without
editing `board.toml`. The build log must contain the explicit define. The
result is `Build/a04br3Pkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd` in the UEFI checkout.
Source/host checks passed; actual firmware compilation and boot remain separate
validation steps. This recipe performs no flash or reboot.

### P29 Home performance sampling (experimental, default off)

The source branch based on device commit `32fe8b7` adds an opt-in sampler:
`a04br3-home-perf=1 a04br3-proclog=10`. This reuses the heartbeat worker;
without a nonzero `a04br3-proclog` there are no periodic samples. When the
new sampler is enabled, the large periodic all-process name list is suppressed
to reduce UART traffic. Use a longer
interval where UART traffic itself would affect observation. Global VM pages
are approximate independent reads; task resident, compressed, and footprint
are separate ledger quantities, not additive totals. The per-task `cpu_at_ns` timestamp and CPU counter are nanoseconds since boot
and cumulative CPU time respectively;
compare matching pid, name and `uid` across samples; CPU reset or exec still
requires discarding that interval.
The task ledger is charged on scheduling and is not a wall clock. Rates can
exceed 100% on SMP. At most ten named tasks and 32 threads per task are
sampled; `busy` marks failed try-locks, `sampled=0` means no wait state, and
`run`/`wait` are partial instantaneous counts, not CPU time or a full wait
history. Process iteration may block even with NOWAITTRANS.

The first 32 physical touch edges report the monotonic time immediately before
publishing and the duration of the synchronous publish callback. These markers
are not an end-to-end input-to-visible-frame measurement; a reported HID count
is not proof that SpringBoard handled that event. The monitor also emits the
existing display and storage-cache counters, when available. Console output
and sampling perturb timing; compare against the original P29 as a control.
No device execution or image build is claimed for this branch.

The display probe retains the latest 32 monotonic timestamp markers between reports:
stage 0 is acceptance of a display swap, stage 1 follows a successful GPU
offer, and stage 2 is the existing successful transfer hook, used by CPU copies and
successful GPU handoffs. The
markers identify observation points only: stage 0 is not the app render
command-buffer commit, stage 2 alone is not proof that pixels are visible, and no causal pairing
with a touch is established. Overwriting older entries or lock contention is counted as
`dropped`; events are emitted later by the heartbeat. A try-lock failure
can race the reporting boundary, so the per-batch drop count is approximate. The legacy swap timeline
already stores timestamps but this P29 source has no wired UART command to read
it. P29 can provide existing touch/report and aggregate scanout data without
reboot; these new markers need a rebuilt image.

Input `seq` and `reports` are shared values read after publishing; with the
synthetic self-test running they may refer to another publisher. HID reports
are attempted reports, not necessarily successful delivery. The input log is
synchronous and can delay subsequent polling. An error-triggered release is
marked `error=<code>`.

### Launchd CPU-path sampling (experimental)

On this diagnostic branch `a04br3-home-perf=1` additionally reports launchd's
recount task user/system totals and up to eight sampled threads. `split=1`
means XNU was compiled with precise user/kernel accounting. With `split=0`,
XNU folds system time into `user_ns`; the two modes must not be interpreted
alike. Recount and ledger counters are separately sampled and may not match;
use cumulative differences from the same thread ID and process unique ID.

Each thread has a state, wait event, CPU totals and a separate row of saved user
PC/LR and kernel continuation. Saved user registers can be stale while the
thread runs in the kernel; the continuation is not the current instruction.
Only non-runnable threads with an available kernel stack get a bounded saved
kernel frame walk, and this is not a live backtrace of a running thread. Busy
scheduler locks omit samples. The sampler can enumerate at most the first 32
threads and retains at most eight successful launchd observations per interval;
output and sampling can perturb time. Raw kernel addresses require the exact
matching kernel symbols. No user pages are faulted in by this addition.

### Timer-interrupted kernel PC statistics (experimental)

With the generic UniXNU timer hook compiled using `CONFIG_XNU_IRQ_PC_STATS=y`
(or `XNU_IRQ_PC_STATS=1` if the selected defconfig leaves it unset), the
existing `a04br3-home-perf=1` runtime opt-in enables PID 1 sampling. Explicit
`native-irq-pid=<pid>` selects another PID; `native-irq-pid=0` disables it.
Without the compiled hook, the existing performance monitor has no timer
sampling. The monitor prints `hook=0` if an explicit PID is selected without
that hook, so zero samples are not mistaken for an idle task. A nonzero
`a04br3-proclog` remains necessary to start the reporting worker.

Every sixteenth target PID kernel-mode timer interrupt contributes the
interrupted unslid PC rounded down to a 256-byte bucket. Fixed atomics, no
allocation, no waiting, no stack walk and no UART output run in the interrupt.
The existing process-monitor worker copies and clears the 64-slot table each
interval, releases its try-lock, then prints up to eight most frequent buckets.
IRQ samples that find the table busy are dropped and counted in `busy`; a full
table increments `overflow`. Both counters are cumulative, while bucket hits
and `stored` belong to the reported `epoch`. The first sampled process unique
ID is pinned; samples for a later process reusing the PID increment `other`
and do not enter the histogram. `ticks` includes all PID generations and
`other` counts only rejected sampled ticks, so these do not share a population.
Reports are not transactional across counters. This rotation permits new PC
buckets in later intervals even if the previous table filled. The atomic
exclusive instructions can retry and have no strict execution-time bound.
Sampling depends on timer scheduling and
interrupt masking, so they are not percentages of CPU time. Addresses are
unslid: symbolize bucket ranges against the exact kernel Mach-O and do not
assume every address in a 256-byte bucket belongs to one function. The feature
requires an image rebuild; it cannot inspect the already running P31 kernel.

IRQ epoch reports include boundary times (`start_ns=0` for the first interval),
interval `stored`/`lost_full`, and missed report attempts (`skipped`). The
`busy` counter is cumulative and asynchronous: an IRQ may increment it while
the report holds the table lock, so differences only approximate per-epoch
contention and cannot yield exact per-epoch loss percentages.

Experimental MSDC interrupt wait: `XNU_MSDC_IRQ_WAIT=1` in the environment
passed to the board build enables `NATIVE_MSDC_IRQ_WAIT=1` and
`ARMCPU_SPI_SERIALIZE=1`. Add `native-msdc-irq=1` to the kernel arguments only
for an explicitly selected experiment; `native-msdc-dma=1` is also required.
The ADT carries SPI 78 and SYSIRQ polarity-bank metadata but does not itself
activate interrupts. Use a UniXNU checkout whose NativeMsdc submodule contains
the corresponding IRQ wait change and a board ArmCpu submodule containing the
serialization change; commits must be available to the build checkout. The
experiment has not been validated on physical hardware.

For read-only userspace measurements compile `tools/msdc-read-bench.c` with a
C11 compiler and run `msdc-read-bench FILE OFFSET BYTES ITERATIONS`. It checks
all repeated reads against the first read and prints bytes, elapsed monotonic
time and process CPU time. It is affected by the filesystem/page cache and
includes comparison overhead, so it cannot alone establish eMMC throughput or
DMA CPU cost. The caller must compare identical inputs and boot conditions and
correlate with kernel IRQ counters. No writes are made by the benchmark.

Experimental fixed source pair: UniXNU commit `435291622f3fc82def4776b07ddaf79874b6d7e4`
selects NativeMsdc `daf6c0b7e8eeb309290f993d9b961e2402717fe8`; this device
revision selects ArmCpu `f3551364f7bb587463059a4648448d608a2ee5da` through
its gitlink. Both source commits must be published to their configured remotes
before a fresh recursive clone can fetch them. The flags above are required;
the pair alone does not activate IRQs. Kernel argument capacity must be checked:
the existing P46/P54 diagnostic configurations can approach the 1023-byte limit.

Without the worker option, IRQ wait is limited to explicitly marked reads on the
driver-owned publication thread. With it, verified read-only block requests can
also use the worker; writable windows and store callbacks continue to poll.
A rejected GIC mask readback is recorded; persistent unquenchable IRQs panic.
SYSIRQ writers that bypass ArmCpu controller synchronization can still race.

On this board, the explicit experimental build option `XNU_MSDC_GIC_NS=1`
requires `XNU_MSDC_IRQ_WAIT=1` and declares a non-secure GICv2 access path.
It permits a Security-Extensions implementation to expose IGROUPR as RAZ/WI;
priority, target and enable readbacks are still required. This does not prove
interrupt delivery. The option is off by default and must not be assumed on
other hardware without establishing its security state.

Experimental verified-root read executor: additionally export
`XNU_MSDC_IRQ_WORKER=1` together with `XNU_MSDC_IRQ_WAIT=1` when building;
`XNU_NATIVE_MSDC_BLOCK=1` and `--verified-root` are required. The new executor
is otherwise compiled out. At saturation or thread-creation failure a read
uses the original polled path. It begins with one I/O thread and one completion
thread and grows completion threads on demand to at most 64. Physical testing,
peak resource impact and full kernel link remain unverified.

The opt-in UART reporter for MSDC IRQ and read-worker counters uses the existing
process heartbeat. Both `a04br3-proclog=10` and `a04br3-home-perf=1` are
required, together with either `a04br3-msdc-perf=1` or
`native-msdc-iostat=1`. The latter automatically enables this reporter;
`a04br3-msdc-perf=1` remains available independently. Both flags default to
off and only the value 1 enables them.
Each heartbeat prints cumulative IRQ captures, wait attempts, undelivered wait
timeouts and wait nanoseconds; worker jobs queued and completed, direct-path
fallbacks and the reserved counter; and GIC mask failures. `unavailable` means
the weakly imported symbol is absent, not that its value is zero. A zero worker
count alone does not prove the worker was compiled or started. `no_delivery`
counts the timeout case after sleeping without ISR delivery, not every reason a
transfer can fall back to polling. `wait_ns` is elapsed wait time, not CPU
time. Individual counters use relaxed atomic loads; the collection is not an
atomic snapshot and counters may change between loads. The reporter neither
enables IRQ nor verifies data or IRQ delivery. Test the actual formatter and
weak-symbol handling with `python3 scripts/tests/test_msdc_irq_report.py`.

With NativeMsdc `965ecd384a9fbd33b21674380edddd2bd57a4acc`, use
`a04br3-proclog=10 a04br3-home-perf=1 native-msdc-iostat=1`. The same heartbeat
then emits a monotonic begin/end bracket and cumulative size-binned transport,
block-offer, and worker queue counters. Use deltas between sample timestamps:
successful transport byte deltas divided by elapsed sample time describe
observed throughput, not peak bus speed. The transport duration includes
failed operations and excludes host-lock wait; offered block requests include
rejected requests, and queue time excludes direct reads and cancellations.
Fields are individually atomic but not captured at one instant. Diagnostics add
clock reads and atomic updates in the driver, plus up to 18 UART lines per
heartbeat; reduce sampling frequency for less perturbation. The existing VM
rows show page usage, and home-perf samples selected tasks; neither measures
memory bandwidth nor provides complete CPU, GPU, or network utilization.
IRQ counters, cache reports, VM rows and I/O samples have different collection
windows even within one heartbeat. These counters do not establish the cause of
a slowdown without same-boot measurements and a comparison. Run
`python3 scripts/tests/test_msdc_io_report.py` for reporter coverage.

For a UART capture of one boot, identify the byte offset of the unique
`[pmic6392_init] Preloader Start` anchor in the original source. Record the
SHA-256 of the complete original source and the SHA-256 of bytes from that
anchor through EOF; pass them as `--source-sha256`, `--boot-start-offset`, and
`--boot-id` respectively to `device/scripts/summarize-msdc-io.py`. The boot ID
is a hash of the captured segment, not an authenticated hardware boot UUID.
The analyzer requires these exact identities and checks the unique preloader
and BLDR boundaries, boot reason position, counter monotonicity and sample
ordering before writing any JSON. Each sample must have a consecutive sequence,
nine transfer rows, six offered rows and one queue row in that order, all
carrying that sequence, followed by a matching end with 16 rows. Missing or
mixed rows, malformed suffixes, missing ends and overlapping snapshot windows
are errors; no JSON is emitted for the file. A complete interval with zero
counter deltas is valid and reports `activity_observed=false`; it does not prove
that no work occurred between the non-atomic loads. Queue output includes the
delta of started jobs and cumulative wait; its maximum is the cumulative maximum
at the end of the interval, not an interval-only maximum. Older reporter logs
without this framing are rejected. The tool rejects observed reboot
markers even if time and counters increased. Capture continuity
still must be established externally: missing UART output or deliberate
concatenation without a recognizable boundary cannot be disproved from hashes
or counters. These rates inherit the non-atomic snapshots and logging overhead
above. This tool covers MSDC transport, not whole-system utilization or memory
bandwidth.
