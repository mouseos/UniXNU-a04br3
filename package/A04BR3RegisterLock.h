// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
extern "C" void a04br3_registers_init(void);
extern "C" bool a04br3_infra_update(volatile uint8_t *, unsigned mask, unsigned bits);
// Thread context only. Covers an entire WACS2 transaction or read/modify/write.
extern "C" bool a04br3_wacs_lock(void);
extern "C" bool a04br3_wacs_try_lock(void);
extern "C" void a04br3_wacs_unlock(void);
// Called only by the owner after an issued read whose completion is uncertain.
extern "C" void a04br3_wacs_poison(void);

class A04BR3WacsGuard {
  bool held;
public:
  explicit A04BR3WacsGuard(bool tryOnly = false)
    : held(tryOnly ? a04br3_wacs_try_lock() : a04br3_wacs_lock()) {}
  ~A04BR3WacsGuard() { if (held) a04br3_wacs_unlock(); }
  explicit operator bool() const { return held; }
  A04BR3WacsGuard(const A04BR3WacsGuard &) = delete;
  A04BR3WacsGuard &operator=(const A04BR3WacsGuard &) = delete;
};
