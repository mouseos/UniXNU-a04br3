// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
extern "C" void a04br3_registers_init(void);
extern "C" bool a04br3_infra_update(volatile uint8_t *, unsigned mask, unsigned bits);

// Nonblocking serialization for every known board WACS2 user.
extern "C" bool a04br3_pwrap_try_lock(void);
extern "C" void a04br3_pwrap_unlock(void);
class A04BR3PwrapGuard {
  bool held;
public:
  A04BR3PwrapGuard() : held(a04br3_pwrap_try_lock()) {}
  ~A04BR3PwrapGuard() { if (held) a04br3_pwrap_unlock(); }
  explicit operator bool() const { return held; }
  A04BR3PwrapGuard(const A04BR3PwrapGuard &) = delete;
  A04BR3PwrapGuard &operator=(const A04BR3PwrapGuard &) = delete;
};
