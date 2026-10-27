// SPDX-License-Identifier: BSD-2-Clause-Patent
// Bounded IRQ histogram; caller filters PID and supplies stable thread identity.
#ifndef A04BR3_BOOT_IRQ_CORRELATION_H
#define A04BR3_BOOT_IRQ_CORRELATION_H
#include <stdint.h>
#include <string.h>
#define UIRQ_SLOTS 32u
#define UIRQ_RATE 64u
struct uirq_record { uint64_t tid, pc, hits; unsigned user, version; };
struct uirq_state {
 unsigned lock;
 uint64_t ticks, sampled, busy, wrong_uid, uid, full;
 struct uirq_record slots[UIRQ_SLOTS];
};
static inline int uirq_due(struct uirq_state *s) {
 return __atomic_fetch_add(&s->ticks,1,__ATOMIC_RELAXED) % UIRQ_RATE == 0;
}
// IRQ context: never wait, allocate, print, or follow stack pointers. Counters
// use atomics; try-lock prevents interrupting a reporting CPU from deadlocking.
static inline void uirq_record(struct uirq_state *s, uint64_t uid, uint64_t tid,
                               uint64_t pc, unsigned user, unsigned version) {
 if (!pc || !uid || !tid) return;
 uint64_t expected=0;
 __atomic_compare_exchange_n(&s->uid,&expected,uid,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);
 if (__atomic_load_n(&s->uid,__ATOMIC_ACQUIRE)!=uid) {
  __atomic_fetch_add(&s->wrong_uid,1,__ATOMIC_RELAXED);return;
 }
 __atomic_fetch_add(&s->sampled,1,__ATOMIC_RELAXED);
 pc &= ~255ULL;
 if (__atomic_exchange_n(&s->lock,1,__ATOMIC_ACQUIRE)) {
  __atomic_fetch_add(&s->busy,1,__ATOMIC_RELAXED);return;
 }
 unsigned start=(unsigned)((tid ^ (pc>>8) ^ version ^ user)%UIRQ_SLOTS);
 for(unsigned i=0;i<UIRQ_SLOTS;++i) {
  struct uirq_record *r=&s->slots[(start+i)%UIRQ_SLOTS];
  if (!r->hits) {r->tid=tid;r->pc=pc;r->user=user;r->version=version;}
  if (r->tid==tid && r->pc==pc && r->version==version && r->user==user) {
   ++r->hits;__atomic_store_n(&s->lock,0,__ATOMIC_RELEASE);return;
  }
 }
 ++s->full;
 __atomic_store_n(&s->lock,0,__ATOMIC_RELEASE);
}
// Single worker only; a failed try produces no partial snapshot.
static inline int uirq_take(struct uirq_state *s, struct uirq_record out[UIRQ_SLOTS], uint64_t *full, uint64_t *at, uint64_t (*clock_now)(void)) {
 if (__atomic_exchange_n(&s->lock,1,__ATOMIC_ACQUIRE)) return 0;
 *at=clock_now();
 memcpy(out,s->slots,sizeof(s->slots));memset(s->slots,0,sizeof(s->slots));
 *full=s->full;s->full=0;
 __atomic_store_n(&s->lock,0,__ATOMIC_RELEASE);return 1;
}
#endif
