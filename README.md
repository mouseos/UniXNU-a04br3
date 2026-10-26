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

## Build

The board needs changes to the UniXNU base that are not upstream yet (all behind default-off flags, prepared as pull
requests): the 16550 console (XNU_NS16550_UART), the board hook (XNU_BOARD_INIT), the MSDC personality
(XNU_NATIVE_MSDC), MediaTek image headers in bootimg (mtk_header) and an authenticated DriverKit header listing. Use the
fork [mouseos/UniXNU](https://github.com/mouseos/UniXNU) until they are merged, and the ArmCpu fork with the GICv2 path
(submodule drivers/ArmCpu of this repository).

    git clone --recurse-submodules https://github.com/mouseos/UniXNU
    git clone --recurse-submodules https://github.com/mouseos/UniXNU-a04br3
    git clone --recurse-submodules https://github.com/mouseos/uefi-a04br3
    cd UniXNU
    ./unixnu defconfig a04br3_defconfig -D ../UniXNU-a04br3
    ./unixnu fetch
    ./unixnu build package
    ./unixnu build firmware --uefi ../uefi-a04br3

The current image was built from these revisions (Actions run 39850069847 of mouseos/UniXNU, firmware built locally
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

### GE8300 の順序保証を再現する

以下は既定オフの実験機能です。通常のクリーンビルドと上の GPU 更新手順を使い、
`data.dbr`・元の trust cache・ドライバを、固定された IPSW とこのソースから生成してください。
別の開発環境のファイルや、端末のバックアップから抽出したファイルは必要ありません。
既存 Data を保つ更新時だけ、最初のビルドが生成した Data seed と UUID を再利用します。

カーネルには `a04br3-gpu-bo-wc=1 a04br3-gpu-bo-map=1` を指定します。
元の IOBuffer を WC で確保し、その所有 descriptor をユーザー空間へ直接 map する組合せです。
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
