// SPDX-License-Identifier: MIT
// Bounded best-effort per-thread totals, including short successful I/O.
#pragma once
#include "NativePowerVRIoTrace.h"
struct NativeIoAggregateRow {
 uint64_t tid,first,last,count[3],ticks[3],errors[3],reported;
 uint32_t pid;
};
struct NativeIoAggregate {
 unsigned lock,busy,full,cursor;
 NativeIoAggregateRow rows[16];
};
static inline bool native_io_aggregate_lock(NativeIoAggregate *a) {
 unsigned expected=0;
 if (__atomic_compare_exchange_n(&a->lock,&expected,1u,false,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED)) return true;
 __atomic_fetch_add(&a->busy,1u,__ATOMIC_RELAXED); return false;
}
static inline void native_io_aggregate_record(NativeIoAggregate *a,const NativeIoRecord &r) {
 if (!r.pid || !r.tid || !r.start || r.end<r.start || r.kind<1 || r.kind>3) return;
 if (!native_io_aggregate_lock(a)) return;
 NativeIoAggregateRow *row=nullptr,*freeRow=nullptr;
 for (auto &entry:a->rows) {
  if (entry.pid==r.pid && entry.tid==r.tid) row=&entry;
  if (!entry.pid && !freeRow) freeRow=&entry;
 }
 if (!row && freeRow) {row=freeRow;row->pid=r.pid;row->tid=r.tid;row->first=r.start;}
 if (row) {
  const unsigned k=r.kind-1;row->count[k]++;row->ticks[k]+=r.end-r.start;
  if (r.result) row->errors[k]++;
  if (r.end>row->last)row->last=r.end;
 } else __atomic_fetch_add(&a->full,1u,__ATOMIC_RELAXED);
 __atomic_store_n(&a->lock,0u,__ATOMIC_RELEASE);
}
static inline unsigned native_io_aggregate_take(NativeIoAggregate *a,NativeIoAggregateRow out[2]) {
 if (!native_io_aggregate_lock(a)) return 0;
 unsigned found=0;
 for (unsigned n=0;n<16 && found<2;n++) {
  unsigned index=(a->cursor+n)%16; auto &r=a->rows[index];
  uint64_t count=r.count[0]+r.count[1]+r.count[2];
  if (r.pid && count!=r.reported) {out[found++]=r;r.reported=count;}
 }
 a->cursor=(a->cursor+2)%16;
 __atomic_store_n(&a->lock,0u,__ATOMIC_RELEASE);return found;
}
