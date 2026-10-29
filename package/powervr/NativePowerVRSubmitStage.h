// SPDX-License-Identifier: MIT
// Bounded, best-effort per-thread completed submit intervals. All ticks are
// wall intervals; kick includes kccb and total includes every other interval.
#pragma once
#include <stdint.h>
enum { NativeSubmitTotal, NativeSubmitCreate, NativeSubmitPte, NativeSubmitLock,
       NativeSubmitSpace, NativeSubmitRelock, NativeSubmitBo, NativeSubmitKick, NativeSubmitKccb, NativeSubmitStages };
struct NativeSubmitSample { uint32_t pid; bool success; uint64_t tid, first, last, ticks[NativeSubmitStages]; };
struct NativeSubmitRow { NativeSubmitSample sample; uint64_t count, succeeded, failed, reported; };
struct NativeSubmitStage { unsigned lock, busy, reportBusy, full, cursor; NativeSubmitRow rows[16]; };
static inline bool native_submit_stage_lock(NativeSubmitStage *s, bool report) {
 unsigned expected=0;
 if (__atomic_compare_exchange_n(&s->lock,&expected,1u,false,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED)) return true;
 __atomic_fetch_add(report?&s->reportBusy:&s->busy,1u,__ATOMIC_RELAXED); return false;
}
static inline void native_submit_stage_record(NativeSubmitStage *s,const NativeSubmitSample &v) {
 if (!v.pid || !v.tid || !v.first || v.last<v.first) return;
 if (!native_submit_stage_lock(s,false)) return;
 NativeSubmitRow *row=nullptr,*freeRow=nullptr;
 for (auto &r:s->rows) { if (r.sample.pid==v.pid && r.sample.tid==v.tid) row=&r; if (!r.sample.pid && !freeRow) freeRow=&r; }
 if (!row && freeRow) {row=freeRow;row->sample.pid=v.pid;row->sample.tid=v.tid;row->sample.first=v.first;}
 if (!row) __atomic_fetch_add(&s->full,1u,__ATOMIC_RELAXED);
 else { ++row->count; if (v.success) ++row->succeeded; else ++row->failed; if (v.last>row->sample.last) row->sample.last=v.last; for(unsigned i=0;i<NativeSubmitStages;i++) row->sample.ticks[i]+=v.ticks[i]; }
 __atomic_store_n(&s->lock,0u,__ATOMIC_RELEASE);
}
static inline unsigned native_submit_stage_take(NativeSubmitStage *s,NativeSubmitRow out[2]) {
 if (!native_submit_stage_lock(s,true)) return 0;
 unsigned n=0;
 for(unsigned i=0;i<16 && n<2;i++) { auto &r=s->rows[(s->cursor+i)%16]; if(r.count!=r.reported) {out[n++]=r;r.reported=r.count;} }
 s->cursor=(s->cursor+2)%16;
 __atomic_store_n(&s->lock,0u,__ATOMIC_RELEASE);return n;
}
