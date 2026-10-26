/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Original internal normalized codec record, not an Apple wire or SDIO ABI.
 * Adapter consumes the real SDIO snapshot header. SDIO owns capture/generation.
 */
#ifndef A04BR3_SCAN_OBSERVATION_H
#define A04BR3_SCAN_OBSERVATION_H
#include <stdint.h>
struct a04_scan_snapshot {
    uint64_t generation,now_us;
    uint32_t count,complete;
};
struct a04_scan_observation {
    uint64_t generation,seen_us;
    uint32_t channel,band,beacon,capability,rcpi_valid,rcpi;
    uint32_t from_probe,ie_truncated;
    uint8_t bssid[6],ssid_length,rate_count,ssid[32],rates[15];
    uint16_t ie_length;
    uint8_t ies[1024];
};
/* Internal normalized network record. SDIO ABI remains the existing
 * ../sdio/A04BR3WLANScanSnapshot.h; adapter converts actual snapshot/copy_record.
 */
#endif
