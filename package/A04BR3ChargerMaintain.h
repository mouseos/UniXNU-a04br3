// SPDX-License-Identifier: MIT
#pragma once
#ifndef A04CHARGER_UEFI_TYPES
#include <stdint.h>
#endif
#include "A04BR3ChargerCore.h"
// Opt-in charge maintenance (boot-arg a04br3-charger-maintain=1), modelled on the
// stock CT3 BSP: drivers/misc/mediatek/power/bq24160.c bq24160_charge_init() and
// drivers/power/mediatek/sts_battery_common.c (10 s bat_thread, TMR_RST while a
// charger exists). Stock values are the CEILING: HZ=0, CE=0 (enabled), TE=1,
// EN_STAT=1, IN limit 1.5 A, IUSB <=500 mA, ICHRG 1.3 A, ITERM 100 mA,
// VBREG 4.32 V, safety timer 6 h, TS enabled. They equal the LK/stock-Linux dump
// 30 00 8c a4 45 51 00 38. Nothing here may exceed them.
namespace a04charger {
namespace maintain {
constexpr uint8_t StockVbreg = 0x29;   // 3.5 V + 41 * 20 mV = 4.32 V
constexpr uint8_t StockIchrg = 0x0a;   // 550 + 10 * 75 mA = 1300 mA
constexpr uint8_t StockIterm = 0x01;   // 100 mA
constexpr uint8_t StockIusbMax = 0x02; // 500 mA (stock only when a host is connected)
constexpr uint8_t Reg3 = uint8_t(StockVbreg << 2);          // IINLIMIT=0 (1.5 A), DDET_EN=0
constexpr uint8_t Reg5 = uint8_t((StockIchrg << 3) | StockIterm);
static_assert(Reg3 == 0xa4 && Reg5 == 0x51, "stock register image");
static_assert(StockVbreg <= 0x29 && StockIchrg <= 0x0a && StockIusbMax <= 0x02, "never above stock");

enum Fault { FaultNone = 0, FaultThermal = 1, FaultBatteryTemp = 2, FaultWatchdog = 3,
             FaultSafetyTimer = 4, FaultIn = 5, FaultUsb = 6, FaultBattery = 7 };

// Register image the stock driver leaves behind, derived from the current sample.
// Fields the stock driver never sets (SUPPLY_SEL, OTG_LOCK, EN_NOBATOP, VINDPM,
// reg07 bit4, read-only TS_FAULT) are preserved, never invented.
inline uint8_t target2(uint8_t raw2) {
  uint8_t iusb = uint8_t((raw2 >> 4) & 7);
  if (iusb > StockIusbMax) iusb = StockIusbMax;
  return uint8_t((iusb << 4) | 0x0c); // RESET=0, EN_STAT=1, TE=1, CE=0, HZ=0
}
inline uint8_t target7(uint8_t raw7) { return uint8_t((raw7 & 0x16) | 0x28); } // 2XTMR=0, TMR=6h, TS_EN=1, LOW_CHG=0

struct Write { uint8_t reg, value; };
// Returns the number of configuration writes needed (0..4). Writes are only
// planned for a validated TI family ID and when the chip reports no fault other
// than an expired host watchdog; thermal, TS, safety-timer, supply and battery
// faults are left to the charger's own protection (logged, kicked, not overridden).
inline unsigned plan(const Snapshot &s, Write out[4]) {
  if (s.valid != 0xff || (s.raw[4] & 0xf8) != 0x40) return 0;
  unsigned fault = s.raw[0] & 7;
  if (fault != FaultNone && fault != FaultWatchdog) return 0;
  unsigned n = 0;
  if ((s.raw[3] & 0xfe) != Reg3) out[n++] = {3, Reg3};
  if (s.raw[5] != Reg5) out[n++] = {5, Reg5};
  if ((s.raw[7] & 0xe9) != 0x28) out[n++] = {7, target7(s.raw[7])};
  if ((s.raw[2] & 0x7f) != target2(s.raw[2])) out[n++] = {2, target2(s.raw[2])};
  return n;
}
// Whitelisted charger data writes for this mode: TMR_RST, or exactly the stock image.
inline bool allowedWrite(uint8_t reg, uint8_t value) {
  switch (reg) {
  case 0: return (value & ~0x88) == 0 && (value & 0x80);
  case 2: return (value & 0x8f) == 0x0c && ((value >> 4) & 7) <= StockIusbMax;
  case 3: return value == Reg3;
  case 5: return value == Reg5;
  case 7: return (value & 0xe9) == 0x28;
  default: return false;
  }
}

// MT8167 I2C PIO, one message: write txLen bytes, optionally repeated START and
// read rxLen bytes (<= 8-byte FIFO). Same controller sequence as transfer().
// fifo receives the raw FIFO_STAT for diagnostics.
template<class IO> bool xfer(IO &io, uint8_t addr, const uint8_t *tx, unsigned txLen,
                             uint8_t *rx, unsigned rxLen, unsigned *fifo) {
  if (!txLen || txLen > 8 || rxLen > 8 || addr > 0x7f) return false;
  if (io.rd(0x24) & 1) return false;
  bool read = rxLen != 0;
  io.wr(0x08, 0); io.wr(0x0c, 7); io.wr(0x38, 1);
  io.wr(0x10, uint16_t((io.rd(0x10) & ~0x3eU) | 0x28U | (read ? 0x12U : 0)));
  io.wr(0x04, uint16_t(addr << 1));
  io.wr(0x14, uint16_t(txLen)); io.wr(0x6c, uint16_t(rxLen)); io.wr(0x18, read ? 2 : 1);
  for (unsigned i = 0; i < txLen; ++i) io.wr(0, tx[i]);
  io.wr(0x24, read ? 1 : 0x4001);
  uint64_t start = io.now();
  for (unsigned poll = 0; poll < 2000; ++poll) {
    uint16_t status = io.rd(0x0c);
    if (status & 6) { io.wr(0x0c, 7); return false; }
    if (status & 1) {
      if (fifo) *fifo = io.rd(0x30);
      for (unsigned i = 0; i < rxLen; ++i) rx[i] = uint8_t(io.rd(0));
      io.wr(0x0c, 7); return true;
    }
    uint64_t now = io.now();
    if (now < start || now - start >= 10000000ULL) return false;
    io.delay();
  }
  return false;
}
template<class IO> bool chargerRead(IO &io, uint8_t reg, uint8_t *value) {
  unsigned fifo = 0; uint8_t v = 0;
  if (reg > 7 || !xfer(io, 0x6b, &reg, 1, &v, 1, &fifo) || ((fifo >> 4) & 15) != 1) return false;
  *value = v; return true;
}
template<class IO> bool chargerWrite(IO &io, uint8_t reg, uint8_t value) {
  if (!allowedWrite(reg, value)) return false;
  uint8_t tx[2] = {reg, value};
  return xfer(io, 0x6b, tx, 2, nullptr, 0, nullptr);
}

// LC709203F (stock lc709203f.c): SMBus word read with CRC-8 (x^8+x^2+x+1) over
// 0x16, reg, 0x17, lo, hi. Read-only; this mode never writes the fuel gauge.
inline uint8_t crc8(uint8_t crc, uint8_t byte) {
  crc ^= byte;
  for (unsigned i = 0; i < 8; ++i) crc = uint8_t((crc & 0x80) ? (crc << 1) ^ 0x07 : crc << 1);
  return crc;
}
template<class IO> bool gaugeRead(IO &io, uint8_t reg, uint16_t *value) {
  uint8_t rx[3] = {}; unsigned fifo = 0;
  if (!xfer(io, 0x0b, &reg, 1, rx, 3, &fifo)) return false;
  uint8_t c = crc8(crc8(crc8(crc8(crc8(0, 0x16), reg), 0x17), rx[0]), rx[1]);
  if (c != rx[2]) return false;
  *value = uint16_t(rx[0] | (rx[1] << 8)); return true;
}

struct State {
  Snapshot sampled, latest; // sampled: before any repair; latest: after verification
  unsigned cycles = 0, kicks = 0, repairs = 0, readErrors = 0, writeErrors = 0, faultHistory = 0;
  uint64_t lastKick = 0, worstGap = 0;
};
enum StepResult { Kept, Repaired, NotCharger, ReadError, WriteError };
// One 10 s cycle: full snapshot, TMR_RST, then stock-image repair if needed and a
// verifying re-read. Failures are counted and retried next cycle; never latched.
template<class IO> StepResult step(State &st, IO &io, Write *done, unsigned *doneCount) {
  *doneCount = 0; ++st.cycles;
  Snapshot s; s.at = io.now();
  for (unsigned reg = 0; reg < 8; ++reg) {
    if (!chargerRead(io, uint8_t(reg), &s.raw[reg])) { ++st.readErrors; st.latest = s; return ReadError; }
    s.valid |= 1U << reg;
  }
  st.faultHistory |= 1U << (s.raw[0] & 7);
  st.sampled = s; st.latest = s;
  if ((s.raw[4] & 0xf8) != 0x40) return NotCharger;
  if (!chargerWrite(io, 0, uint8_t(0x80 | (s.raw[0] & 8)))) { ++st.writeErrors; return WriteError; }
  uint64_t now = io.now();
  if (st.kicks && now > st.lastKick && now - st.lastKick > st.worstGap) st.worstGap = now - st.lastKick;
  st.lastKick = now; ++st.kicks;
  Write w[4]; unsigned n = plan(s, w);
  if (!n) return Kept;
  for (unsigned i = 0; i < n; ++i) {
    if (!chargerWrite(io, w[i].reg, w[i].value)) { ++st.writeErrors; return WriteError; }
    done[(*doneCount)++] = w[i];
  }
  ++st.repairs;
  for (unsigned reg = 0; reg < 8; ++reg)
    if (!chargerRead(io, uint8_t(reg), &st.latest.raw[reg])) { ++st.readErrors; return ReadError; }
  return Repaired;
}
}
}

