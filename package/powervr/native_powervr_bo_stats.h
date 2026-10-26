/* SPDX-License-Identifier: MIT */
#ifndef NATIVE_POWERVR_BO_STATS_H
#define NATIVE_POWERVR_BO_STATS_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Object lifetime, NOT retained physical backing or per-process footprint.
 * Normal allocations exclude JobsGem/FW/PT allocations. Host imports (including
 * scanout references) are separate and may overlap the same physical bytes.
 * Relaxed atomic snapshots are approximate, not a transactional tuple.
 * Default off; exact a04br3-stacklog=1 sampled before service publication.
 * No user ABI method or hot-path logging. Caller may report at low frequency. */
struct native_powervr_bo_live_class {
  uint64_t count, bytes, peak_count, peak_bytes;
};
struct native_powervr_bo_live_record {
  uint64_t enabled;
  struct native_powervr_bo_live_class normal, host_import;
};
/* Always initializes out; null is allowed. Zero before enabled publication. */
void native_powervr_bo_live_snapshot(struct native_powervr_bo_live_record *out);
#ifdef __cplusplus
}
#endif
#endif
