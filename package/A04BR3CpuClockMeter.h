// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>

// MT8167 board-only experiment. Register assignments and the 26 MHz / 520
// window come from MediaTek's clkdbg_mt8167.c (CT3 GPL source, commit
// 7c4782127d4ea11965be00ce1343056be9bfb769). This implementation never
// changes PLL hopping, PLL test selection, CPU mux or the debug selector.
// A local lock cannot exclude firmware or an external debug agent.
struct A04BR3CpuMeterResult {
  unsigned status; // 0 candidate, 1 busy, 2 precondition, 3 reset, 4 conflict, 5 zero, 6 restore, 7 changed
  uint32_t ctrl, debug, hopping, mux, dcm, pll0, pll1, power, baseline, count;
  uint64_t khz;
};

template<class IO> static A04BR3CpuMeterResult a04br3_cpu_meter(IO &io)
{
  A04BR3CpuMeterResult r = {};
  if (!io.lock()) { r.status = 1; return r; }
  struct Release { IO &io; ~Release() { io.unlock(); } } release{io};
  r.ctrl = io.top(0x10); r.debug = io.top(0x38); r.hopping = io.pll(0x14);
  r.mux = io.infra(0); r.dcm = io.infra(0x14); r.pll0 = io.pll(0x100);
  r.pll1 = io.pll(0x104); r.power = io.pll(0x110);
  // Reject configured/busy meters and any condition under which the debug
  // signal or CPU parent may not correspond to the unmodified ARMPLL.
  if (r.ctrl != 0 || (r.debug & 0xffU) != 0 || r.hopping != 0 ||
      ((r.mux >> 2) & 3U) != 1 || (r.dcm & 1U) || !(r.pll0 & 1U) ||
      !(r.power & 1U) || (r.power & 2U)) { r.status = 2; return r; }
  // Only write the meter control, and restore the known idle value 0. Refuse
  // unexpected readback; it may be another owner or unqualified hardware.
  io.write(0x10, 1U << 14);
  if (io.top(0x10) != (1U << 14)) { r.status = 3; return r; }
  io.write(0x10, 0);
  if (io.top(0x10) != 0) { r.status = 3; return r; }
  r.baseline = io.top(0x14);
  if (r.baseline != 0) { r.status = 3; return r; }
  const uint32_t configured = (25U << 16) | (1U << 15) | 519U;
  io.write(0x10, configured); io.delay(40);
  if (io.top(0x10) != configured) { r.status = 4; return r; }
  r.count = io.top(0x14);
  io.write(0x10, 1U << 14);
  if (io.top(0x10) != (1U << 14)) { r.status = 6; return r; }
  io.write(0x10, 0);
  if (io.top(0x10) != 0) { r.status = 6; return r; }
  if (io.top(0x38) != r.debug || io.pll(0x14) != r.hopping ||
      io.infra(0) != r.mux || io.infra(0x14) != r.dcm ||
      io.pll(0x100) != r.pll0 || io.pll(0x104) != r.pll1 ||
      io.pll(0x110) != r.power) { r.status = 7; return r; }
  if (!r.count) { r.status = 5; return r; }
  r.khz = (uint64_t(r.count) * 26000U) / 520U;
  return r;
}
