// SPDX-License-Identifier: MIT
// a04br3 (Benesse TAB-A04-BR3, MediaTek MT8167) unattended test cycle: boot argument a04br3-autoreset=<seconds>.
// Default off: without the argument nothing in this file touches hardware.
//
// a04br3_autoreset_init() is called once from unixnu_board_init() (package/A04BR3Board.cpp) in bsd_init. With the
// argument it does:
//  1. Set bit 13 of the PMIC RTC register RTC_PDN1 (0x802C). The stock LK checks that bit first and, when set, prints
//     "[FASTBOOT] reboot to boot loader", enters fastboot mode and clears it (REA of the stock LK, FUN_41e032e4,
//     FUN_41e0667c, FUN_41e06638). So every later reset (panic, watchdog, the timer below) lands in fastboot, where the
//     host flashes or boots the next image with fastboot: no mtkclient.
//  2. Arm the MT8167 TOPRGU hardware watchdog and kick it from a kernel thread, so a hung kernel also resets.
//  3. After <seconds>, reset through TOPRGU.
//  4. Reset on request: the same thread polls UART0 receive (16550 LSR.DR, 0x11005000) every 100 ms and resets into
//     fastboot as soon as the host sends the line "A04BR3!RST" (scripts/a04br3-reset.sh), so a round ends when the
//     evidence is in, without waiting for the timer. This needs serial=1 (output only) in the boot arguments: with
//     serial=3 XNU's serial keyboard thread (osfmk/console/serial_general.c) drains the receive FIFO every 16 ms.
//     Other received bytes are dropped.
// Register sequences:
//  - PWRAP WACS2 (0x1000F000 +0xA0 CMD, +0xA4 RDATA, +0xA8 VLDCLR) and the RTC unlock/write-trigger order are those of
//    the stock LK (FUN_41e0588c, FUN_41e06280, FUN_41e06254).
//  - TOPRGU 0x10007000 (MODE key 0x22000000, LENGTH key 0x08, RESTART key 0x1971, SWRST key 0x1209) follows Linux
//    drivers/watchdog/mtk_wdt.c and the restart contract of github.com/mouseos/xnu_kernel_benesse_a04br3
//    (iokit/Kernel/arm/A04BR3Restart.h), which ran on this tablet.
#include <IOKit/IOLib.h>
#include <IOKit/IOPlatformExpert.h>
#include <kern/thread.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <stdint.h>
#include <string.h>
#include <libkern/libkern.h>

extern "C" kern_return_t kernel_thread_start(void (*)(void *, wait_result_t), void *, thread_t *);
extern "C" void thread_deallocate(thread_t);
// Not exported to the IOKit compile context (as in BootReset.c of UniXNU-sheng, which declares ml_io_map_unmappable).
extern "C" vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
// XNU 10063 kern/debug.h (KERNEL_PRIVATE), config/Private.exports.
extern "C" unsigned panic_active(void);
// The first bootstrap kernel links a display stub without NativeHidInput.
extern "C" volatile uint32_t native_hid_input_command __attribute__((weak));
// Exported for the opt-in CPU PMU experiment. While a probe is in progress,
// periodic WDT feeding stops so an upper-EL hang can return to fastboot.
extern "C" {
volatile uint32_t a04br3_runtime_recovery_ready = 0;
volatile uint32_t a04br3_runtime_probe_active = 0;
}

