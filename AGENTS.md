# AGENTS.md (UniXNU-a04br3)

Device repository of the UniXNU port to the Benesse TAB-A04-BR3 (a04br3, MediaTek MT8167, 4x Cortex-A35, GIC-400,
2 GiB DRAM, eMMC). Read README.md first; it records the build commands, the partition layout, the first installation
and the unattended test cycle.

## Common guides

- Port a device: https://unixnu.com/porting/ (steps, device repository, drivers, records, log capture, packaging)
- Build and boot: https://unixnu.com/building
- Repository layout and commit scope: https://unixnu.com/contributing/repository

## Rules

- Board policy, board code and board inputs live here: `board.toml`, `configs/`, `package/` (kits/a04br3 in the build
  tree), `scripts/`. UEFI board code lives in mouseos/uefi-a04br3 (`Platforms/Benesse/a04br3Pkg`). Shared code goes
  to UniXNU or its component repositories behind a default-off flag, in a form that can be proposed upstream.
- Distributed images are build outputs only. Device-specific data (MAC address, calibration, nvram, proinfo) is read
  from the tablet at runtime and never goes into an image, a seed or this repository.
- Builds: the macOS workflow of mouseos/UniXNU (`macos.yml`, device_repository = mouseos/UniXNU-a04br3,
  defconfig = a04br3_defconfig, pre_build = bash "$DEVICE_DIR/scripts/a04br3-pre-build.sh"); firmware with
  `unixnu build firmware --uefi ../uefi-a04br3`; boot image with `unixnu bootimg`.
- First installation: `scripts/make-a04br3-flash.py --bundle` (mtkclient, once). Later images: LK fastboot and
  `scripts/run-a04br3-unattended.sh` (UART0 921600 8N1, return to fastboot through the RTC flag and the watchdog).
- Partition table: `scripts/a04br3-gpt.json`, written by `scripts/make-a04br3-gpt.py` and checked against a stock
  dump by `scripts/check-a04br3-gpt.py`. logo, the bootloader and calibration partitions keep their stock places.
- Keep host results and hardware results apart, with the revisions, commands and logs that produced them.
- Commit messages describe the change (`feat(<area>)`, `fix(<area>)`, `docs`, `build(<area>)`, `chore`).
