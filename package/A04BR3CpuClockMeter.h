// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>

// MT8167 board-only experiment. Register assignments and the 26 MHz / 520
// window come from MediaTek's clkdbg_mt8167.c (CT3 GPL source, commit
// 7c4782127d4ea11965be00ce1343056be9bfb769). This implementation never
// changes PLL hopping, PLL test selection, CPU mux or the debug selector.
// A local lock cannot exclude firmware or an external debug agent.
struct A04BR3CpuMeterResult {
  unsigned status; // 0 candidate, 1 busy, 2 precondition, 3 reset, 4 conflict, 5 zero, 6 restore, 7 changed, 8 unsettled, 9 route/recovery
  uint32_t ctrl, debug, hopping, plltest, mux, dcm, pll0, pll1, power, baseline, count;
  uint64_t khz;
  uint32_t second;
  unsigned valid, stage; // valid: baseline=1, count=2, second=4; stage: last operation

};

// A write occurred and its resulting control state is not established.
static inline bool a04br3_cpu_meter_requires_recovery(unsigned status)
{ return status == 3 || status == 4 || status == 6 || status == 9; }

template<class IO> static A04BR3CpuMeterResult a04br3_cpu_meter_id(IO &io, unsigned id)
{
  A04BR3CpuMeterResult r = {};
  if (!io.lock()) { r.status = 1; return r; }
  struct Release { IO &io; ~Release() { io.unlock(); } } release{io};
  r.ctrl = io.top(0x10); r.debug = io.top(0x38); r.hopping = io.pll(0x14); r.plltest = io.pll(0x64);
  r.mux = io.infra(0); r.dcm = io.infra(0x14); r.pll0 = io.pll(0x100);
  r.pll1 = io.pll(0x104); r.power = io.pll(0x110);
  // Reject configured/busy meters and any condition under which the debug
  // signal or CPU parent may not correspond to the unmodified ARMPLL.
  // MT8167 Linux maps ARMCA7 PLL to FHCTL bit 0; other bits are never written.
  // Require the vendor clkdbg test-route setting; no test or PLL register writes.
  // This condition is conservative: it does not establish the route or the
  // core clock, and other owners remain unexcluded.
  if (r.ctrl != 0 || (r.debug & 0xffU) != 0 || (r.hopping & 1U) != 0 || (r.plltest & 0x3ffU) != 0x287U ||
      ((r.mux >> 2) & 3U) != 1 || (r.dcm & 1U) || !(r.pll0 & 1U) ||
      !(r.power & 1U) || (r.power & 2U)) { r.status = 2; return r; }
  // Only write the meter control, and restore the known idle value 0. Refuse
  // unexpected readback; it may be another owner or unqualified hardware.
  r.stage = 1;
  io.write(0x10, 1U << 14);
  if (io.top(0x10) != (1U << 14)) { r.status = 3; return r; }
  io.write(0x10, 0);
  if (io.top(0x10) != 0) { r.status = 3; return r; }
  r.stage = 2; r.baseline = io.top(0x14); r.valid |= 1;
  if (r.baseline != 0) { r.status = 3; return r; }
  const uint32_t configured = (id << 16) | (1U << 15) | 519U;
  r.stage = 3; io.write(0x10, configured); io.delay(40);
  if (io.top(0x10) != configured) { r.status = 4; return r; }
  r.count = io.top(0x14); r.valid |= 2;
  // A nonzero latched value is not a completion flag. Require a second
  // identical reading after another interval, while acknowledging that even
  // a stable value does not prove the meter's undocumented completion state.
  io.delay(10);
  r.second = io.top(0x14); r.valid |= 4;
  if (io.top(0x10) != configured) { r.status = 4; return r; }
  r.stage = 4; io.write(0x10, 1U << 14);
  if (io.top(0x10) != (1U << 14)) { r.status = 6; return r; }
  io.write(0x10, 0);
  if (io.top(0x10) != 0) { r.status = 6; return r; }
  r.stage = 5;
  if (io.top(0x38) != r.debug || io.pll(0x14) != r.hopping || io.pll(0x64) != r.plltest ||
      io.infra(0) != r.mux || io.infra(0x14) != r.dcm ||
      io.pll(0x100) != r.pll0 || io.pll(0x104) != r.pll1 ||
      io.pll(0x110) != r.power) { r.status = 7; return r; }
  if (!r.count) { r.status = 5; return r; }
  if (r.count != r.second) { r.status = 8; return r; }
  r.stage = 6; r.khz = (uint64_t(r.count) * 26000U) / 520U;
  return r;
}

template<class IO> static A04BR3CpuMeterResult a04br3_cpu_meter(IO &io)
{ return a04br3_cpu_meter_id(io, 25); }
