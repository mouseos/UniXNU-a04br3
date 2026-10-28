// SPDX-License-Identifier: MIT
// Explicit board-only opt-in: a04br3-charger-owner=1. OFF touches no hardware.
// NOT passive/read-only: even reads start TI's 30 s WDT and can consume FAULT.
// Preserves verified LK policy only; no voltage/current/safety-timer setters.
#include <IOKit/IOLib.h>
#include <kern/thread.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <stdint.h>
#include "A04BR3RegisterLock.h"
#include "A04BR3ChargerCore.h"
#include "A04BR3GaugeCore.h"
#include "A04BR3ChargerHandoff.h"
#include "A04BR3ChargerBoardCore.h"
extern "C" vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
// XNU kern/thread.h declares this only in its private kernel context; this
// board archive links into that source kernel, using the same C prototype.
extern "C" void thread_terminate_self(void);
namespace {
volatile uint8_t *i2c, *gpio, *top, *infra, *apmixed;
IOLock *bus;
bool gaugeVoltage;
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
  uint64_t readStart[8] = {}, readEnd[8] = {};
  bool read(unsigned reg, uint8_t *value) {
    readStart[reg] = now();
    bool ok = a04charger::transfer(*this, true, uint8_t(reg), value);
    readEnd[reg] = now();
    return ok;
  }
  bool kick(uint8_t value) { return a04charger::transfer(*this, false, 0, &value); }
};
bool inheritedReady() {
  uint32_t gate = rd32(top, 0x24);
  uint32_t dir0 = rd32(gpio, 0), out0 = rd32(gpio, 0x100);
  uint32_t in0 = rd32(gpio, 0x200), dir1 = rd32(gpio, 0x10);
  // Observer only; dedicated clock/pin setup is owned by BoardCore initialization.
  // Never drive EN, borrow touch's VGP1, or reset a busy bus.
  if ((gate & (1U << 16)) || mode(60) != 1 || mode(61) != 1 || mode(9) ||
      !(dir0 & (1U << 9)) || (out0 & (1U << 9)) || (in0 & (1U << 9)) ||
      mode(34) || mode(35) || (dir1 & 12)) return false;
  IO io;
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
  IO serviceClock;
  uint64_t serviceStarted=serviceClock.now(), initStarted=0, initEnded=0, readyEnded=0;
  bool initialized = false;
  for (;;) {
    IOLockLock(bus);
    if (!initialized) {
      initStarted=serviceClock.now();
      if (owner.handoffPending && initStarted >= owner.handoffDeadline) {
        owner.stopped=true; IOLockUnlock(bus);
        IOLog("a04br3 charger: handoff expired before I2C initialization; no I2C access\n");
        break;
      }
      Ops setup; auto status = a04charger::initialize(setup);
      initEnded=serviceClock.now();
      if (status != a04charger::BoardReady) { owner.stopped=true; IOLockUnlock(bus); IOLog("a04br3 charger: I2C2 initialize result=%u\n", unsigned(status)); break; }
      initialized = true;
    }
    if (!inheritedReady()) {
      owner.stopped = true;
      IOLockUnlock(bus);
      IOLog("a04br3 charger: inherited resources unavailable; owner stopped; no recovery/config writes, charger fallback may occur\n");
      break;
    }
    readyEnded=serviceClock.now();
    Ops ops; uint64_t started=ops.now(), deadline=owner.handoffPending ? owner.handoffDeadline : 0;
    a04charger::Result result = a04charger::step(owner, ops);
    uint64_t ended=ops.now();
    // Optional measurement client; no additional transaction when OFF or when
    // the charger owner has stopped. Never another I2C2 thread/private lock.
    const bool sampled=gaugeVoltage && result==a04charger::Maintained;
    a04gauge::Sample g;
    if(sampled) g=a04gauge::sample(ops);
    IOLockUnlock(bus);
    if(sampled){
      if(g.valid)
        IOLog("a04br3 gauge: t=%llu valid=1 mode=%04x raw=%02x,%02x,%02x mV=%u current=unknown\n",
              (unsigned long long)g.at,g.mode.value,g.voltage.raw[0],g.voltage.raw[1],g.voltage.raw[2],unsigned(g.voltage.value));
      else
        IOLog("a04br3 gauge: t=%llu valid=0 result=%u mode=%04x mr=%u raw=%02x,%02x,%02x vr=%u mV=unknown current=unknown\n",
              (unsigned long long)g.at,unsigned(g.result),g.mode.value,unsigned(g.mode.result),g.voltage.raw[0],g.voltage.raw[1],g.voltage.raw[2],unsigned(g.voltage.result));
    }
    report(result);
    if (owner.kicks <= 1 || result != a04charger::Maintained) IOLog("a04br3 charger: service=%llu init=%llu/%llu ready=%llu step=%llu/%llu handoff_deadline=%llu read0=%llu/%llu read1=%llu/%llu read2=%llu/%llu read3=%llu/%llu read4=%llu/%llu read5=%llu/%llu read6=%llu/%llu read7=%llu/%llu\n",
      (unsigned long long)serviceStarted,(unsigned long long)initStarted,(unsigned long long)initEnded,(unsigned long long)readyEnded,(unsigned long long)started,(unsigned long long)ended,(unsigned long long)deadline,
      (unsigned long long)ops.readStart[0],(unsigned long long)ops.readEnd[0],(unsigned long long)ops.readStart[1],(unsigned long long)ops.readEnd[1],
      (unsigned long long)ops.readStart[2],(unsigned long long)ops.readEnd[2],(unsigned long long)ops.readStart[3],(unsigned long long)ops.readEnd[3],
      (unsigned long long)ops.readStart[4],(unsigned long long)ops.readEnd[4],(unsigned long long)ops.readStart[5],(unsigned long long)ops.readEnd[5],
      (unsigned long long)ops.readStart[6],(unsigned long long)ops.readEnd[6],(unsigned long long)ops.readStart[7],(unsigned long long)ops.readEnd[7]);
    if (result != a04charger::Maintained) {
      IOLog("a04br3 charger: owner stopped; no policy repair/retry; 30 s WDT fallback may alter inherited settings\n");
      break;
    }
    IOSleep(10000);
  }
  // IO mappings, lock and first raw/FAULT remain for crash diagnostics until reboot.
  thread_terminate_self();
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
  IOLog("a04br3 charger: firmware handoff verified last=%llu exit=%llu now=%llu freq=%llu first=%llx history=%llx now_ns=%llu deadline_ns=%llu\n",
        (unsigned long long)a04charger::get64(fw+24),(unsigned long long)a04charger::get64(fw+40),
        (unsigned long long)counter,(unsigned long long)frequency,
        (unsigned long long)a04charger::get64(fw+72),(unsigned long long)a04charger::get64(fw+56),
        (unsigned long long)nowNs,(unsigned long long)owner.handoffDeadline);
  unsigned sampleVoltage=0;
  gaugeVoltage=PE_parse_boot_argn("a04br3-gauge-voltage",&sampleVoltage,sizeof sampleVoltage)&&sampleVoltage==1;
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
