// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>

// This only serializes participating board clients. A lock is retained on an
// uncertain post-command state so another cooperating client cannot consume it.
// It is not a lease against firmware, other exception levels or a debugger.
enum A04BR3RtcWacsStatus { rtcOk, rtcContended, rtcForeign, rtcTimeout, rtcDirty, rtcBadValue };
struct A04BR3RtcWacsResult { A04BR3RtcWacsStatus status; unsigned second; bool quarantined; uint32_t initial, last, response; };
template<class IO> static A04BR3RtcWacsResult a04br3_rtc_wacs_read(IO &io)
{
  A04BR3RtcWacsResult r={rtcContended,0,false,0,0,0};
  if (!io.lock()) return r;
  uint32_t v=io.status(); r.initial=r.last=v;
  // Never clear a transaction whose origin is unknown.
  if (!(v & (1U<<21)) || ((v>>16)&7U)!=0) {r.status=rtcForeign;r.quarantined=true;return r;}
  io.command((0x800aU >> 1) << 16);
  bool ready=false;
  for (unsigned n=0;n<2048;++n) {
    v=io.status(); r.last=v;
    if (!(v & (1U<<21))) break;
    if (((v>>16)&7U)==6) { ready=true;break; }
  }
  if (!ready) { r.status=rtcTimeout;r.quarantined=true;return r; }
  r.response=v;
  const uint32_t value=v&0xffffU;
  io.clear();
  bool idle=false;
  for (unsigned n=0;n<2048;++n) {
    v=io.status(); r.last=v;
    if (!(v & (1U<<21))) break;
    if (((v>>16)&7U)==0) { idle=true;break; }
  }
  if (!idle) {r.status=rtcDirty;r.quarantined=true;return r;}
  io.unlock();
  if (value>=60U) {r.status=rtcBadValue;return r;}
  r.status=rtcOk;r.second=value;return r;
}
