// SPDX-License-Identifier: MIT
#pragma once
// MT8167 facts: clk-mt8167.c mainpll/ahb_infra_d2/ifr_i2c2_sel;
// i2c-mt65xx.c timing_adjust=1, divider 2, default 100 kHz;
// a04br3.dts SDA60/SCL61 ALT1 bias-disable. No charger policy setters.
namespace a04charger {
enum BoardResult { BoardReady, ClockUnknown, ClockOff, BusBusy, TimingUnsupported, BoardVerifyFailed };
// GPL mainpll: PCW21, INTEGER_BITS7 => fractional14, PD at bit24.
inline uint64_t mainRate(unsigned pcwreg) {
  uint64_t vco = (26000000ULL * (pcwreg & 0x1fffffU) + 16383) >> 14;
  unsigned pd = 1U << ((pcwreg >> 24) & 7);
  return (vco + pd - 1) / pd;
}
// IO: rd32/wr32 absolute addresses, rd/wr controller 16-bit offsets.
// Caller holds exclusive I2C2 ownership and excludes any other clock/pin writer.
template<class IO> BoardResult initialize(IO &io) {
  unsigned ahb = (io.rd32(0x10000000) >> 4) & 15;
  uint64_t parent;
  if (ahb == 1) parent = 13000000; // clk26m/2
  else {
    unsigned factor = ahb == 2 ? 11 : ahb == 4 ? 12 : ahb == 12 ? 10 : 0;
    if (!factor) return ClockUnknown; // clk_null, not an invented frequency
    unsigned con0 = io.rd32(0x10018120), pwr = io.rd32(0x10018130);
    if (!(con0 & 1) || (pwr & 3) != 1) return ClockOff;
    unsigned pcw = io.rd32(0x10018124);
    parent = (mainRate(pcw) / factor) / 2;
    if (!parent || pcw != io.rd32(0x10018124)) return ClockUnknown;
  }
  // Formula: parent/(clock_div * 2 * sample * step), clock_div=2.
  // Search integer divisors for fastest <=100kHz; no copied GPL implementation.
  unsigned sample = 0, step = 0, best = 513;
  for (unsigned a = 1; a <= 8; ++a) for (unsigned b = 1; b <= 64; ++b) {
    unsigned product = a * b;
    if (parent <= 400000ULL * product && product < best) { sample = a; step = b; best = product; }
  }
  if (!sample) return TimingUnsupported;
  // Dedicated W1C gate only: never APDMA, I2C0, rg_i2c or PWRAP gates.
  io.wr32(0x10000084, 1U << 16);
  if (io.rd32(0x10000024) & (1U << 16)) return BoardVerifyFailed;
  if (io.rd(0x24) & 1) return BusBusy; // never reset an in-flight transfer
  if (!io.updateMux(8, 0)) return BoardVerifyFailed; // I2C2 alone -> ahb_infra_d2 (stock DTS)
  // SET/CLR aliases preserve GPIO62..64 and every unrelated mode/pull field.
  io.wr32(0x100053c8, 0x3f); io.wr32(0x100053c4, 9); // pins60/61 ALT1
  io.wr32(0x10005914, 2); // IES group60/61 only (pinctrl-mt8167.c)
  io.wr32(0x10005538, 0x3000); // bias-disable60/61 only
  io.wr(0x08, 0); io.wr(0x0c, 7); io.wr(0x38, 1);
  io.wr(0x40, 3); io.wr(0x54, 0); io.wr(0x70, 1);
  io.wr(0x20, ((sample - 1) << 8) | (step - 1));
  io.wr(0x48, 0); io.wr(0x28, 0x8001); io.wr(0x60, 0); io.wr(0x10, 0x28); io.wr(0x1c, 2);
  if ((io.rd32(0x10001080) & 8) || (io.rd32(0x100053c0) & 0x3f) != 9 ||
      (io.rd32(0x10005530) & 0x3000) || io.rd(0x70) != 1 || io.rd(0x40) != 3 || io.rd(0x28) != 0x8001 ||
      io.rd(0x20) != (((sample - 1) << 8) | (step - 1))) return BoardVerifyFailed;
  return BoardReady;
}
}
