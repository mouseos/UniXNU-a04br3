// SPDX-License-Identifier: MIT
#pragma once

// A single opt-in boot-time CPU-copy observation, not a DRAM bandwidth test.
// Page counters are independent snapshots, and the reserve is not a guarantee.
static void a04br3_early_copy_probe()
{
  unsigned enabled = 0;
  if (!PE_parse_boot_argn("ecb", &enabled, sizeof(enabled)) || enabled != 1) return;
  constexpr size_t bytes = 256 * 1024;
  constexpr uint64_t minimum = 32 * 1024 * 1024 + 2 * bytes;
  uint64_t before[7] = {};
  boot_memory_snapshot(before);
  IOLog("a04br3 early-copy phase=memory page=%llu free=%llu active=%llu inactive=%llu wired=%llu compressed=%llu speculative=%llu snapshot=non_atomic\n",
        (unsigned long long)before[0], (unsigned long long)before[1], (unsigned long long)before[2],
        (unsigned long long)before[3], (unsigned long long)before[4], (unsigned long long)before[5], (unsigned long long)before[6]);
  if (!before[0] || before[0] > 65536 || before[6] > before[1]) {
    IOLog("a04br3 early-copy phase=skip reason=invalid_snapshot\n"); return;
  }
  const uint64_t neededPages = minimum / before[0] + ((minimum % before[0]) != 0);
  if (before[1] - before[6] < neededPages) {
    IOLog("a04br3 early-copy phase=skip reason=insufficient_unoccupied reserve_bytes=%llu\n", (unsigned long long)minimum); return;
  }
  auto *src = static_cast<unsigned char *>(IOMalloc(bytes));
  if (!src) { IOLog("a04br3 early-copy phase=skip reason=source_allocation\n"); return; }
  auto *dst = static_cast<unsigned char *>(IOMalloc(bytes));
  if (!dst) { IOFree(src, bytes); IOLog("a04br3 early-copy phase=skip reason=destination_allocation\n"); return; }
  for (size_t i = 0; i < bytes; ++i) src[i] = static_cast<unsigned char>((i * 17u + (i >> 12)) & 255u);
  volatile unsigned char *touch = dst;
  for (size_t i = 0; i < bytes; i += 4096) touch[i] = 0;
  touch[bytes - 1] = 0;
  uint64_t start = mach_absolute_time();
  memcpy(dst, src, bytes);
  uint64_t end = mach_absolute_time(), elapsed = 0;
  bool verified = memcmp(src, dst, bytes) == 0;
  if (end > start) absolutetime_to_nanoseconds(end - start, &elapsed);
  if (verified && elapsed) {
    IOLog("a04br3 early-copy phase=result synthetic=1 context=bsd_init bytes=%llu elapsed_ns=%llu verified=1 scope=cpu_copy snapshot=non_atomic\n",
          (unsigned long long)bytes, (unsigned long long)elapsed);
  } else {
    IOLog("a04br3 early-copy phase=error reason=%s\n", verified ? "invalid_elapsed" : "verification_failed");
  }
  IOFree(dst, bytes);
  IOFree(src, bytes);
  uint64_t after[7] = {};
  boot_memory_snapshot(after);
  IOLog("a04br3 early-copy phase=after page=%llu free=%llu active=%llu inactive=%llu wired=%llu compressed=%llu speculative=%llu snapshot=non_atomic\n",
        (unsigned long long)after[0], (unsigned long long)after[1], (unsigned long long)after[2],
        (unsigned long long)after[3], (unsigned long long)after[4], (unsigned long long)after[5], (unsigned long long)after[6]);
}
