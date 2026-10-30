// SPDX-License-Identifier: BSD-2-Clause-Patent
// a04br3 GPU bring-up, step 1 (boot argument a04br3-gpu=1 or 2, default off; 2 also boots the firmware, NativePowerVR.cpp): power the MT8167 MFG domains and identify the
// PowerVR Rogue core. Ported from github.com/mouseos/xnu_kernel_benesse_a04br3 iokit/Kernel/arm/rogue (A04BR3RogueGPU.cpp,
// A04BR3RoguePower.h: register facts from the board Linux mtk-scpsys-mt8167, mtk-infracfg and clk-mt8167), where the same
// sequence ran on this tablet and read BVNC 22.40.54.30 (GE8300). The core stays at crystal speed (26 MHz); no firmware
// is loaded and nothing is submitted yet.
//  SPM 0x10006000, INFRACFG 0x10001000, TOPCKGEN 0x10000000, MFGCFG 0x13FFE000, RGX core 0x13000000 (CORE_ID at 0x20/0x24,
//  CORE_REVISION at 0x18).
#include <IOKit/IOLib.h>
#include "A04BR3RegisterLock.h"
#include <pexpert/pexpert.h>
#include <stdint.h>
#include "A04BR3RoguePower.h"

extern "C" vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);

extern "C" {
void a04br3_gpu_init(void);
void native_powervr_boot(void);   // package/powervr/NativePowerVR.cpp (a04br3-gpu=2)
volatile uint64_t a04br3_gpu_bvnc;
volatile uint32_t a04br3_gpu_core_hz = 26000000;
volatile int a04br3_gpu_power_error = -1;
}

