// SPDX-License-Identifier: MIT
// Explicit board-only opt-in: a04br3-charger-owner=1. OFF touches no hardware.
// NOT passive/read-only: even reads start TI's 30 s WDT and can consume FAULT.
// Preserves verified LK policy only; no voltage/current/safety-timer setters.
// Separate opt-in a04br3-charger-maintain=1 (A04BR3ChargerMaintain.h) instead keeps
// the stock CT3 charge settings as a ceiling, kicks every 10 s, retries transient
// failures and logs LC709203F battery telemetry. Neither argument is a default.
#include <IOKit/IOLib.h>
#include <kern/thread.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <stdint.h>
#include "A04BR3RegisterLock.h"
#include "A04BR3ChargerCore.h"
#include "A04BR3ChargerHandoff.h"
#include "A04BR3ChargerBoardCore.h"
#include "A04BR3ChargerMaintain.h"
extern "C" vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
// XNU kern/thread.h declares this only in its private kernel context; this
// board archive links into that source kernel, using the same C prototype.
extern "C" void thread_terminate_self(void);
extern "C" kern_return_t kernel_thread_start_priority(thread_continue_t, void *, integer_t, thread_t *);
namespace {
volatile uint8_t *i2c, *gpio, *top, *infra, *apmixed;
IOLock *bus;
a04charger::Owner owner;
uint32_t rd32(volatile uint8_t *b, unsigned o) {
  uint32_t v = *reinterpret_cast<volatile uint32_t *>(b + o);
  __asm__ volatile("dmb oshld" ::: "memory"); return v;
}
unsigned mode(unsigned pin) { return (rd32(gpio, 0x300 + (pin / 5) * 16) >> ((pin % 5) * 3)) & 7; }
struct IO {
  bool updateMux(unsigned mask, unsigned bits) { return a04br3_infra_update(infra,mask,bits); }
  uint32_t rd32(unsigned a) { volatile uint8_t *b = a >= 0x10018000 ? apmixed : a >= 0x10005000 ? gpio : a >= 0x10001000 ? infra : top; unsigned base = a >= 0x10018000 ? 0x10018000 : a >= 0x10005000 ? 0x10005000 : a >= 0x10001000 ? 0x10001000 : 0x10000000; return ::rd32(b, a-base); }
  void wr32(unsigned a, unsigned v) { volatile uint8_t *b = a >= 0x10005000 ? gpio : a >= 0x10001000 ? infra : top; unsigned base = a >= 0x10005000 ? 0x10005000 : a >= 0x10001000 ? 0x10001000 : 0x10000000; *reinterpret_cast<volatile uint32_t *>(b+a-base)=v; __asm__ volatile("dsb oshst" ::: "memory"); }
  uint16_t rd(unsigned o) { uint16_t v = *reinterpret_cast<volatile uint16_t *>(i2c + o); __asm__ volatile("dmb oshld" ::: "memory"); return v; }
  void wr(unsigned o, uint16_t v) { *reinterpret_cast<volatile uint16_t *>(i2c + o) = v; __asm__ volatile("dsb oshst" ::: "memory"); }
  uint64_t now() { uint64_t ns = 0; absolutetime_to_nanoseconds(mach_absolute_time(), &ns); return ns; }
  void delay() { IODelay(5); }
};
// Same controller ownership covers the snapshot AND pet. No fuel-gauge client
// may bypass this lock; no I2C2/PMIC service is registered by this implementation.
struct Ops : IO {
  bool read(unsigned reg, uint8_t *value) { return a04charger::transfer(*this, true, uint8_t(reg), value); }
  bool kick(uint8_t value) { return a04charger::transfer(*this, false, 0, &value); }
};
bool inheritedReady() {
  uint32_t gate = rd32(top, 0x24), mux = rd32(infra, 0x80);
  uint32_t dir0 = rd32(gpio, 0), out0 = rd32(gpio, 0x100);
  uint32_t in0 = rd32(gpio, 0x200), dir1 = rd32(gpio, 0x10), in1 = rd32(gpio, 0x210);
  IOLog("a04br3 charger: inherited gate=%08x mux=%08x EN mode%u dir%u out%u in%u USB mode%u dir%u in%u AC mode%u dir%u in%u SDA%u SCL%u\n",
    gate, mux, mode(9), (dir0 >> 9) & 1, (out0 >> 9) & 1, (in0 >> 9) & 1,
    mode(34), (dir1 >> 2) & 1, (in1 >> 2) & 1, mode(35), (dir1 >> 3) & 1, (in1 >> 3) & 1, mode(60), mode(61));
  // Observer only; dedicated clock/pin setup is owned by BoardCore initialization.
  // Never drive EN, borrow touch's VGP1, or reset a busy bus.
  if ((gate & (1U << 16)) || mode(60) != 1 || mode(61) != 1 || mode(9) ||
      !(dir0 & (1U << 9)) || (out0 & (1U << 9)) || (in0 & (1U << 9)) ||
      mode(34) || mode(35) || (dir1 & 12)) return false;
  IO io;
  IOLog("a04br3 charger: i2c2 inherited control=%04x timing=%04x hs=%04x io=%04x div=%04x start=%04x\n",
    io.rd(0x10), io.rd(0x20), io.rd(0x48), io.rd(0x40), io.rd(0x70), io.rd(0x24));
  return !(io.rd(0x24) & 1) && io.rd(0x20) && !(io.rd(0x48) & 1) && io.rd(0x40) == 3 && io.rd(0x60) == 0;
}
void report(a04charger::Result result) {
  const auto &s = owner.latest;
  IOLog("a04br3 charger: t=%llu valid=%02x raw=%02x,%02x,%02x,%02x,%02x,%02x,%02x,%02x result=%u kicks=%u firstFAULT=%u history=%02x\n",
    (unsigned long long)s.at, s.valid, s.raw[0], s.raw[1], s.raw[2], s.raw[3], s.raw[4], s.raw[5], s.raw[6], s.raw[7], unsigned(result), owner.kicks,
    (owner.first.valid & 1) ? owner.first.raw[0] & 7 : 255, owner.faultHistory);
}
void service(void *, wait_result_t) {
  // This already running thread owns all reads and the 10 s servicing cadence.
  // It is established before the first charger transaction; no detached probe.
  bool initialized = false;
  for (;;) {
    IOLockLock(bus);
    if (!initialized) {
      inheritedReady(); // stage 1 records inherited state, not readiness as a permanent gate
      IOLog("a04br3 charger: clock ahb=%08x maincon=%08x pcw=%08x pwr=%08x\n",
            rd32(top,0), rd32(apmixed,0x120), rd32(apmixed,0x124), rd32(apmixed,0x130));
      Ops setup; auto status = a04charger::initialize(setup);
      IOLog("a04br3 charger: I2C2 initialize result=%u (0 ready)\n", unsigned(status));
      if (status != a04charger::BoardReady) { owner.stopped=true; IOLockUnlock(bus); break; }
      initialized = true;
    }
    if (!inheritedReady()) {
      owner.stopped = true;
      IOLockUnlock(bus);
      IOLog("a04br3 charger: inherited resources unavailable; owner stopped; no recovery/config writes, charger fallback may occur\n");
      break;
    }
    Ops ops; a04charger::Result result = a04charger::step(owner, ops);
    report(result);
    IOLockUnlock(bus);
    if (result != a04charger::Maintained) {
      IOLog("a04br3 charger: owner stopped; no policy repair/retry; 30 s WDT fallback may alter inherited settings\n");
      break;
    }
    IOSleep(10000);
  }
  // IO mappings, lock and first raw/FAULT remain for crash diagnostics until reboot.
  thread_terminate_self();
}

// ---- a04br3-charger-maintain=1 -------------------------------------------------
namespace cm = a04charger::maintain;
unsigned logEvery = 6; // cycles of 10 s
bool stillOwned() {
  IO io;
  return !(rd32(top, 0x24) & (1U << 16)) && !(rd32(infra, 0x80) & 8) && mode(60) == 1 && mode(61) == 1 &&
         io.rd(0x40) == 3 && io.rd(0x70) == 1 && io.rd(0x28) == 0x8001;
}
const char *stepName(cm::StepResult r) {
  switch (r) { case cm::Kept: return "kept"; case cm::Repaired: return "repaired"; case cm::NotCharger: return "bad-id";
  case cm::ReadError: return "read-error"; default: return "write-error"; }
}
void maintainReport(const char *why, cm::StepResult r, const cm::State &st,
                    const cm::Write *w, unsigned n, bool gaugeOk, const uint16_t g[4]) {
  const auto &s = st.latest;
  const auto &before = st.sampled;
  uint64_t gapMs = st.worstGap / 1000000ULL;
  IOLog("a04br3 charger: maintain %s t=%llus %s stat=%u fault=%u raw=%02x,%02x,%02x,%02x,%02x,%02x,%02x,%02x valid=%02x "
        "kicks=%u worstgap=%llums repairs=%u rerr=%u werr=%u history=%02x\n",
        why, (unsigned long long)(s.at / 1000000000ULL), stepName(r), (s.raw[0] >> 4) & 7, s.raw[0] & 7,
        s.raw[0], s.raw[1], s.raw[2], s.raw[3], s.raw[4], s.raw[5], s.raw[6], s.raw[7], s.valid,
        st.kicks, (unsigned long long)gapMs, st.repairs, st.readErrors, st.writeErrors, st.faultHistory);
  if (n) IOLog("a04br3 charger: maintain wrote %u reg(s) from raw=%02x,%02x,%02x,%02x,%02x,%02x,%02x,%02x: %02x=%02x %02x=%02x %02x=%02x %02x=%02x\n",
               n, before.raw[0], before.raw[1], before.raw[2], before.raw[3], before.raw[4], before.raw[5], before.raw[6], before.raw[7],
               w[0].reg, w[0].value, n > 1 ? w[1].reg : 0, n > 1 ? w[1].value : 0,
               n > 2 ? w[2].reg : 0, n > 2 ? w[2].value : 0, n > 3 ? w[3].reg : 0, n > 3 ? w[3].value : 0);
  if (!gaugeOk) { IOLog("a04br3 battery: gauge read failed\n"); return; }
  int tenthC = int(g[3]) - 2732;
  unsigned absC = unsigned(tenthC < 0 ? -tenthC : tenthC);
  IOLog("a04br3 battery: t=%llus vbat=%umV rsoc=%u%% ite=%u.%u%% temp=%s%u.%uC\n",
        (unsigned long long)(s.at / 1000000000ULL), unsigned(g[0]), unsigned(g[1]), g[2] / 10U, g[2] % 10U,
        tenthC < 0 ? "-" : "", absC / 10, absC % 10);
}
void maintainService(void *, wait_result_t) {
  cm::State st;
  bool ready = false;
  unsigned sinceLog = logEvery, lastKey = ~0U;
  for (;;) {
    cm::Write w[4] = {}; unsigned n = 0;
    cm::StepResult r = cm::ReadError;
    bool logNow = false, periodic = false, gaugeOk = false;
    uint16_t g[4] = {};
    IOLockLock(bus);
    if (!ready || !stillOwned()) {
      inheritedReady(); // logs inherited GPIO/controller state; EN/detect pins are never driven
      IO setup;
      // I2C2 carries only this thread's charger/gauge traffic, so a transfer this
      // thread abandoned on timeout may be cleared with the controller's own
      // SOFTRESET (i2c-mt65xx init_hw does the same). Never at first init.
      if (st.cycles && (setup.rd(0x24) & 1)) { setup.wr(0x50, 1); IOLog("a04br3 charger: maintain soft-reset stuck I2C2\n"); }
      auto status = a04charger::initialize(setup);
      IOLog("a04br3 charger: maintain I2C2 initialize result=%u (0 ready)\n", unsigned(status));
      ready = status == a04charger::BoardReady;
    }
    if (ready) {
      IO io; r = cm::step(st, io, w, &n);
      if (r == cm::ReadError || r == cm::WriteError) ready = false; // re-initialize next cycle
      unsigned key = (unsigned(st.latest.raw[0]) << 8) | unsigned(r);
      periodic = ++sinceLog >= logEvery;
      logNow = periodic || n || key != lastKey;
      lastKey = key;
      if (logNow) {
        gaugeOk = cm::gaugeRead(io, 0x09, &g[0]) && cm::gaugeRead(io, 0x0d, &g[1]) &&
                  cm::gaugeRead(io, 0x0f, &g[2]) && cm::gaugeRead(io, 0x08, &g[3]);
      }
    }
    IOLockUnlock(bus);
    if (logNow) {
      maintainReport(periodic ? "periodic" : "change", r, st, w, n, gaugeOk, g);
      sinceLog = 0;
    }
    IOSleep(10000); // stock BAT_TASK_PERIOD; TI watchdog is 30 s
  }
}
bool mapAll() {
  bus = IOLockAlloc();
  if (!bus) { IOLog("a04br3 charger: no owner lock; no I2C access\n"); return false; }
  i2c = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x1100b000, 0x1000));
  gpio = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10005000, 0x1000));
  top = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10000000, 0x1000));
  infra = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10001000, 0x1000));
  apmixed = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10018000, 0x1000));
  if (!i2c || !gpio || !top || !infra || !apmixed) { IOLog("a04br3 charger: mapping failed; no I2C access\n"); return false; }
  return true;
}
}
// BEGIN firmware DT lookup: actual XNU device_tree.h status contract.
static bool firmwareHandoff(const void **record, unsigned *length) {
  DTEntry chosen = nullptr;
  return SecureDTLookupEntry(nullptr, "/chosen", &chosen) == kSuccess &&
         SecureDTGetProperty(chosen, "a04br3-charger-owner", record, length) == kSuccess;
}
// END firmware DT lookup
extern "C" void a04br3_charger_init(void) {
  unsigned enabled = 0;
  unsigned maintainMode = 0;
  if (PE_parse_boot_argn("a04br3-charger-maintain", &maintainMode, sizeof maintainMode) && maintainMode == 1) {
    unsigned seconds = 0;
    if (PE_parse_boot_argn("a04br3-charger-log", &seconds, sizeof seconds) && seconds >= 10 && seconds <= 3600) logEvery = seconds / 10;
    IOLog("a04br3 charger: maintain mode: stock ceiling IN 1.5 A, ICHRG 1.3 A, VBREG 4.32 V, 6 h timer; kick 10 s; log %u s%s\n",
          logEvery * 10, PE_parse_boot_argn("a04br3-charger-owner", &enabled, sizeof enabled) ? "; a04br3-charger-owner ignored" : "");
    if (!mapAll()) return;
    thread_t thread = nullptr;
    // Above default kernel priority so boot-time I/O cannot starve the 30 s watchdog.
    if (kernel_thread_start_priority(maintainService, nullptr, 81, &thread) != KERN_SUCCESS) {
      IOLog("a04br3 charger: maintain thread unavailable; no I2C access\n"); return;
    }
    thread_deallocate(thread);
    return;
  }
  if (!PE_parse_boot_argn("a04br3-charger-owner", &enabled, sizeof enabled) || enabled != 1) return;
  // Validate this boot's sealed firmware record before even mapping registers.
  const void *record=nullptr; unsigned length=0;
  if (!firmwareHandoff(&record, &length)) {
    owner.stopped=true;IOLog("a04br3 charger: no firmware handoff; no charger access\n");return;
  }
  uint64_t counter,frequency;
  __asm__ volatile("mrs %0, cntfrq_el0":"=r"(frequency));
  IO clock; uint64_t nowNs=clock.now(); // NS first: scheduling delay makes deadline conservative
  __asm__ volatile("mrs %0, cntpct_el0":"=r"(counter));
  if(!a04charger::adopt(owner,static_cast<const uint8_t *>(record),length,counter,frequency,nowNs)) {
    owner.stopped=true;
    IOLog("a04br3 charger: stopped/invalid/late firmware owner; no charger access\n");return;
  }
  const auto fw=static_cast<const uint8_t *>(record);
  IOLog("a04br3 charger: firmware handoff verified last=%llu exit=%llu now=%llu freq=%llu first=%llx history=%llx\n",
        (unsigned long long)a04charger::get64(fw+24),(unsigned long long)a04charger::get64(fw+40),
        (unsigned long long)counter,(unsigned long long)frequency,
        (unsigned long long)a04charger::get64(fw+72),(unsigned long long)a04charger::get64(fw+56));
  bus = IOLockAlloc();
  if (!bus) { IOLog("a04br3 charger: no owner lock; no I2C access\n"); return; }
  i2c = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x1100b000, 0x1000));
  gpio = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10005000, 0x1000));
  top = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10000000, 0x1000));
  infra = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10001000, 0x1000));
  apmixed = reinterpret_cast<volatile uint8_t *>(ml_io_map(0x10018000, 0x1000));
  if (!i2c || !gpio || !top || !infra || !apmixed) { IOLog("a04br3 charger: mapping failed; no I2C access\n"); return; }
  thread_t thread = nullptr;
  if (kernel_thread_start(service, nullptr, &thread) != KERN_SUCCESS) {
    IOLog("a04br3 charger: owner thread unavailable; no I2C access\n"); return;
  }
  thread_deallocate(thread);
}
