// SPDX-License-Identifier: MIT
// Bounded try-lock accounting of completed waits. Never wait for diagnostics.
#pragma once
#include <stdint.h>
struct NativeWaitSlot { uint64_t tid,fence,first,last,sum,pendingAt; uint32_t pid,count,errors,pending,beginLogged; };
struct NativeWaitResult { uint64_t tid,fence,first,last,sum; uint32_t pid,count,errors; int32_t result; };
struct NativeWaitSeries {
 unsigned enabled,lock,busy,full,late,evicted,expiredPending,suppressedBegin,suppressedSeries;
 uint64_t threshold,windowTicks,staleTicks,pendingLeaseTicks,windowStart;
 unsigned beginBudget,seriesBudget;
 NativeWaitSlot slots[16];
};
static inline bool native_wait_lock(NativeWaitSeries *s) {
 unsigned expected=0;
 if (__atomic_compare_exchange_n(&s->lock,&expected,1,false,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED)) return true;
 __atomic_fetch_add(&s->busy,1u,__ATOMIC_RELAXED);return false;
}
static inline void native_wait_unlock(NativeWaitSeries *s) { __atomic_store_n(&s->lock,0u,__ATOMIC_RELEASE); }
static inline bool native_wait_window(NativeWaitSeries *s,uint64_t now) {
 if (!s->windowStart || (now>=s->windowStart && now-s->windowStart>=s->windowTicks)) {
  s->windowStart=now;s->beginBudget=0;s->seriesBudget=0;
 }
 if (now<s->windowStart) {__atomic_fetch_add(&s->late,1u,__ATOMIC_RELAXED);return false;}
 return true;
}
// Returns true once for a newly observed pid/tid/fence, subject to rate limit.
// The caller supplies the timestamp before IOLog; start timing the wait AFTER it.
static inline bool native_wait_begin(NativeWaitSeries *s,uint32_t pid,uint64_t tid,uint64_t fence,uint64_t now) {
 if (!__atomic_load_n(&s->enabled,__ATOMIC_ACQUIRE) || !pid || !tid || !now || !native_wait_lock(s)) return false;
 if (!native_wait_window(s,now)) {native_wait_unlock(s);return false;}
 int freeSlot=-1,match=-1;
 for (unsigned i=0;i<16;i++) {
  NativeWaitSlot &p=s->slots[i];
  if (p.pid && p.pending && now>=p.pendingAt && now-p.pendingAt>=s->pendingLeaseTicks) {p={};__atomic_fetch_add(&s->expiredPending,1u,__ATOMIC_RELAXED);}
  if (p.pid && !p.pending && now>=p.last && now-p.last>=s->staleTicks) {p={};__atomic_fetch_add(&s->evicted,1u,__ATOMIC_RELAXED);}
  if (p.pid==pid && p.tid==tid && p.fence==fence) match=int(i);
  if (!p.pid && freeSlot<0) freeSlot=int(i);
 }
 if (match>=0) {
  NativeWaitSlot &p=s->slots[match];
  if (now<p.last) {__atomic_fetch_add(&s->late,1u,__ATOMIC_RELAXED);native_wait_unlock(s);return false;}
  p.pending=1;p.pendingAt=now;native_wait_unlock(s);return false;
 }
 if (freeSlot<0) {__atomic_fetch_add(&s->full,1u,__ATOMIC_RELAXED);native_wait_unlock(s);return false;}
 NativeWaitSlot &p=s->slots[freeSlot];p={};p.pid=pid;p.tid=tid;p.fence=fence;p.last=now;p.pending=1;p.pendingAt=now;
 bool emit=s->beginBudget<4;
 if (emit) ++s->beginBudget; else __atomic_fetch_add(&s->suppressedBegin,1u,__ATOMIC_RELAXED);
 p.beginLogged=emit;
 native_wait_unlock(s);return emit;
}
// Completed events only; sum excludes gaps and synchronous reporting overhead.
static inline bool native_wait_end(NativeWaitSeries *s,uint32_t pid,uint64_t tid,uint64_t fence,
                                   uint64_t start,uint64_t end,int32_t result,bool terminal,NativeWaitResult *out) {
 if (!__atomic_load_n(&s->enabled,__ATOMIC_ACQUIRE) || !pid || !tid || !start || end<start || !native_wait_lock(s)) return false;
 if (!native_wait_window(s,end)) {native_wait_unlock(s);return false;}
 NativeWaitSlot *slot=nullptr;
 for (unsigned i=0;i<16;i++) if(s->slots[i].pid==pid && s->slots[i].tid==tid && s->slots[i].fence==fence) {slot=&s->slots[i];break;}
 if (!slot || start<slot->last) {__atomic_fetch_add(&s->late,1u,__ATOMIC_RELAXED);native_wait_unlock(s);return false;}
 NativeWaitSlot &p=*slot;uint64_t delta=end-start;
 if (!p.count) p.first=start;
 if (~uint64_t(0)-p.sum<delta || p.count==~uint32_t(0)) {__atomic_fetch_add(&s->late,1u,__ATOMIC_RELAXED);p={};native_wait_unlock(s);return false;}
 p.last=end;p.pending=0;p.sum+=delta;++p.count;if(result && p.errors!=~uint32_t(0))++p.errors;
 bool emit=false;
 if (p.sum>=s->threshold || (terminal && p.beginLogged)) {
  if (s->seriesBudget<4) {
   ++s->seriesBudget;*out={p.tid,p.fence,p.first,p.last,p.sum,p.pid,p.count,p.errors,result};emit=true;
   p.first=0;p.sum=0;p.count=0;p.errors=0;
  } else __atomic_fetch_add(&s->suppressedSeries,1u,__ATOMIC_RELAXED);
 }
 if (terminal) p={};
 native_wait_unlock(s);return emit;
}
