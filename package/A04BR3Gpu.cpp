// SPDX-License-Identifier: BSD-2-Clause-Patent
// a04br3 GPU bring-up, step 1 (boot argument a04br3-gpu=1 or 2, default off; 2 also boots the firmware, NativePowerVR.cpp): power the MT8167 MFG domains and identify the
// PowerVR Rogue core. Ported from github.com/mouseos/xnu_kernel_benesse_a04br3 iokit/Kernel/arm/rogue (A04BR3RogueGPU.cpp,
// A04BR3RoguePower.h: register facts from the board Linux mtk-scpsys-mt8167, mtk-infracfg and clk-mt8167), where the same
// sequence ran on this tablet and read BVNC 22.40.54.30 (GE8300). The core stays at crystal speed (26 MHz); no firmware
// is loaded and nothing is submitted yet.
//  SPM 0x10006000, INFRACFG 0x10001000, TOPCKGEN 0x10000000, MFGCFG 0x13FFE000, RGX core 0x13000000 (CORE_ID at 0x20/0x24,
//  CORE_REVISION at 0x18).
#include <IOKit/IOLib.h>
#include <pexpert/pexpert.h>
#include <stdint.h>
#include "A04BR3RoguePower.h"

extern "C" vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);

extern "C" {
void a04br3_gpu_init(void);
void native_powervr_boot(void);   // package/powervr/NativePowerVR.cpp (a04br3-gpu=2)
volatile uint64_t a04br3_gpu_bvnc;
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
  const a04_rgx_io io = {nullptr, bankRead, bankWrite, waitUs};
  IOLog("a04br3 gpu: MFG status 0x%x/0x%x, powering the Rogue domains\n", bankRead(nullptr, A04_RGX_SPM, 0x60c),
        bankRead(nullptr, A04_RGX_SPM, 0x610));
  a04br3_gpu_power_error = a04_rgx_power_on(&io);
  if (a04br3_gpu_power_error) { IOLog("a04br3 gpu: power stage %d failed; core not accessed\n", a04br3_gpu_power_error); return; }
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
