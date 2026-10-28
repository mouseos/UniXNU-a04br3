// SPDX-License-Identifier: MIT
// Opt-in, nonblocking rate limit for long WaitFence reason snapshots.
#pragma once
#include <stdint.h>
struct NativeWaitReasonSlot { uint64_t tid,fence,owner,last; uint32_t pid; };
struct NativeWaitReasonLimit {
  unsigned lock,busy,suppressed,evicted;
  uint64_t windowStart,windowTicks,perFenceTicks,minTicks;
  unsigned used;
  NativeWaitReasonSlot slots[16];
};
static inline bool native_wait_reason_claim(NativeWaitReasonLimit *s,uint32_t pid,uint64_t tid,uint64_t fence,
                                            uint64_t owner,uint64_t start,uint64_t now) {
  if (!pid || !tid || !owner || !start || now<start || now-start<s->minTicks) return false;
  unsigned expected=0;
  if (!__atomic_compare_exchange_n(&s->lock,&expected,1,false,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED)) {
    __atomic_fetch_add(&s->busy,1u,__ATOMIC_RELAXED);return false;
  }
  if (!s->windowStart || (now>=s->windowStart && now-s->windowStart>=s->windowTicks)) {
    s->windowStart=now;s->used=0;
  }
  int match=-1,freeSlot=-1,oldest=0;
  for (unsigned i=0;i<16;i++) {
    auto &p=s->slots[i];
    if (p.pid==pid && p.tid==tid && p.fence==fence && p.owner==owner) match=int(i);
    if (!p.pid && freeSlot<0) freeSlot=int(i);
    if (p.last<s->slots[oldest].last) oldest=int(i);
  }
  bool allowed=now>=s->windowStart && s->used<2;
  if (match>=0) {
    const uint64_t last=s->slots[match].last;
    if (now<last || now-last<s->perFenceTicks) allowed=false;
  }
  if (allowed) {
    const int i=match>=0?match:(freeSlot>=0?freeSlot:oldest);
    if (match<0 && freeSlot<0) __atomic_fetch_add(&s->evicted,1u,__ATOMIC_RELAXED);
    s->slots[i]={tid,fence,owner,now,pid};++s->used;
  } else __atomic_fetch_add(&s->suppressed,1u,__ATOMIC_RELAXED);
  __atomic_store_n(&s->lock,0u,__ATOMIC_RELEASE);return allowed;
}
