// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>

// Disconnected prototype: no production caller, register addresses or loops.
// The caller must supply an owned, bounded RTC read and monotonically advancing
// generic counter brackets. The seconds source's oscillator, update latency,
// independence and UTC accuracy are not established by this algorithm.
// The caller must independently ensure no RTC adjustment or 60 or more second updates between actual sample
// instants. A wall-time gap under 60s alone does not prove this; identical
// seconds can alias.
struct A04BR3RtcPoint { uint64_t before, after; unsigned second; };
struct A04BR3RtcReference {
  A04BR3RtcPoint previous;
  uint64_t firstEarly, firstLate, lastEarly, lastLate;
  unsigned transitions;
  bool havePrevious, invalid;
};
struct A04BR3RtcBounds { uint64_t minTicks, maxTicks; unsigned rtcSeconds; bool valid; };
static inline bool a04br3_rtc_fail(A04BR3RtcReference &s) { s.invalid=true; return false; }
static inline bool a04br3_rtc_observe(A04BR3RtcReference &s, const A04BR3RtcPoint &p)
{
  if (s.invalid || p.second>=60 || p.before>p.after) return a04br3_rtc_fail(s);
  if (!s.havePrevious) { s.previous=p; s.havePrevious=true; return true; }
  if (p.before<s.previous.after) return a04br3_rtc_fail(s);
  if (p.second != s.previous.second) {
    if (p.second != (s.previous.second+1U)%60U || s.transitions==59U)
      return a04br3_rtc_fail(s);
    // Transition occurs after the previous read and no later than this read.
    // Using the outer brackets is conservative even if WACS latency varies.
    const uint64_t early=s.previous.before, late=p.after;
    if (!s.transitions) { s.firstEarly=early; s.firstLate=late; }
    s.lastEarly=early; s.lastLate=late; ++s.transitions;
  }
  s.previous=p; return true;
}
static inline A04BR3RtcBounds a04br3_rtc_bounds(const A04BR3RtcReference &s)
{
  A04BR3RtcBounds r={};
  if (s.invalid || s.transitions<2 || s.lastEarly<=s.firstLate || s.lastLate<s.firstEarly) return r;
  r.minTicks=s.lastEarly-s.firstLate; r.maxTicks=s.lastLate-s.firstEarly;
  r.rtcSeconds=s.transitions-1; r.valid=true; return r;
}
// Exactly one bounded sampling operation; no sleep, retry, register writes or
// logging here. Adapter is responsible for WACS serialization and checks.
template<class IO> static bool a04br3_rtc_sample(IO &io, A04BR3RtcReference &s)
{
  A04BR3RtcPoint p={}; p.before=io.timer();
  if (!io.readSecond(&p.second)) return a04br3_rtc_fail(s);
  p.after=io.timer();
  return a04br3_rtc_observe(s,p);
}
