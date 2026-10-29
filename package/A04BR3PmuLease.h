// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
// Local exclusion only. `qualified` must be established independently of an
// empty PMU register snapshot, a boot argument, or this lock.
struct A04BR3PmuLease { uint64_t state, generation; };
static inline bool a04br3_pmu_try(A04BR3PmuLease &l, bool qualified, bool watchdogHeld, unsigned cpu, uint64_t *generation)
{
  if (!qualified || !watchdogHeld || cpu>=4 || !generation) return false;
  uint64_t expected=0;
  if (!__atomic_compare_exchange_n(&l.state,&expected,1,false,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED)) return false;
  if (l.generation>=UINT64_MAX/8-1) {__atomic_store_n(&l.state,0,__ATOMIC_RELEASE);return false;}
  uint64_t gen=++l.generation, token=(gen<<3)|(cpu+1);
  *generation=gen;__atomic_store_n(&l.state,token,__ATOMIC_RELEASE);return true;
}
static inline bool a04br3_pmu_valid(A04BR3PmuLease &l, bool watchdogHeld, unsigned cpu, uint64_t generation)
{
  return watchdogHeld && cpu<4 && generation && generation<UINT64_MAX/8 &&
    __atomic_load_n(&l.state,__ATOMIC_ACQUIRE)==((generation<<3)|(cpu+1));
}
static inline bool a04br3_pmu_release(A04BR3PmuLease &l, bool watchdogHeld, unsigned cpu, uint64_t generation)
{
  if (!a04br3_pmu_valid(l,watchdogHeld,cpu,generation)) return false;
  uint64_t token=(generation<<3)|(cpu+1);
  return __atomic_compare_exchange_n(&l.state,&token,0,false,__ATOMIC_RELEASE,__ATOMIC_RELAXED);
}
