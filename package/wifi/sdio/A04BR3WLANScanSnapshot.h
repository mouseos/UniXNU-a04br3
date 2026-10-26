/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Observed scan snapshot contract for native owner. No native wire synthesis.
 * All IE bytes are copied from validated received beacon/probe response.
 */
#ifndef A04BR3_WLAN_SCAN_SNAPSHOT_H
#define A04BR3_WLAN_SCAN_SNAPSHOT_H
#include "A04BR3WLANStation.h"
enum { A04_WLAN_SCAN_MAX_IE=2304-36, A04_WLAN_SCAN_PASSIVE=0, A04_WLAN_SCAN_ACTIVE=1 };
struct a04_wlan_scan_info {
 unsigned generation,count,complete,dropped;
 uint64_t started_us,completed_us;
 int error;unsigned mode; /* actual accepted RF mode, not native cached policy3 */
};
struct a04_wlan_scan_extra {
 uint64_t observed_us;unsigned generation,from_probe,ie_bytes;
 uint8_t ies[A04_WLAN_SCAN_MAX_IE];
};
struct a04_wlan_scan_record {
 struct a04_wlan_ap ap;
 struct a04_wlan_scan_extra observation;
};
/* Caller supplies generation from info. Copy only completed immutable records;
 * next scan changes generation even if it reuses the same wire sequence/index.
 * Full RX IEs may exceed native1024 capacity: native encoder must return an
 * explicit size error rather than truncate or reconstruct security IEs.
 * AGE in native ABI is ms: max(0,now-observed_us)/1000, timestamp is monotonic.
 */
#endif
