// SPDX-License-Identifier: MIT
// Whether a process name is in a comma-separated list of process names, with the rule of the kernel's sandbox grant
// (UniXNU patches/xnu/zzw-native-adreno-sandbox-grant.patch, native_sandbox_grant): a process name holds 16 characters, so a longer
// entry is compared by its first 16. Shared by the accelerator's plugin-name visibility (NativePowerVRMetal.cpp) and its host test.
#pragma once
#include <stddef.h>
#include <string.h>

static inline bool native_powervr_name_listed(const char *list, const char *name)
{
  if (!list || !name || !name[0]) return false;
  const size_t limit = 16;
  for (const char *at = list; *at;) {
    size_t length = 0;
    while (at[length] && at[length] != ',') ++length;
    if (length && !strncmp(name, at, length < limit ? length : limit) && (length >= limit || name[length] == 0)) return true;
    at += length + (at[length] == ',' ? 1 : 0);
  }
  return false;
}