namespace {

constexpr uint64_t kPwrap = 0x1000F000, kToprgu = 0x10007000, kUart0 = 0x11005000;
constexpr unsigned kUartRbr = 0x00, kUartLsr = 0x14;   // 32-bit register spacing
constexpr char kResetRequest[] = "A04BR3!RST";
constexpr char kShotRequest[] = "A04BR3!SHOT";
constexpr char kHomeRequest[] = "A04BR3!HOME";
// The LK frame buffer that NativeLegacyDisplay presents into (the GOP base the UART report of LegacyDisplayProbe.cpp prints,
// "gop be9c0000+3e8000 800x1280"); mapped as device memory like the presenter's own mapping: 800x1280, 32 bpp, stride 3200.
constexpr uint64_t kFrameBuffer = 0xBE9C0000;
constexpr unsigned kFbWidth = 800, kFbHeight = 1280, kFbStride = 3200;
constexpr unsigned kWacsCmd = 0xA0, kWacsRdata = 0xA4, kWacsVldclr = 0xA8;
constexpr uint32_t kRtcBbpu = 0x8000, kRtcPdn1 = 0x802C, kRtcProt = 0x8036, kRtcWrtgr = 0x803C;
constexpr uint32_t kRtcPdn1Fastboot = 1U << 13;
constexpr unsigned kWdtMode = 0x00, kWdtLength = 0x04, kWdtRestart = 0x08, kWdtSwrst = 0x14;
constexpr unsigned kWatchdogSeconds = 31;   // TOPRGU LENGTH holds at most 31 s (Linux mtk_wdt max_timeout)

uint32_t rd(volatile uint8_t *base, unsigned offset);
volatile uint8_t *gPwrap, *gToprgu, *gUart;
unsigned gRequestMatched, gShotMatched, gHomeMatched;
volatile uint8_t *gFb;
bool gShotWanted;

// True once the received bytes end with kResetRequest.
bool resetRequested(void)
{
  if (gUart == nullptr) { return false; }
  for (unsigned n = 0; n < 64 && (rd(gUart, kUartLsr) & 1U); n++) {
    const char c = static_cast<char>(rd(gUart, kUartRbr) & 0xff);
    // Use the same mailbox as the physical Home key. NativeHidInput emits the
    // complete press/release pair; this can wake iPadOS without resetting it.
    if (c == kHomeRequest[gHomeMatched]) {
      if (++gHomeMatched == sizeof(kHomeRequest) - 1) {
        if (&native_hid_input_command) native_hid_input_command = 1;
        gHomeMatched = 0;
        IOLog("a04br3 input: UART Home %s\n", &native_hid_input_command ? "requested" : "unavailable in bootstrap kernel");
      }
    } else {
      gHomeMatched = (c == kHomeRequest[0]) ? 1 : 0;
    }
    if (c == kShotRequest[gShotMatched]) {
      if (++gShotMatched == sizeof(kShotRequest) - 1) { gShotWanted = true; gShotMatched = 0; }
    } else {
      gShotMatched = (c == kShotRequest[0]) ? 1 : 0;
    }
    if (c == kResetRequest[gRequestMatched]) {
      if (++gRequestMatched == sizeof(kResetRequest) - 1) { return true; }
    } else {
      gRequestMatched = (c == kResetRequest[0]) ? 1 : 0;
    }
  }
  return false;
}
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
  // EN with EXRST_EN and AUTO_START as resetNow (and uefi-a04br3 A04BR3TestCycleLib); no IRQ, no dual mode.
  const uint32_t mode = (rd(gToprgu, kWdtMode) & 0x00ffffffU & ~0x48U) | 0x22000015U;
  wr(gToprgu, kWdtMode, mode);
}

// BEGIN panic-aware watchdog (actual-function host fixture).
static unsigned gPanicLatched;
using HaltRestart = int (*)(unsigned);
static HaltRestart gPreviousHaltRestart;

bool panicObserved(void)
{
  if (__atomic_load_n(&gPanicLatched, __ATOMIC_ACQUIRE)) return true;
  if (!panic_active()) return false;
  __atomic_store_n(&gPanicLatched, 1u, __ATOMIC_RELEASE);
  return true;
}

void watchdogKick(void)
{
  if (!panicObserved() && !__atomic_load_n(&a04br3_runtime_probe_active, __ATOMIC_ACQUIRE)) wr(gToprgu, kWdtRestart, 0x1971);
}

void resetNow(void)
{
  wr(gToprgu, kWdtRestart, 0x1971);
  // Keep the armed WDT as a fallback if a panic's software reset fails.
  // This is one explicit reset request, not a resumption of periodic feeding.
  const uint32_t fallback = panicObserved() ? 1U : 0U;
  wr(gToprgu, kWdtMode, (rd(gToprgu, kWdtMode) & 0x00ffffa6U) | 0x22000014U | fallback);
  IODelay(100);
  wr(gToprgu, kWdtSwrst, 0x1209);
  for (;;) { __asm__ volatile("wfi"); }
}

