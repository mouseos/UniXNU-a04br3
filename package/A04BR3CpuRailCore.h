// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>

// Caller owns the shared WACS2 mutex. Each operation is bounded. A timed-out
// issued read can still complete later; quarantine the channel until reboot.
namespace a04br3rail {
enum Result { ok, unavailable, timeout_before_command, timeout_after_command, changed };
struct Snapshot { uint16_t control, selector; uint32_t nominal_uv; };
template <typename IO> bool wait(IO &io, unsigned fsm) {
  for (unsigned i=0; i<1000; ++i) {
    uint32_t s = io.read(0xa4);
    if (!(s & (1U << 21))) return false;
    if (((s >> 16) & 7U) == fsm) return true;
    io.delay();
  }
  return false;
}
template <typename IO> Result read16(IO &io, unsigned address, uint16_t &v) {
  if (!wait(io, 0)) return timeout_before_command;
  io.write(0xa0, (address >> 1) << 16); // PMIC read; never a PMIC write
  if (!wait(io, 6)) return timeout_after_command;
  uint16_t data = uint16_t(io.read(0xa4));
  io.write(0xa8, 1); // acknowledge our own response
  if (!wait(io, 0)) return timeout_after_command;
  v = data;
  return ok;
}
template <typename IO> Result capture(IO &io, Snapshot &out) {
  if (!(io.read(0x98) & 1U) || !(io.read(0x9c) & 1U)) return unavailable;
  uint16_t c1=0, c2=0, v1=0, v2=0;
  Result r = read16(io, 0x216, c1); if (r != ok) return r;
  const unsigned address = (c1 & 2U) ? 0x220 : 0x21e;
  r = read16(io, address, v1); if (r != ok) return r;
  r = read16(io, address, v2); if (r != ok) return r;
  r = read16(io, 0x216, c2); if (r != ok) return r;
  if (c1 != c2 || v1 != v2) return changed;
  out = {c1, v1, 700000U + (v1 & 0x7fU) * 6250U};
  return ok;
}
}
