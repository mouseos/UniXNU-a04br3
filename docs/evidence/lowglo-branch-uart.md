# UART excerpt: memory floor 0x52000000 with the moved low globals

trials/lowglo-branch (2026-11-02): official macos.yml run 41360286449 (UniXNU 61451ae wip/a04br3-low-globals, device 73cc3ef, a04br3_defconfig with CONFIG_XNU_LOW_GLOBALS_BELOW=y) + official firmware FD a3bd3f60 (DarwinBoot 023daa9). One boot, ended by the UART reset request.

```text
A04BR3 SEC: cpu-1300 after: armpll pll1=800C8000 pcw=C8000 (1300 MHz) vproc VOSEL_ON=0060 1300 mV other=0060 hp_en=00000100 (register values; measure the clock with the PMU)
[A04BR3 eMMC] mode=hs200 clk=188000000 width=8 cmd=rise:10 map=7FFFFFFF/00000000 data=rise:10 map=FFFFFFFF/00000000 iocon=00000000 pad=002A2A00 pb=403C0046/FFFF0609/540A180F verify=ok
DARWINBOOT: kernel low globals at 0xFFFFFFEFE0000000; static region floor 0xFFFFFFEFE0800000 (kernel va 0xFFFFFFF00427C000)
DARWINBOOT: memory floor 0x52000000 accepted, below window 0x1607C000, total 0x6C67C000
vm_page_bootstrap: 316582 free pages, 127446 wired pages, (up to 0 of which are delayed free)
powervr-metal[backboardd]: M2VP set auto=8 mem=1734
a04br3-autoreset: reset requested on UART, resetting into fastboot
a04br3 display: stage 10 meta 0 start 1 gop be9c0000+3e8000 800x1280 reg 1 surface 0 pm 0 client pid 80 calls 5 swaps 510 reason 0 transfers 509 status 0 presented 509 retired 509
```
