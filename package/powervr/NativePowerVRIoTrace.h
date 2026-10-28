// SPDX-License-Identifier: MIT
// Bounded, nonblocking completion records. Writers publish before the single
// reporter reads; overflowing records are counted rather than blocking GPU work.
#pragma once
#include <stdint.h>
struct NativeIoRecord { uint64_t start, end, tid, fence; uint32_t pid, kind; int32_t result; };
struct NativeIoSlot { unsigned state; NativeIoRecord record; };
struct NativeIoTrace { unsigned enabled, cursor; uint64_t dropped; NativeIoSlot slot[128]; };
static inline void native_io_record(NativeIoTrace *t, const NativeIoRecord &r) {
 if (!__atomic_load_n(&t->enabled,__ATOMIC_RELAXED)) return;
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
