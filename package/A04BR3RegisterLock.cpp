// SPDX-License-Identifier: MIT
// One short lock for every board writer of INFRACFG+0x80 (I2C0 and I2C2).
// Allocated synchronously before either input or charger thread can start.
#include <IOKit/IOLib.h>
#include <machine/machine_routines.h>
#include "A04BR3RegisterLock.h"
static IOLock *infraLock;
static IOLock *wacsLock;
static bool wacsPoisoned;
extern "C" void a04br3_registers_init(void) {
  infraLock = IOLockAlloc();
  wacsLock = IOLockAlloc();
}
extern "C" bool a04br3_wacs_lock(void) {
  if (!wacsLock || ml_at_interrupt_context() || !ml_get_interrupts_enabled()) return false;
  IOLockLock(wacsLock);
  if (wacsPoisoned) { IOLockUnlock(wacsLock); return false; }
  return true;
}
extern "C" bool a04br3_wacs_try_lock(void) {
  if (!wacsLock || ml_at_interrupt_context() || !ml_get_interrupts_enabled() || !IOLockTryLock(wacsLock)) return false;
  if (wacsPoisoned) { IOLockUnlock(wacsLock); return false; }
  return true;
}
extern "C" void a04br3_wacs_poison(void) { wacsPoisoned = true; }
extern "C" void a04br3_wacs_unlock(void) { IOLockUnlock(wacsLock); }
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
