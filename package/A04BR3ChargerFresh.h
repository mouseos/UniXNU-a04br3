// SPDX-License-Identifier: MIT
#pragma once
#include "A04BR3ChargerCore.h"
namespace a04charger {
template<class Ops> struct FreshGuard {
  Ops &ops; Owner &owner; uint64_t start;
  uint64_t now() { return ops.now(); }
  bool read(unsigned reg, uint8_t *v) { return ops.read(reg,v); }
  bool kick(uint8_t value) {
    uint64_t t=ops.now();
    if (t<start || t-start>=1000000000ULL ||
        (owner.handoffPending && t>=owner.handoffDeadline) ||
        (owner.kicks && (t<owner.lastKick || t-owner.lastKick>=20000000000ULL))) return false;
    return ops.kick(value);
  }
};
// Retry only a complete, policy-valid snapshot that expired solely by age.
// Each attempt uses the unchanged core's 1 s freshness and 20 s owner bounds.
// First FAULT history is retained; IO, policy, fault and real deadline failures
// must never be retried. No watchdog write occurs on an expired snapshot.
template<class Ops> Result stepFresh(Owner &owner, Ops &ops, unsigned &attempts) {
  attempts=0;
  for (unsigned n=0; n<3; ++n) {
    uint64_t started=ops.now();
    bool pending=owner.handoffPending;
    uint64_t deadline=owner.handoffDeadline, previous=owner.lastKick;
    bool hadKick=owner.kicks!=0;
    FreshGuard<Ops> guarded{ops,owner,started};
    Result result=step(owner,guarded);
    ++attempts;
    if (result==Maintained) {
      // The physical write can precede its completion by arbitrary scheduling
      // delay. Count the next deadline from BEFORE the operation, not after it.
      owner.lastKick=started;
      uint64_t finished=ops.now();
      if (finished<started || finished-started>=1000000000ULL ||
          (pending && finished>=deadline) ||
          (hadKick && (finished<previous || finished-previous>=20000000000ULL))) {
        owner.stopped=true; return Deadline;
      }
      return result;
    }
    if (result!=Deadline || n==2 || owner.latest.valid!=0xff ||
        policy(owner.latest)!=Maintained || (owner.faultHistory & ~1U)) return result;
    uint64_t now=ops.now(), captured=owner.latest.at;
    if (now<captured || now-captured<1000000000ULL ||
        (owner.handoffPending && now>=owner.handoffDeadline) ||
        (owner.kicks && (now<owner.lastKick || now-owner.lastKick>=20000000000ULL))) return result;
    owner.stopped=false;
  }
  return Deadline;
}
}