int boardHaltRestart(unsigned type)
{
  // Existing XNU notifications, synchronously before the ordinary panic text.
  // This branch does no MMIO, allocation, locking, logging or sleeping. Latch
  // before forwarding: even a stuck previous handler cannot keep us feeding.
  if (type == kPEPanicBegin || type == kPEPanicEnd ||
      type == kPEPanicDiagnosticsDone || type == kPEPanicDiagnosticsInProgress ||
      type == kPEPanicRestartCPU || type == kPEPanicRestartCPUNoCallouts)
    __atomic_store_n(&gPanicLatched, 1u, __ATOMIC_RELEASE);
  if (type == kPERestartCPU || type == kPEPanicRestartCPU || type == kPEPanicRestartCPUNoCallouts) {
    if (gToprgu) resetNow();
  }
  HaltRestart previous = __atomic_load_n(&gPreviousHaltRestart, __ATOMIC_ACQUIRE);
  return previous ? previous(type) : -1;
}

void installHaltRestart(void)
{
  // The board hook runs once; this resident archive is never unloaded. Keep
  // unrelated platform operations with the handler already registered by XNU.
  HaltRestart previous = __atomic_load_n(&PE_halt_restart, __ATOMIC_ACQUIRE);
  if (previous == boardHaltRestart) return;
  __atomic_store_n(&gPreviousHaltRestart, previous, __ATOMIC_RELEASE);
  __atomic_store_n(&PE_halt_restart, &boardHaltRestart, __ATOMIC_RELEASE);
}
// END panic-aware watchdog

// Screenshot over UART (bring-up): after "A04BR3!SHOT" the frame buffer goes out on UART0 as text lines, one per row:
// "A04BR3SHOT <row> <crc32 of the row number and the row's raw bytes, hex> <base64 of the run-length coded row>", framed by "A04BR3SHOT BEGIN
// 800 1280 3200" and "A04BR3SHOT END". The run-length code is a sequence of (count 1..256 as one byte minus 1, 4-byte pixel).
// Kernel log lines may interleave with the rows (other CPUs print while this thread writes); the host
// (scripts/a04br3-screenshot.py) keeps rows whose CRC matches and asks again for missing ones.
bool gUartStuck;
// false once the transmitter has not drained for 100000 polls; the screenshot then stops (the watchdog keeps running).
bool uartPut(char c)
{
  if (gUartStuck) return false;
  unsigned spin = 0;
  while (!(rd(gUart, kUartLsr) & 0x20)) {
    if (++spin > 100000) { gUartStuck = true; return false; }
  }
  wr(gUart, kUartRbr, uint8_t(c));
  return true;
}
void uartPuts(const char *text) { while (*text) uartPut(*text++); }
// CRC-32 (zlib) of the row number (4 bytes, little endian) followed by the row's pixels.
uint32_t shotCrc(uint32_t y, const uint8_t *data, unsigned length)
{
  uint32_t crc = 0xffffffffU;
  for (unsigned i = 0; i < 4; i++) {
    crc ^= (y >> (8 * i)) & 0xff;
    for (unsigned b = 0; b < 8; b++) crc = (crc >> 1) ^ (0xedb88320U & (0U - (crc & 1)));
  }
  for (unsigned i = 0; i < length; i++) {
    crc ^= data[i];
    for (unsigned b = 0; b < 8; b++) crc = (crc >> 1) ^ (0xedb88320U & (0U - (crc & 1)));
  }
  return ~crc;
}
void sendShot(void)
{
  static uint32_t pixels[kFbWidth];
  static uint8_t coded[kFbWidth * 5];
  const uint8_t *row = reinterpret_cast<const uint8_t *>(pixels);
  gUartStuck = false;
  static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  char line[64];
  snprintf(line, sizeof(line), "\r\nA04BR3SHOT BEGIN %u %u %u\r\n", kFbWidth, kFbHeight, kFbStride);
  uartPuts(line);
  for (unsigned y = 0; y < kFbHeight; y++) {
    for (unsigned x = 0; x < kFbWidth; x++) {
      pixels[x] = *reinterpret_cast<volatile uint32_t *>(gFb + uint64_t(y) * kFbStride + x * 4);
    }
    unsigned n = 0;
    for (unsigned x = 0; x < kFbWidth;) {
      const uint32_t pixel = pixels[x];
      unsigned run = 1;
      while (x + run < kFbWidth && run < 256 && pixels[x + run] == pixel) run++;
      coded[n++] = uint8_t(run - 1);
      memcpy(coded + n, &pixel, 4);
      n += 4;
      x += run;
    }
    snprintf(line, sizeof(line), "\r\nA04BR3SHOT %u %08x ", y, shotCrc(y, row, kFbWidth * 4));
    uartPuts(line);
    for (unsigned i = 0; i < n; i += 3) {
      const uint32_t v = (uint32_t(coded[i]) << 16) | (i + 1 < n ? uint32_t(coded[i + 1]) << 8 : 0U) |
                         (i + 2 < n ? uint32_t(coded[i + 2]) : 0U);
      uartPut(alphabet[(v >> 18) & 63]);
      uartPut(alphabet[(v >> 12) & 63]);
      uartPut(i + 1 < n ? alphabet[(v >> 6) & 63] : '=');
      uartPut(i + 2 < n ? alphabet[v & 63] : '=');
    }
    uartPuts("\r\n");
    if ((y & 15) == 15) { watchdogKick(); }
    if (gUartStuck) { IOLog("a04br3-autoreset: UART transmitter stuck, screenshot stopped at row %u\n", y); return; }
  }
  uartPuts("A04BR3SHOT END\r\n");
}

