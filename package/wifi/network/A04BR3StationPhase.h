/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Map actual locked MT6630 station state to the network snapshot. No guessed
 * firmware ABI; this reads the existing source-owned host state machine.
 * SDIO owner calls while holding transportLock, after bzero(out).
 */
#ifndef A04BR3_STATION_PHASE_H
#define A04BR3_STATION_PHASE_H
#include "A04BR3NetworkBackend.h"
#include "../sdio/A04BR3WLANStation.h"
static inline enum a04_network_station_phase a04_network_phase_from_station(const struct a04_wlan_station *s)
{
    if(!s || !s->configured || s->failed)return A04_NET_PHASE_UNKNOWN;
    if(s->sta_active && s->join.phase==4)return A04_NET_PHASE_ASSOCIATED;
    if(s->scan.pending)return A04_NET_PHASE_SCAN;
    if(!s->selected && !s->join.phase && !s->sta_pending && !s->sta_active)
        return A04_NET_PHASE_IDLE;
    if(!s->selected)return A04_NET_PHASE_UNKNOWN;
    if(s->join.phase==1)return A04_NET_PHASE_AUTH;
    // Auth response accepted (2), assoc request sent (3), or assoc response
    // accepted but still awaiting real ACTIVATE_STA_REC (4): not RUN yet.
    if(s->join.phase>=2 && s->join.phase<=4)return A04_NET_PHASE_ASSOC;
    return A04_NET_PHASE_UNKNOWN; // e.g. channel grant before auth submission
}
/* Do not promote command TX, AP response alone, or secure association to
 * native join success. Security/key/native supplicant integration is absent. */
static inline enum a04_network_join_state a04_network_join_from_station(const struct a04_wlan_station *s)
{
    if(!s || !s->configured)return A04_NET_JOIN_UNKNOWN;
    if(s->failed || s->join.failed)return A04_NET_JOIN_FAILED;
    if(s->secured)return A04_NET_JOIN_UNKNOWN;
    if(!s->selected){
        if(!s->join.phase && !s->sta_pending && !s->sta_active)
            return A04_NET_JOIN_NOT_STARTED;
        return A04_NET_JOIN_UNKNOWN;
    }
    if(s->join.phase>4 || s->scan.pending)return A04_NET_JOIN_UNKNOWN;
    if(s->sta_active){
        if(s->join.phase==4 && !s->sta_pending && !s->channel_pending &&
           s->join.aid && s->join.aid<=2007)return A04_NET_JOIN_OPEN_COMPLETE;
        return A04_NET_JOIN_UNKNOWN;
    }
    if(s->join.phase==4)
        return s->sta_pending?A04_NET_JOIN_PENDING:A04_NET_JOIN_UNKNOWN;
    if(s->join.phase || s->channel_pending || s->grant_ms)
        return A04_NET_JOIN_PENDING;
    return A04_NET_JOIN_UNKNOWN;
}
#endif
