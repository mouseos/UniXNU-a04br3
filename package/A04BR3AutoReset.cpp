// SPDX-License-Identifier: MIT
// a04br3 (Benesse TAB-A04-BR3, MediaTek MT8167) unattended test cycle: boot argument a04br3-autoreset=<seconds>.
// Default off: without the argument nothing in this file touches hardware.
//
// unixnu_board_init() is called once from bsd_init (CONFIG_XNU_BOARD_INIT=y, patch native-board-init-hook). With the
// argument it does:
//  1. Set bit 13 of the PMIC RTC register RTC_PDN1 (0x802C). The stock LK checks that bit first and, when set, prints
//     "[FASTBOOT] reboot to boot loader", enters fastboot mode and clears it (REA of the stock LK, FUN_41e032e4,
//     FUN_41e0667c, FUN_41e06638). So every later reset (panic, watchdog, the timer below) lands in fastboot, where the
//     host flashes or boots the next image with fastboot: no mtkclient.
//  2. Arm the MT8167 TOPRGU hardware watchdog and kick it from a kernel thread, so a hung kernel also resets.
//  3. After <seconds>, reset through TOPRGU.
// Register sequences:
//  - PWRAP WACS2 (0x1000F000 +0xA0 CMD, +0xA4 RDATA, +0xA8 VLDCLR) and the RTC unlock/write-trigger order are those of
//    the stock LK (FUN_41e0588c, FUN_41e06280, FUN_41e06254).
//  - TOPRGU 0x10007000 (MODE key 0x22000000, LENGTH key 0x08, RESTART key 0x1971, SWRST key 0x1209) follows Linux
//    drivers/watchdog/mtk_wdt.c and the restart contract of github.com/mouseos/xnu_kernel_benesse_a04br3
//    (iokit/Kernel/arm/A04BR3Restart.h), which ran on this tablet.
#include <IOKit/IOLib.h>
#include <kern/thread.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <stdint.h>

extern "C" kern_return_t kernel_thread_start(void (*)(void *, wait_result_t), void *, thread_t *);
extern "C" void thread_deallocate(thread_t);

