// SPDX-License-Identifier: BSD-2-Clause-Patent
#ifndef A04BR3_BOOT_MEMORY_QUEUE_SNAPSHOT_H
#define A04BR3_BOOT_MEMORY_QUEUE_SNAPSHOT_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
// Supplemental raw XNU counters; existing boot_memory_snapshot(out[7]) is
// unchanged. Page counts, not bytes; independent reads are approximate.
// Throttled pages are omitted from raw inactive/wired on the iOS target.
// lopage_free is the low-page reserve. pageable_internal/external classify
// pageable pages; they are NOT extra disjoint queues to add to active/inactive.
// Compressor mode and config flags describe policy, not how many pages have
// been compressed. A zero compressor page count alone cannot identify policy.
struct boot_memory_queue_record {
  uint64_t throttled, lopage_free, pageable_internal, pageable_external;
  int32_t compressor_mode;
  uint32_t compressor_present, compressor_active;
  uint32_t swap_present, swap_active, freezer_swap_active;
};
// Caller uses the existing default-off stacklog gate. No locks, allocation,
// queue traversal, MMIO or logging. Null is allowed; every field is initialized.
void boot_memory_queue_snapshot(struct boot_memory_queue_record *out);
#ifdef __cplusplus
}
#endif
#endif
