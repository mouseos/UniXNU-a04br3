// SPDX-License-Identifier: MIT
#pragma once
#include "A04BR3CpuClockMeter.h"

// Optional MT8167 clkdbg test-route experiment. The caller must already have
// printed the original registers and armed a watchdog. The local lock cannot
// exclude firmware or another hardware owner; WDT reset retention is unknown.
// The existing measurement holds the same lock through BorrowedIO.
template<class IO> struct A04BR3MeterBorrowedIO {
  IO &io;
  bool lock() { return true; }
  void unlock() {}
  uint32_t top(unsigned x) { return io.top(x); }
  uint32_t pll(unsigned x) { return io.pll(x); }
  uint32_t infra(unsigned x) { return io.infra(x); }
  void write(unsigned x,uint32_t v) { io.write(x,v); }
  void delay(unsigned x) { io.delay(x); }
};

template<class IO> static A04BR3CpuMeterResult a04br3_cpu_meter_routed(IO &io)
{
  A04BR3CpuMeterResult r = {};
  if (!io.lock()) { r.status=1; return r; }
  struct Release { IO &io; ~Release() { io.unlock(); } } release{io};
  const uint32_t ctrl=io.top(0x10), debug=io.top(0x38), test=io.pll(0x64), hop=io.pll(0x14);
  r.ctrl=ctrl; r.debug=debug; r.plltest=test; r.hopping=hop;
  r.mux=io.infra(0); r.dcm=io.infra(0x14); r.pll0=io.pll(0x100);
  r.pll1=io.pll(0x104); r.power=io.pll(0x110);
  if (ctrl || (hop & 1U) || ((r.mux>>2)&3U)!=1 || (r.dcm&1U) ||
      !(r.pll0&1U) || !(r.power&1U) || (r.power&2U)) { r.status=2; return r; }
  // Publish the exact snapshot which will be used for restoration before any
  // register writes. UART flushing and external ownership remain unverified.
  io.beforeRoute(r);
  // Refuse any changed snapshot before starting. All accesses occur under the
  // local lock; this still cannot exclude a concurrent external writer.
  if (io.top(0x10)!=ctrl || io.top(0x38)!=debug || io.pll(0x64)!=test || io.pll(0x14)!=hop) { r.status=2; return r; }
  const uint32_t routedTest=(test & ~0x3ffU) | 0x287U;
  io.writePllTest(routedTest);
  if (io.pll(0x64)!=((test & ~0x3ffU)|0x287U)) { r.status=9; return r; }
  // Check ownership before changing another register.
  if (io.top(0x38)!=debug || io.top(0x10)!=0 || io.pll(0x14)!=hop) { r.status=9; return r; }
  io.writeDebug(debug & ~0xffU);
  if (io.top(0x38)!=(debug & ~0xffU)) {
    // If debug never changed and the meter is idle, restore our test field.
    // Ownership can still race here; failure stays quarantined regardless.
    if (io.top(0x38)==debug && io.top(0x10)==0 && io.pll(0x14)==hop &&
        io.pll(0x64)==routedTest) io.writePllTest(test);
    r.status=9; return r;
  }
  io.delayRoute(10);
  A04BR3MeterBorrowedIO<IO> borrowed{io};
  auto measured=a04br3_cpu_meter(borrowed);
  // Diagnostics must retain the pre-route snapshot even on later failure.
  measured.ctrl=ctrl; measured.debug=debug; measured.plltest=test; measured.hopping=hop;
  measured.mux=r.mux; measured.dcm=r.dcm; measured.pll0=r.pll0;
  measured.pll1=r.pll1; measured.power=r.power;
  // A dirty/foreign meter must not be manipulated to restore a route.
  if (a04br3_cpu_meter_requires_recovery(measured.status) || io.top(0x10)!=0) { measured.status=9; return measured; }
  if ((io.top(0x38)&0xffU)!=0 || (io.pll(0x64)&0x3ffU)!=0x287U || io.pll(0x14)!=hop) { measured.status=9; return measured; }
  const uint32_t d=io.top(0x38);
  if ((d & 0xffU)!=0) { measured.status=9; return measured; }
  io.writeDebug((d & ~0xffU) | (debug & 0xffU));
  if (io.top(0x38)!=((d & ~0xffU)|(debug & 0xffU))) { measured.status=9; return measured; }
  const uint32_t p=io.pll(0x64);
  if ((p&0x3ffU)!=0x287U || io.top(0x10)!=0) { measured.status=9; return measured; }
  io.writePllTest((p & ~0x3ffU) | (test & 0x3ffU));
  if (io.pll(0x64)!=((p & ~0x3ffU)|(test & 0x3ffU))) { measured.status=9; return measured; }
  return measured;
}
