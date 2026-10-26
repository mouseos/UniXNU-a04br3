/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Original GetWithIOCTL case103 uses the1164byte result carrier and inline
 * IE capacity1024, with request version zero after bzero. Metadata comes from
 * real currentRecord, NOT selection of an arbitrary scanned AP. RSSI and AGE
 * retain actual last-observation provenance; no zero-RSSI or refreshed age.
 */
#ifndef A04BR3_NATIVE_CURRENT_NETWORK_H
#define A04BR3_NATIVE_CURRENT_NETWORK_H
#include <stdint.h>
#include <string.h>
#include <sys/errno.h>
#include "A04BR3NativeScanResults.h"
#include "../sdio/A04BR3WLANScanSnapshot.h"
struct a04_current_network_work {
 struct a04_wlan_scan_record raw;
 struct a04_scan_observation record;
 uint8_t wire[1164];
};
/* Heap work supplied by caller; stage output so an error exposes no partial
 * response. Version1 direct requests are compatible; version0 is observed. */
static inline int a04_native_current_encode(struct a04_current_network_work *work,
 uint64_t now,void *out,uint32_t *bytes)
{
 if(!work || !out || !bytes || *bytes!=1164)return EINVAL;
 const uint8_t *p=(const uint8_t *)out;
 if(a04_scan_result_u32(p)>1 || ((unsigned)p[0x8a]|((unsigned)p[0x8b]<<8))!=1024)return EINVAL;
 const struct a04_wlan_scan_record *raw=&work->raw;
 if(raw->observation.ie_bytes>1024)return EMSGSIZE;
 if(raw->ap.ssid_length>32 || raw->ap.rate_count>sizeof(raw->ap.rates))return EINVAL;
 struct a04_scan_observation *r=&work->record;*r={};
 r->generation=raw->observation.generation;r->seen_us=raw->observation.observed_us;
 r->channel=raw->ap.channel;r->band=raw->ap.band;r->beacon=raw->ap.beacon;r->capability=raw->ap.capability;
 r->rcpi_valid=raw->ap.rcpi_valid;r->rcpi=raw->ap.rcpi;r->from_probe=raw->observation.from_probe;
 r->ssid_length=(uint8_t)raw->ap.ssid_length;r->rate_count=(uint8_t)raw->ap.rate_count;
 memcpy(r->bssid,raw->ap.mac,6);memcpy(r->ssid,raw->ap.ssid,r->ssid_length);
 memcpy(r->rates,raw->ap.rates,r->rate_count);r->ie_length=(uint16_t)raw->observation.ie_bytes;
 memcpy(r->ies,raw->observation.ies,r->ie_length);
 memset(work->wire,0,sizeof(work->wire));work->wire[0]=1;work->wire[0x8b]=4;
 struct a04_scan_snapshot snapshot={r->generation,now,1,1};uint32_t length=1164;
 int error=a04_scan_result_encode(&snapshot,r,work->wire,&length);if(error)return error;
 memcpy(out,work->wire,1164);return 0;
}
#endif
