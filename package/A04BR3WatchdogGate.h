// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stdint.h>

// Coordinates this board's normal feeder with a diagnostic probe. It does
// not control direct reset, firmware or other watchdog agents. No waiting in
// either path: a contended operation must be retried by its caller.
struct A04BR3WatchdogGate {
  uint32_t busy;
  bool lock() {
    uint32_t expected = 0;
    return __atomic_compare_exchange_n(&busy, &expected, 1, false, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED);
  }
  void unlock() { __atomic_store_n(&busy, 0, __ATOMIC_RELEASE); }
  bool begin(volatile uint32_t *active) {
    if (!lock()) return false;
    bool ok = !__atomic_load_n(active, __ATOMIC_ACQUIRE);
    if (ok) __atomic_store_n(active, 1U, __ATOMIC_RELEASE);
    unlock();
    return ok;
  }
  template<typename F> bool feed(volatile uint32_t *active, bool permitted, F write) {
    if (!lock()) return false;
    bool ok = permitted && !__atomic_load_n(active, __ATOMIC_ACQUIRE);
    if (ok) write();
    unlock();
    return ok;
  }
};
