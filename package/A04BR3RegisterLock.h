// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
extern "C" void a04br3_registers_init(void);
extern "C" bool a04br3_infra_update(volatile uint8_t *, unsigned mask, unsigned bits);