namespace {
volatile uint8_t *gBank[4];
uint32_t bankRead(void *, unsigned bank, unsigned offset)
{
  const uint32_t v = *reinterpret_cast<volatile uint32_t *>(gBank[bank] + offset);
  __asm__ volatile("dmb oshld" ::: "memory");
  return v;
}
void bankWrite(void *, unsigned bank, unsigned offset, uint32_t value)
{
  *reinterpret_cast<volatile uint32_t *>(gBank[bank] + offset) = value;
  __asm__ volatile("dsb oshst" ::: "memory");
}
void waitUs(void *, unsigned us) { IODelay(us); }

// Startup-only clock/rail snapshot. Board.cpp calls GPU init after autoreset's
// startup WACS transactions and before Input starts its touch power sequence.
// This relies on no host reset request during startup; it is not a shared WACS
// lock and must never be used as a runtime API or after the input thread starts.
// Register sources: board Linux clk-mt8167.c, mt8167.dtsi, mtk-pmic-wrap.c
// (mt8167_regs/pwrap_read), and mt6392-regulator.c plus mt6392/registers.h.
uint32_t clockDiagnosticRead(volatile uint8_t *base, unsigned offset)
{
  const uint32_t value = *reinterpret_cast<volatile uint32_t *>(base + offset);
  __asm__ volatile("dmb oshld" ::: "memory");
  return value;
}
void clockDiagnosticWrite(volatile uint8_t *base, unsigned offset, uint32_t value)
{
  *reinterpret_cast<volatile uint32_t *>(base + offset) = value;
  __asm__ volatile("dsb oshst" ::: "memory");
}
bool clockDiagnosticWait(volatile uint8_t *pwrap, unsigned fsm)
{
  // Like Input's pwWait: at most 1000 polls / 10 ms per FSM phase.
  for (unsigned n = 0; n < 1000; n++) {
    const uint32_t status = clockDiagnosticRead(pwrap, 0xa4);
    if (!(status & (1U << 21))) return false;   // WACS2 INIT_DONE
    if (((status >> 16) & 7) == fsm) return true;
    IODelay(10);
  }
  return false;
}
const char *clockDiagnosticPmicRead(volatile uint8_t *pwrap, unsigned address, uint16_t *value)
{
  A04BR3PwrapGuard guard(10000);
  if (!guard) return "WACS2 busy";
  if ((address & 1) || address > 0xfffe) return "invalid PMIC address";
  if (!clockDiagnosticWait(pwrap, 0)) { guard.quarantine(); return "idle timeout or INIT_DONE lost"; }
  // Only a read command: bit 31 is always zero. No PMIC register writes,
  // wrapper initialization, or changes to clock/power/voltage configuration.
  clockDiagnosticWrite(pwrap, 0xa0, (address >> 1) << 16);
  if (!clockDiagnosticWait(pwrap, 6)) { guard.quarantine(); return "response timeout or INIT_DONE lost"; }
  const uint16_t data = uint16_t(clockDiagnosticRead(pwrap, 0xa4));
  clockDiagnosticWrite(pwrap, 0xa8, 1);   // acknowledge this read's valid data
  if (!clockDiagnosticWait(pwrap, 0)) { guard.quarantine(); return "completion timeout or INIT_DONE lost"; }
  *value = data;
  return nullptr;
}
void clockDiagnosticSnapshot(volatile uint8_t *top)
{
  unsigned enabled = 0;
  if (!PE_parse_boot_argn("a04br3-gpu-clock-diagnostic", &enabled, sizeof(enabled)) || enabled != 1) return;

  const uint32_t sel0 = clockDiagnosticRead(top, 0x000), sel1 = clockDiagnosticRead(top, 0x004);
  IOLog("a04br3 gpu clock diagnostic: startup TOP SEL0 %08x SEL1 %08x (before power/FW)\n", sel0, sel1);
  volatile uint8_t *apmixed = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10018000, 0x1000));
  if (apmixed) {
    const unsigned offsets[] = {0x120, 0x140, 0x160};
    const char *const names[] = {"MAINPLL", "UNIVPLL", "MMPLL"};
    for (unsigned i = 0; i < 3; i++) {
      const uint32_t con0 = clockDiagnosticRead(apmixed, offsets[i]);
      const uint32_t con1 = clockDiagnosticRead(apmixed, offsets[i] + 4);
      const uint32_t power = clockDiagnosticRead(apmixed, offsets[i] + 0x10);
      IOLog("a04br3 gpu clock diagnostic: %s CON0 %08x CON1 %08x PWR %08x\n", names[i], con0, con1, power);
    }
  } else {
    IOLog("a04br3 gpu clock diagnostic: MAINPLL/UNIVPLL/MMPLL unknown (mapping failed)\n");
  }

  volatile uint8_t *pwrap = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x1000f000, 0x1000));
  if (!pwrap) {
    IOLog("a04br3 gpu clock diagnostic: PWRAP/VCORE unknown (mapping failed)\n");
    return;
  }
  const uint32_t wacs_enable = clockDiagnosticRead(pwrap, 0x98);
  const uint32_t init = clockDiagnosticRead(pwrap, 0x9c);
  const uint32_t status = clockDiagnosticRead(pwrap, 0xa4);
  IOLog("a04br3 gpu clock diagnostic: WACS2 EN %08x INIT %08x RDATA %08x (unlocked, non-atomic)\n", wacs_enable, init, status);
  if (!(wacs_enable & 1) || !(init & 1) || !(status & (1U << 21))) {
    IOLog("a04br3 gpu clock diagnostic: VCORE unknown (WACS2 not initialized/enabled)\n");
    return;
  }
  const unsigned addresses[] = {0x030a, 0x030e, 0x0312, 0x0314};
  for (unsigned i = 0; i < 4; i++) {
    uint16_t value = 0;
    const char *error = clockDiagnosticPmicRead(pwrap, addresses[i], &value);
    if (error) {
      IOLog("a04br3 gpu clock diagnostic: VCORE %04x and remaining registers unknown (%s; unlocked RDATA %08x); continuing boot\n",
            addresses[i], error, clockDiagnosticRead(pwrap, 0xa4));
      return;
    }
    IOLog("a04br3 gpu clock diagnostic: VCORE %04x raw %04x\n", addresses[i], unsigned(value));
  }
}
unsigned selectClockParent(uint32_t *hz)
{
  unsigned requested = 0;
  if (!PE_parse_boot_argn("a04br3-gpu-clock", &requested, sizeof(requested)) || requested == 0) return 4;
  if (requested != 380) { IOLog("a04br3 gpu clock: unsupported request %u; retaining 26 MHz\n", requested); return 4; }
  auto *pll = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10018000, 0x1000));
  auto *pwrap = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x1000f000, 0x1000));
  if (!pll || !pwrap || !(clockDiagnosticRead(pwrap, 0x98) & 1) || !(clockDiagnosticRead(pwrap, 0x9c) & 1)) {
    IOLog("a04br3 gpu clock: PLL/VCORE state unavailable; retaining 26 MHz\n"); return 4;
  }
  const uint32_t con0 = clockDiagnosticRead(pll, 0x160), con1 = clockDiagnosticRead(pll, 0x164), power = clockDiagnosticRead(pll, 0x170);
  uint16_t rail[4] = {};
  const unsigned addresses[] = {0x030a, 0x030e, 0x0312, 0x0314};
  for (unsigned i = 0; i < 4; i++) {
    if (const char *error = clockDiagnosticPmicRead(pwrap, addresses[i], &rail[i])) {
      IOLog("a04br3 gpu clock: VCORE unknown (%s); retaining 26 MHz\n", error); return 4;
    }
  }
  const uint32_t selected = a04_rgx_mmpll_hz(con0, con1, power, rail[0], rail[1], rail[2], rail[3]);
  if (!selected || con0 != clockDiagnosticRead(pll, 0x160) || con1 != clockDiagnosticRead(pll, 0x164) || power != clockDiagnosticRead(pll, 0x170)) {
    IOLog("a04br3 gpu clock: inactive, unstable or unsupported PLL/VCORE state; retaining 26 MHz\n"); return 4;
  }
  *hz = selected;
  IOLog("a04br3 gpu clock: selecting existing MMPLL %u Hz, VCORE selector %02x status %04x; no PLL/voltage writes\n",
        selected, unsigned(((rail[0] & 2) ? rail[3] : rail[2]) & 0x7f), unsigned(rail[1]));
  return 7;
}
}  // namespace

