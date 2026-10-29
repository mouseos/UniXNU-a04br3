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

struct A04BR3CpuMeterCrosscheck {
  A04BR3CpuMeterResult sample[3];
  unsigned attempted, route_stage; // 1 test set, 2 debug set, 3 debug restored, 4 test restored
};

template<class IO> static A04BR3CpuMeterResult a04br3_cpu_meter_route_impl(IO &io, A04BR3CpuMeterCrosscheck *cross)
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
  if (cross && (io.infra(0)!=r.mux || io.infra(0x14)!=r.dcm ||
      io.pll(0x100)!=r.pll0 || io.pll(0x104)!=r.pll1 || io.pll(0x110)!=r.power)) { r.status=2; return r; }
  const uint32_t routedTest=(test & ~0x3ffU) | 0x287U;
  io.writePllTest(routedTest);
  if (io.pll(0x64)!=((test & ~0x3ffU)|0x287U)) { r.status=9; return r; }
  if (cross) cross->route_stage=1;
  // Check ownership before changing another register.
  if (io.top(0x38)!=debug || io.top(0x10)!=0 || io.pll(0x14)!=hop) { r.status=9; return r; }
  io.writeDebug(debug & ~0xffU);
  if (io.top(0x38)!=(debug & ~0xffU)) {
    // If debug never changed and the meter is idle, restore our test field.
    // Ownership can still race here; failure stays quarantined regardless.
    if (!cross && io.top(0x38)==debug && io.top(0x10)==0 && io.pll(0x14)==hop &&
        io.pll(0x64)==routedTest) io.writePllTest(test);
    r.status=9; return r;
  }
  if (cross) cross->route_stage=2;
  io.delayRoute(10);
  A04BR3MeterBorrowedIO<IO> borrowed{io};
  A04BR3CpuMeterResult measured = {};
  if (!cross) measured=a04br3_cpu_meter(borrowed);
  else {
    const unsigned ids[] = {25, 7, 24};
    for (unsigned i=0; i<3; ++i) {
      // Detect changes between samples, not only within each sample.
      if ((io.top(0x38)!=(debug & ~0xffU) || io.pll(0x64)!=routedTest ||
          io.pll(0x14)!=hop || io.infra(0)!=r.mux || io.infra(0x14)!=r.dcm ||
          io.pll(0x100)!=r.pll0 || io.pll(0x104)!=r.pll1 || io.pll(0x110)!=r.power)) {
        measured.status=7; break;
      }
      cross->attempted=i+1;
      cross->sample[i]=a04br3_cpu_meter_id(borrowed, ids[i]);
      measured=cross->sample[i];
      if (measured.status) break;
    }
  }
  // A surrounding-state change makes ownership uncertain. In cm=3 do not
  // touch the route again, even when the meter itself has become idle.
  if (cross && measured.status==7) measured.status=9;
  // Diagnostics must retain the pre-route snapshot even on later failure.
  measured.ctrl=ctrl; measured.debug=debug; measured.plltest=test; measured.hopping=hop;
  measured.mux=r.mux; measured.dcm=r.dcm; measured.pll0=r.pll0;
  measured.pll1=r.pll1; measured.power=r.power;
  // A dirty/foreign meter must not be manipulated to restore a route.
  if (a04br3_cpu_meter_requires_recovery(measured.status) || io.top(0x10)!=0) { measured.status=9; return measured; }
  if ((io.top(0x38)&0xffU)!=0 || (io.pll(0x64)&0x3ffU)!=0x287U || io.pll(0x14)!=hop) { measured.status=9; return measured; }
  if (cross && (io.top(0x38)!=(debug & ~0xffU) || io.pll(0x64)!=routedTest ||
      io.infra(0)!=r.mux || io.infra(0x14)!=r.dcm || io.pll(0x100)!=r.pll0 ||
      io.pll(0x104)!=r.pll1 || io.pll(0x110)!=r.power)) { measured.status=9; return measured; }
  const uint32_t d=io.top(0x38);
  if ((cross && d!=(debug & ~0xffU)) || (!cross && (d & 0xffU)!=0)) { measured.status=9; return measured; }
  io.writeDebug((d & ~0xffU) | (debug & 0xffU));
  if (io.top(0x38)!=((d & ~0xffU)|(debug & 0xffU))) { measured.status=9; return measured; }
  if (cross) cross->route_stage=3;
  if (cross && (io.top(0x38)!=debug || io.pll(0x64)!=routedTest || io.pll(0x14)!=hop ||
      io.infra(0)!=r.mux || io.infra(0x14)!=r.dcm || io.pll(0x100)!=r.pll0 ||
      io.pll(0x104)!=r.pll1 || io.pll(0x110)!=r.power)) { measured.status=9; return measured; }
  const uint32_t p=io.pll(0x64);
  if ((cross ? p!=routedTest : (p&0x3ffU)!=0x287U) || io.top(0x10)!=0) { measured.status=9; return measured; }
  io.writePllTest((p & ~0x3ffU) | (test & 0x3ffU));
  if (io.pll(0x64)!=((p & ~0x3ffU)|(test & 0x3ffU))) { measured.status=9; return measured; }
  if (cross) cross->route_stage=4;
  return measured;
}

template<class IO> static A04BR3CpuMeterResult a04br3_cpu_meter_routed(IO &io)
{ return a04br3_cpu_meter_route_impl(io, nullptr); }

template<class IO> static A04BR3CpuMeterResult a04br3_cpu_meter_crosscheck(IO &io, A04BR3CpuMeterCrosscheck &cross)
{ cross = {}; return a04br3_cpu_meter_route_impl(io, &cross); }

// A pre-write refusal or a complete, verified restoration permits feeding.
// stage 0 alone is insufficient: the first write may have failed readback.
static inline bool a04br3_cpu_crosscheck_feed_allowed(const A04BR3CpuMeterResult &r, const A04BR3CpuMeterCrosscheck &cross)
{ return (cross.route_stage == 0 && (r.status == 1 || r.status == 2)) ||
         (cross.route_stage == 4 && !a04br3_cpu_meter_requires_recovery(r.status)); }
