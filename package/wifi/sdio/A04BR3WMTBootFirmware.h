// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
// Immutable build-supplied firmware bytes, not old bootshim reservation PAs.
#include <stdint.h>
extern "C" const uint8_t a04br3_wmt_firmware[], a04br3_wmt_firmware_end[];
enum { A04_WMT_BOOT_OK=0,A04_WMT_BOOT_ABSENT=1,A04_WMT_BOOT_RANGE=2,A04_WMT_BOOT_MAPPING=3 };
static inline int a04_wmt_boot_firmware_view(const void **view,unsigned *bytes) {
  if(!view||!bytes)return A04_WMT_BOOT_RANGE;
  *view=nullptr;*bytes=0;
  uintptr_t begin=reinterpret_cast<uintptr_t>(a04br3_wmt_firmware), end=reinterpret_cast<uintptr_t>(a04br3_wmt_firmware_end);
  if(end<begin)return A04_WMT_BOOT_RANGE;
  auto size=end-begin;
  if(size<32||size>32*1024*1024)return A04_WMT_BOOT_RANGE;
  *view=a04br3_wmt_firmware;*bytes=unsigned(size);return A04_WMT_BOOT_OK;
}