void a04br3_gpu_init(void)
{
  unsigned enabled = 0;
  if (!PE_parse_boot_argn("a04br3-gpu", &enabled, sizeof(enabled)) || enabled == 0) return;
  gBank[A04_RGX_SPM] = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10006000, 0x1000));
  gBank[A04_RGX_INFRA] = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10001000, 0x1000));
  gBank[A04_RGX_TOP] = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10000000, 0x1000));
  gBank[A04_RGX_MFG] = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x13FFE000, 0x1000));
  if (!gBank[0] || !gBank[1] || !gBank[2] || !gBank[3]) { IOLog("a04br3 gpu: register mapping failed\n"); return; }
  clockDiagnosticSnapshot(gBank[A04_RGX_TOP]);
  const a04_rgx_io io = {nullptr, bankRead, bankWrite, waitUs};
  IOLog("a04br3 gpu: MFG status 0x%x/0x%x, powering the Rogue domains\n", bankRead(nullptr, A04_RGX_SPM, 0x60c),
        bankRead(nullptr, A04_RGX_SPM, 0x610));
  uint32_t coreHz = 26000000;
  const unsigned parent = selectClockParent(&coreHz);
  a04br3_gpu_power_error = parent == 4 ? a04_rgx_power_on(&io) : a04_rgx_power_on_parent(&io, parent);
  if (a04br3_gpu_power_error) { IOLog("a04br3 gpu: power stage %d failed; core not accessed\n", a04br3_gpu_power_error); return; }
  const unsigned actualParent = (bankRead(nullptr, A04_RGX_TOP, 0x000) >> 8) & 7;
  const unsigned actualInput = (bankRead(nullptr, A04_RGX_TOP, 0x004) >> 8) & 63;
  if (actualParent != parent || actualInput != 8) {
    IOLog("a04br3 gpu clock: mux readback mismatch parent %u/%u input %u; firmware not started\n", actualParent, parent, actualInput);
    return;
  }
  a04br3_gpu_core_hz = coreHz;
  IOLog("a04br3 gpu clock: active parent %u core %u Hz\n", actualParent, coreHz);
  volatile uint8_t *core = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x13000000, 0x10000));
  if (!core) { IOLog("a04br3 gpu: core mapping failed\n"); return; }
  const uint32_t lo = *reinterpret_cast<volatile uint32_t *>(core + 0x20), hi = *reinterpret_cast<volatile uint32_t *>(core + 0x24);
  const uint32_t revision = *reinterpret_cast<volatile uint32_t *>(core + 0x18);
  __asm__ volatile("dmb oshld" ::: "memory");
  a04br3_gpu_bvnc = (uint64_t(hi) << 32) | lo;
  IOLog("a04br3 gpu: BVNC %u.%u.%u.%u (raw 0x%llx) revision 0x%x\n", hi >> 16, hi & 0xffff, lo >> 16, lo & 0xffff,
        static_cast<unsigned long long>(a04br3_gpu_bvnc), revision);
  if (a04br3_gpu_bvnc && a04br3_gpu_bvnc != ~0ULL) native_powervr_boot();
}
