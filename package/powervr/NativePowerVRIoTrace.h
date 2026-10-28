// SPDX-License-Identifier: MIT
// Bounded, nonblocking completion records. Writers publish before the single
// reporter reads; overflowing records are counted rather than blocking GPU work.
#pragma once
#include <stdint.h>
struct NativeIoRecord { uint64_t start, end, tid, fence; uint32_t pid, kind; int32_t result; };
struct NativeIoSlot { unsigned state; NativeIoRecord record; };
struct NativeIoTrace {
 unsigned enabled, cursor;
 unsigned immediate_enabled, immediate_count, immediate_skipped;
 uint64_t immediate_threshold_ticks;
 uint64_t threshold_ticks, dropped;
 uint64_t short_count[3], short_ticks[3];
 NativeIoSlot slot[128];
};
// Exact global limit even when multiple user-client threads finish together.
static inline bool native_io_immediate_claim(NativeIoTrace *t, const NativeIoRecord &r) {
 if (!__atomic_load_n(&t->enabled,__ATOMIC_ACQUIRE) ||
     !__atomic_load_n(&t->immediate_enabled,__ATOMIC_ACQUIRE) || !r.start ||
     r.end<r.start || r.kind<1 || r.kind>3 ||
     r.end-r.start<t->immediate_threshold_ticks) return false;
 unsigned n=__atomic_load_n(&t->immediate_count,__ATOMIC_RELAXED);
 while (n<8) {
  if (__atomic_compare_exchange_n(&t->immediate_count,&n,n+1,false,__ATOMIC_RELAXED,__ATOMIC_RELAXED)) return true;
 }
 __atomic_fetch_add(&t->immediate_skipped,1u,__ATOMIC_RELAXED);
 return false;
}
static inline void native_io_record(NativeIoTrace *t, const NativeIoRecord &r) {
 if (!__atomic_load_n(&t->enabled,__ATOMIC_ACQUIRE)) return;
 if (!r.start || r.end < r.start || r.kind < 1 || r.kind > 3) return;
 const uint64_t elapsed=r.end-r.start;
 if (r.result==0 && elapsed<t->threshold_ticks) {
  __atomic_fetch_add(&t->short_count[r.kind-1],1ull,__ATOMIC_RELAXED);
  __atomic_fetch_add(&t->short_ticks[r.kind-1],elapsed,__ATOMIC_RELAXED);
  return;
 }
 unsigned start=__atomic_fetch_add(&t->cursor,1u,__ATOMIC_RELAXED);
 for (unsigned n=0;n<128;n++) {
  NativeIoSlot *slot=&t->slot[(start+n)%128]; unsigned expected=0;
  if (__atomic_compare_exchange_n(&slot->state,&expected,1u,false,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED)) {
   slot->record=r; __atomic_store_n(&slot->state,2u,__ATOMIC_RELEASE); return;
  }
 }
 __atomic_fetch_add(&t->dropped,1ull,__ATOMIC_RELAXED);
}
static inline bool native_io_take(NativeIoTrace *t, unsigned index, NativeIoRecord *out) {
 NativeIoSlot *slot=&t->slot[index]; unsigned expected=2;
 if (!__atomic_compare_exchange_n(&slot->state,&expected,3u,false,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED)) return false;
 *out=slot->record; __atomic_store_n(&slot->state,0u,__ATOMIC_RELEASE); return true;
}