namespace {

constexpr uint64_t kPwrap = 0x1000F000, kToprgu = 0x10007000;
constexpr unsigned kWacsCmd = 0xA0, kWacsRdata = 0xA4, kWacsVldclr = 0xA8;
constexpr uint32_t kRtcBbpu = 0x8000, kRtcPdn1 = 0x802C, kRtcProt = 0x8036, kRtcWrtgr = 0x803C;
constexpr uint32_t kRtcPdn1Fastboot = 1U << 13;
constexpr unsigned kWdtMode = 0x00, kWdtLength = 0x04, kWdtRestart = 0x08, kWdtSwrst = 0x14;
constexpr unsigned kWatchdogSeconds = 31;   // TOPRGU LENGTH holds at most 31 s (Linux mtk_wdt max_timeout)

volatile uint8_t *gPwrap, *gToprgu;
unsigned gResetSeconds;

uint32_t rd(volatile uint8_t *base, unsigned offset) { return *reinterpret_cast<volatile uint32_t *>(base + offset); }
void wr(volatile uint8_t *base, unsigned offset, uint32_t value)
{
  *reinterpret_cast<volatile uint32_t *>(base + offset) = value;
  __asm__ volatile("dsb sy" ::: "memory");
}

// One WACS2 transaction, as the stock LK: wait for the idle FSM (clearing a pending valid), issue, and for a read wait
// for valid data. Bounded: false on timeout or when the wrapper never finished its initialization.
bool wacs(bool write, uint32_t address, uint32_t data, uint32_t *out)
{
  for (unsigned spin = 0;; spin++) {
    const uint32_t status = rd(gPwrap, kWacsRdata);
    if ((status & (1U << 21)) == 0 || spin > 1000000) { return false; }  // INIT_DONE
    const uint32_t fsm = (status >> 16) & 7;
    if (fsm == 0) { break; }
    if (fsm == 6) { wr(gPwrap, kWacsVldclr, 1); }
  }
  wr(gPwrap, kWacsCmd, (write ? 1U << 31 : 0) | ((address >> 1) << 16) | (data & 0xffff));
  if (write) { return true; }
  for (unsigned spin = 0;; spin++) {
    const uint32_t status = rd(gPwrap, kWacsRdata);
    if (spin > 1000000) { return false; }
    if (((status >> 16) & 7) == 6) {
      *out = status & 0xffff;
      wr(gPwrap, kWacsVldclr, 1);
      return true;
    }
  }
}

bool rtcTrigger(void)
{
  if (!wacs(true, kRtcWrtgr, 1, nullptr)) { return false; }
  for (unsigned spin = 0; spin < 100000; spin++) {
    uint32_t bbpu = 0;
    if (!wacs(false, kRtcBbpu, 0, &bbpu)) { return false; }
    if ((bbpu & (1U << 6)) == 0) { return true; }  // CBUSY clear
  }
  return false;
}

bool setFastbootFlag(void)
{
  uint32_t pdn1 = 0;
  if (!wacs(true, kRtcProt, 0x586a, nullptr) || !rtcTrigger() ||
      !wacs(true, kRtcProt, 0x9136, nullptr) || !rtcTrigger() ||
      !wacs(false, kRtcPdn1, 0, &pdn1) ||
      !wacs(true, kRtcPdn1, (pdn1 & 0xffff) | kRtcPdn1Fastboot, nullptr) || !rtcTrigger()) {
    return false;
  }
  uint32_t check = 0;
  return wacs(false, kRtcPdn1, 0, &check) && (check & kRtcPdn1Fastboot) != 0;
}

void watchdogArm(unsigned seconds)
{
  wr(gToprgu, kWdtLength, ((seconds << 6) << 5) | 0x08);
  wr(gToprgu, kWdtRestart, 0x1971);
  const uint32_t mode = (rd(gToprgu, kWdtMode) & 0x00ffffffU & ~0x08U & ~0x40U) | 0x22000001U;  // EN; no IRQ, no dual mode
  wr(gToprgu, kWdtMode, mode);
}

void watchdogKick(void) { wr(gToprgu, kWdtRestart, 0x1971); }

void resetNow(void)
{
  wr(gToprgu, kWdtRestart, 0x1971);
  wr(gToprgu, kWdtMode, (rd(gToprgu, kWdtMode) & 0x00ffffa6U) | 0x22000014U);
  IODelay(100);
  wr(gToprgu, kWdtSwrst, 0x1209);
  for (;;) { __asm__ volatile("wfi"); }
}

void cycleThread(void *, wait_result_t)
{
  const uint64_t started = mach_absolute_time();
  uint64_t limit = 0;
  nanoseconds_to_absolutetime(static_cast<uint64_t>(gResetSeconds) * NSEC_PER_SEC, &limit);
  for (;;) {
    watchdogKick();
    if (mach_absolute_time() - started >= limit) {
      IOLog("a04br3-autoreset: %u s elapsed, resetting into fastboot\n", gResetSeconds);
      IOSleep(200);  // let the console drain
      resetNow();
    }
    IOSleep(1000);
  }
}

}  // namespace

extern "C" void unixnu_board_init(void)
{
  unsigned seconds = 0;
  if (!PE_parse_boot_argn("a04br3-autoreset", &seconds, sizeof(seconds)) || seconds == 0) { return; }
  gResetSeconds = seconds;
  gPwrap = reinterpret_cast<volatile uint8_t *>(ml_io_map(kPwrap, 0x1000));
  gToprgu = reinterpret_cast<volatile uint8_t *>(ml_io_map(kToprgu, 0x1000));
  if (gPwrap == nullptr || gToprgu == nullptr) {
    IOLog("a04br3-autoreset: register mapping failed; cycle disabled\n");
    return;
  }
  const bool flag = setFastbootFlag();
  IOLog("a04br3-autoreset: fastboot flag %s, reset after %u s, watchdog %u s\n", flag ? "set" : "NOT set", seconds,
        kWatchdogSeconds);
  watchdogArm(kWatchdogSeconds);
  thread_t thread = nullptr;
  if (kernel_thread_start(cycleThread, nullptr, &thread) == KERN_SUCCESS) {
    thread_deallocate(thread);
  } else {
    IOLog("a04br3-autoreset: thread start failed; the watchdog still resets\n");
  }
}
