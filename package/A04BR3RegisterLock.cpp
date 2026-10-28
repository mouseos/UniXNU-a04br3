// SPDX-License-Identifier: MIT
// One short lock for every board writer of INFRACFG+0x80 (I2C0 and I2C2).
// Allocated synchronously before either input or charger thread can start.
#include <IOKit/IOLib.h>
#include "A04BR3RegisterLock.h"
static IOLock *infraLock;
extern "C" void a04br3_registers_init(void) { infraLock = IOLockAlloc(); }
extern "C" bool a04br3_infra_update(volatile uint8_t *infra, unsigned mask, unsigned bits) {
  if (!infraLock || !infra || !mask || (mask & ~10U) || (bits & ~mask)) return false;
  IOLockLock(infraLock);
  auto reg = reinterpret_cast<volatile uint32_t *>(infra + 0x80);
  *reg = (*reg & ~mask) | bits;
#if defined(__aarch64__)
  __asm__ volatile("dsb oshst" ::: "memory");
#else
  __atomic_thread_fence(__ATOMIC_SEQ_CST); // host regression executes the real locked RMW
#endif
  bool ok = (*reg & mask) == bits;
  IOLockUnlock(infraLock);
  return ok;
}

// Atomic try-lock never sleeps or masks interrupts. A caller that finds the
// shared PMIC wrapper busy must fail and retry later, not touch its registers.
static uint32_t pwrapBusy;
extern "C" bool a04br3_pwrap_try_lock(void) {
  uint32_t expected = 0;
  return __atomic_compare_exchange_n(&pwrapBusy, &expected, 1, false, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED);
}
extern "C" void a04br3_pwrap_unlock(void) { __atomic_store_n(&pwrapBusy, 0, __ATOMIC_RELEASE); }
