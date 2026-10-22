# UniXNU-a04br3

UniXNU device support for the Benesse TAB-A04-BR3 (a04br3), a tablet with the MediaTek MT8167 SoC
(four Cortex-A35 cores, GIC-400, 2 GiB DRAM, eMMC). Work in progress: board file, kernel configuration,
Apple device tree generator and GPT layout. No hardware run with UniXNU is documented yet.

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

## launchd without a display

The launchd-side parts of UniXNU-tb128fu and UniXNU-sheng that do not need a display or a boot-progress page:

- AppleKeyStore stand-in (`offline-key-events=1`, `sheng-aks=1`): `package/KeyStoreProbe.cpp` (from UniXNU-tb128fu) and
  UniXNU `drivers/NativeKeyStoreEvents`, linked in `libA04BR3Board.a` and started from `unixnu_board_init`
  (`package/A04BR3Board.cpp`). The other boards start it from their display probe.
- launchd disabled jobs (`sheng-disable-watchdogd=1`, `sheng-disable-jobs=...`): `scripts/a04br3-seed-launch-policy.py`
  writes `/private/var/db/com.apple.xpc.launchd/disabled.plist` into the Data seed at build time with the bytes of
  `package/BootWatchdogPolicy.c` (from UniXNU-tb128fu). The kernel form, `unixnu_board_launch` with
  `CONFIG_XNU_BOARD_LAUNCH=y`, needs the UniXNU change in `upstream/UniXNU/0001-native-board-launch-hook.patch`; UniXNU
  calls the policy today only from its boot-progress page.
- `-no_corpses` and `ml-timeout-lock-panic-scale=40`: stock XNU boot arguments.

## Credits

- [UniXNU](https://github.com/UniXNU/UniXNU) and [UniXNU-sheng](https://github.com/UniXNU/UniXNU-sheng): build
  driver, board file format, ADT generator structure (scripts/make-a04br3-adt.py is adapted from make-sheng-adt.py).
- [ios-sweet](https://github.com/dipa-studio/ios-sweet): security status properties for the activation path.
- The earlier a04br3 ports by the same maintainer, the source of the MT8167 values:
  [xnu_kernel_benesse_a04br3](https://github.com/mouseos/xnu_kernel_benesse_a04br3),
  [xnu_bootshim_benesse_a04br3](https://github.com/mouseos/xnu_bootshim_benesse_a04br3),
  [xnu_device_benesse_a04br3](https://github.com/mouseos/xnu_device_benesse_a04br3),
  [edk2-mtk](https://github.com/mouseos/edk2-mtk) (branch a04br3-upstream-port).
