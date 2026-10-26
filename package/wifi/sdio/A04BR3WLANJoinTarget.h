/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Open-join admission contract. Caller supplies the observed generation and
 * exact identity; validate under the same transportLock as station_select.
 * This is a kernel-local interface, not a new firmware or Apple wire format.
 */
#ifndef A04BR3_WLAN_JOIN_TARGET_H
#define A04BR3_WLAN_JOIN_TARGET_H
#include "A04BR3WLANScanSnapshot.h"
struct a04_wlan_join_target {
 unsigned generation,index,ssid_length,channel,band;
 uint8_t bssid[6],ssid[32];
};
#endif
