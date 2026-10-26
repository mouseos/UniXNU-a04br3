// SPDX-License-Identifier: MIT
#pragma once
#ifndef A04CHARGER_UEFI_TYPES
#include <stdint.h>
#endif
// Board policy facts: stock a04br3 DTS/GPL charger initialization and the observed
// LK 30 00 8c a4 45 51 00 38 dump. Register semantics: TI SLUSAO0H, 8.3.9/8.6.
// This owner preserves a matching inherited policy; it never installs that policy.
namespace a04charger {
struct Snapshot {
  uint8_t raw[8] = {};
  unsigned valid = 0;
  uint64_t at = 0;
};
struct Owner {
  Snapshot first, latest;
  bool attempted = false, stopped = false;
  unsigned faultHistory = 0, kicks = 0;
  uint64_t lastKick = 0;
  bool handoffPending = false;
  uint64_t handoffDeadline = 0;
};
enum Result { Maintained, Deadline, ReadFailed, PolicyMismatch, Fault, KickFailed, Stopped };
inline Result policy(const Snapshot &s) {
  if (s.valid != 0xff) return ReadFailed;
  // ID establishes TI family/revision only, not BQ24160 vs BQ24160A.
  // The board's declared BQ24160 is a source prerequisite, not inferred from ID.
  if ((s.raw[4] & 0xf8) != 0x40) return PolicyMismatch;
  if ((s.raw[0] & 7) || (s.raw[7] & 6)) return Fault;
  if ((s.raw[0] & 8) || (s.raw[2] & 0x7f) != 0x0c || (s.raw[3] & 0xfe) != 0xa4 ||
      s.raw[5] != 0x51 || (s.raw[7] & 0xe9) != 0x28 || (s.raw[6] & 0x3f) || (s.raw[1] & 15)) return PolicyMismatch;
  unsigned status = (s.raw[0] >> 4) & 7;
  if (status == 3 && (s.raw[1] & 0xc0) == 0) return Maintained; // IN, not IUSB_LIMIT
  if (status == 4 && (s.raw[1] & 0x30) == 0) return Maintained;
  if (status == 5 && ((s.raw[1] & 0xc0) == 0 || (s.raw[1] & 0x30) == 0)) return Maintained;
  return PolicyMismatch;
}
// Ops: now() ns; read(reg,&byte) success only for a complete transaction;
// kick(value): write 00 with TMR_RST and observed SUPPLY_SEL only. No setters.
// First read starts/affects the charger watchdog, and 00 clears historical FAULT
// when no longer present. attempted stays true even after a partial/NACK read.
// A stopped owner never silently resumes or pretends its previous policy restored.
template<class Ops> Result step(Owner &owner, Ops &ops) {
  if (owner.stopped) return Stopped;
  uint64_t now = ops.now();
  if (owner.handoffPending && now >= owner.handoffDeadline) { owner.stopped=true; return Deadline; }
  if (owner.kicks && (now < owner.lastKick || now - owner.lastKick >= 20000000000ULL)) {
    owner.stopped = true; return Deadline; // leave hardware's documented fallback; no stale kicks
  }
  Snapshot sample; sample.at = now;
  bool first = !owner.attempted;
  owner.attempted = true;
  for (unsigned reg = 0; reg < 8; ++reg) {
    if (!ops.read(reg, &sample.raw[reg])) {
      owner.latest = sample;
      if (first) owner.first = sample;
      owner.stopped = true; return ReadFailed;
    }
    sample.valid |= 1U << reg;
    if (!reg) owner.faultHistory |= 1U << (sample.raw[0] & 7);
  }
  owner.latest = sample;
  if (first) owner.first = sample;
  Result result = policy(sample);
  // Bound the whole capture, not just individual controller polls.
  uint64_t end = ops.now();
  if ((owner.handoffPending && end >= owner.handoffDeadline) || end < now || end - now >= 1000000000ULL) result = Deadline;
  if (result != Maintained) { owner.stopped = true; return result; }
  // Never echo dynamic STAT/FAULT/TMR_RST bits or write RESET/CE/HZ/limits.
  if (!ops.kick(uint8_t(0x80 | (sample.raw[0] & 8)))) {
    owner.stopped = true; return KickFailed;
  }
  owner.lastKick = ops.now(); ++owner.kicks; owner.handoffPending=false;
  return Maintained;
}
// Actual PIO transaction shared by the board implementation and host MMIO tests.
// Preserve inherited clock/timing/pins, disable DMA only in this owned controller.
// Read: one-byte register pointer followed by repeated START + one byte receive.
// Write: exactly register 00 + the caller's validated watchdog-only value.
// No bus scan, soft reset, DMA, pinmux, clock, PMIC, or retry loop.
template<class IO> bool transfer(IO &io, bool read, uint8_t reg, uint8_t *value) {
  if (reg > 7 || (!read && (reg != 0 || ((*value & ~0x88) || !(*value & 0x80))))) return false;
  if (io.rd(0x24) & 1) return false;
  io.wr(0x08, 0); io.wr(0x0c, 7); io.wr(0x38, 1);
  io.wr(0x10, uint16_t((io.rd(0x10) & ~0x3eU) | 0x28U | (read ? 0x12U : 0)));
  io.wr(0x04, 0xd6); // 7-bit 0x6b; read direction is handled by DIR_CHANGE
  io.wr(0x14, read ? 1 : 2); io.wr(0x6c, read ? 1 : 0); io.wr(0x18, read ? 2 : 1);
  io.wr(0, reg); if (!read) io.wr(0, *value);
  io.wr(0x24, read ? 1 : 0x4001); // MT8167 single-message auto-restart trigger
  uint64_t start = io.now();
  for (unsigned poll = 0; poll < 2000; ++poll) {
    uint16_t status = io.rd(0x0c);
    if (status & 6) { io.wr(0x0c, 7); return false; }
    if (status & 1) {
      if (read) {
        if (((io.rd(0x30) >> 4) & 15) != 1) { io.wr(0x0c, 7); return false; }
        *value = uint8_t(io.rd(0));
      }
      io.wr(0x0c, 7); return true;
    }
    uint64_t now = io.now();
    if (now < start || now - start >= 10000000ULL) return false;
    io.delay();
  }
  return false;
}
}