void cycleThread(void *, wait_result_t)
{
  const uint64_t started = mach_absolute_time();
  uint64_t limit = 0;
  nanoseconds_to_absolutetime(static_cast<uint64_t>(gResetSeconds) * NSEC_PER_SEC, &limit);
  for (unsigned tick = 0;; tick++) {
    if (tick % 10 == 0) { watchdogKick(); }
    if (mach_absolute_time() - started >= limit) {
      IOLog("a04br3-autoreset: %u s elapsed, resetting into fastboot\n", gResetSeconds);
      IOSleep(200);  // let the console drain
      resetNow();
    }
    const bool reset = resetRequested();
    if (gShotWanted && gFb) { gShotWanted = false; sendShot(); }
    if (reset) {
      IOLog("a04br3-autoreset: reset requested on UART, resetting into fastboot\n");
      IOSleep(200);
      resetNow();
    }
    IOSleep(100);
  }
}

}  // namespace

// Fresh watchdog mode read after the PMU worker has bound to its target CPU.
// This does not re-read the RTC fastboot bit or prevent another agent from
// changing either register after this observation.
extern "C" bool a04br3_runtime_watchdog_armed(void)
{
  if (!gToprgu || !__atomic_load_n(&a04br3_runtime_recovery_ready, __ATOMIC_ACQUIRE)) return false;
  return (rd(gToprgu, kWdtMode) & 0x5dU) == 0x15U;
}

extern "C" void a04br3_autoreset_init(void)
{
  unsigned seconds = 0;
  if (!PE_parse_boot_argn("a04br3-autoreset", &seconds, sizeof(seconds)) || seconds == 0) { return; }
  gResetSeconds = seconds;
  gPwrap = reinterpret_cast<volatile uint8_t *>(ml_io_map(kPwrap, 0x1000));
  gToprgu = reinterpret_cast<volatile uint8_t *>(ml_io_map(kToprgu, 0x1000));
  gUart = reinterpret_cast<volatile uint8_t *>(ml_io_map(kUart0, 0x1000));
  gFb = reinterpret_cast<volatile uint8_t *>(ml_io_map(kFrameBuffer, kFbStride * kFbHeight));
  if (gFb == nullptr) { IOLog("a04br3-autoreset: frame buffer mapping failed; no screenshot\n"); }
  if (gUart == nullptr) { IOLog("a04br3-autoreset: UART0 mapping failed; no reset on request\n"); }
  if (gPwrap == nullptr || gToprgu == nullptr) {
    IOLog("a04br3-autoreset: register mapping failed; cycle disabled\n");
    return;
  }
  const bool flag = setFastbootFlag();
  IOLog("a04br3-autoreset: fastboot flag %s, reset after %u s, watchdog %u s\n", flag ? "set" : "NOT set", seconds,
        kWatchdogSeconds);
  watchdogArm(kWatchdogSeconds);
  if (flag && (rd(gToprgu, kWdtMode) & 1U))
    __atomic_store_n(&a04br3_runtime_recovery_ready, 1U, __ATOMIC_RELEASE);
  installHaltRestart(); // opt-in only, after TOPRGU/RTC setup; existing XNU panic notifications
  thread_t thread = nullptr;
  if (kernel_thread_start(cycleThread, nullptr, &thread) == KERN_SUCCESS) {
    thread_deallocate(thread);
  } else {
    IOLog("a04br3-autoreset: thread start failed; the watchdog still resets\n");
  }
}
