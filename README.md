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

[NativeMsdc](https://github.com/mouseos/NativeMsdc) (`drivers/NativeMsdc`, `CONFIG_XNU_NATIVE_MSDC=y`) drives the eMMC on
MSDC0 (`/arm-io/mmc0`). It does nothing unless `native-msdc-root` or `native-msdc-persist` is set. The userdata windows
of the root image (`native-msdc-root`) need `CONFIG_XNU_NATIVE_MSDC_BLOCK=y` and the storage ABI audit described in
`board.toml`, the same input that NativeRamBlock waits for.
## Flashing

`scripts/make-a04br3-flash.py --boot out/a04br3/boot/boot.img --userdata out/a04br3/userdata/userdata-root.img --out DIR`
writes `DIR/write.mtk` for `mtk script DIR/write.mtk` (mtkclient, first installation): boot, userdata, then both GPT
copies. Later images go through fastboot (below).

## Unattended test cycle

The stock LK enters fastboot when bit 13 of the PMIC RTC register RTC_PDN1 (0x802C) is set and clears it (REA of the
stock LK). With the boot argument `a04br3-autoreset=<seconds>` the kernel (`package/A04BR3AutoReset.cpp`, called through
the board hook `unixnu_board_init`) sets that bit, arms the TOPRGU watchdog and resets after the given time. A panic, a
hang or the timer therefore brings the tablet back to fastboot, and `scripts/run-a04br3-unattended.sh` writes the next
image with fastboot, reboots it and cuts the UART log of the run (UART0, 921600 8N1). No mtkclient is needed after the
first installation. Without the boot argument nothing is changed.

## Credits

- [UniXNU](https://github.com/UniXNU/UniXNU) and [UniXNU-sheng](https://github.com/UniXNU/UniXNU-sheng): build
  driver, board file format, ADT generator structure (scripts/make-a04br3-adt.py is adapted from make-sheng-adt.py).
- [ios-sweet](https://github.com/dipa-studio/ios-sweet): security status properties for the activation path.
- The earlier a04br3 ports by the same maintainer, the source of the MT8167 values:
  [xnu_kernel_benesse_a04br3](https://github.com/mouseos/xnu_kernel_benesse_a04br3),
  [xnu_bootshim_benesse_a04br3](https://github.com/mouseos/xnu_bootshim_benesse_a04br3),
  [xnu_device_benesse_a04br3](https://github.com/mouseos/xnu_device_benesse_a04br3),
  [edk2-mtk](https://github.com/mouseos/edk2-mtk) (branch a04br3-upstream-port).
