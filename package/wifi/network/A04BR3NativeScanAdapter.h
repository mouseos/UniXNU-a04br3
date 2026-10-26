/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Real production SDIO snapshot/copy_record adapter. Uses Popper's existing
 * source API, not a speculative parallel snapshot contract or placeholder.
 */
#ifndef A04BR3_NATIVE_SCAN_ADAPTER_H
#define A04BR3_NATIVE_SCAN_ADAPTER_H
#include "A04BR3NativeScanDispatch.h"
#include "../sdio/A04BR3WLANBackendAPI.h"
#include <kern/clock.h>
static inline uint64_t a04_scan_provider_now()
{
    clock_sec_t seconds;clock_usec_t usec;clock_get_system_microtime(&seconds,&usec);
    return (uint64_t)seconds*1000000+usec;
}
static inline int a04_scan_provider_info(IOService *provider,struct a04_wlan_scan_info *info)
{
    int error=a04br3_wlan_scan_snapshot(provider,info);
    if(!error && info->error)error=EIO;
    return error;
}
static inline int a04_scan_provider_snapshot(void *context,uint64_t expected,unsigned index,
    struct a04_scan_snapshot *snapshot,struct a04_scan_observation *record)
{
    auto *provider=(IOService *)context;
    if(!provider || !snapshot)return ENXIO;
    a04_network_backend backend={};
    int error=a04br3_wlan_acquire_backend(provider,&backend);if(error)return error;
    a04_network_state state={};error=backend.state(backend.context,&state);
    if(!error && !a04_network_control_ready(&state))error=ENETDOWN;
    a04_wlan_scan_info info={};
    if(!error)error=a04_scan_provider_info(provider,&info);
    if(!error && expected && expected!=info.generation)error=ESTALE;
    a04_wlan_scan_record *raw=nullptr;
    if(!error && record){
        if(!info.complete)error=EAGAIN;
        else if(index>=info.count)error=ENOENT;
        else{
            raw=(a04_wlan_scan_record *)IOMallocZero(sizeof(*raw));
            if(!raw)error=ENOMEM;
            else error=a04br3_wlan_scan_copy_record(provider,info.generation,index,raw);
            // copy_record deliberately uses EAGAIN for pending or replacement.
            // Re-read actual metadata to distinguish replacement from pending.
            a04_wlan_scan_info after={};int status=a04_scan_provider_info(provider,&after);
            if(status)error=status;
            else if(after.generation!=info.generation)error=ESTALE;
            else if(!after.complete)error=EAGAIN;
            else if(!error && raw->observation.generation!=info.generation)error=ESTALE;
            if(!error && raw->observation.ie_bytes>1024)error=EMSGSIZE;
            if(!error && (raw->ap.ssid_length>32 || raw->ap.rate_count>sizeof(raw->ap.rates)))error=EINVAL;
        }
    }
    if(!error){
        *snapshot={info.generation,a04_scan_provider_now(),info.count,info.complete};
        if(record){
            *record={};record->generation=raw->observation.generation;
            record->seen_us=raw->observation.observed_us;
            record->channel=raw->ap.channel;record->band=raw->ap.band;
            record->beacon=raw->ap.beacon;record->capability=raw->ap.capability;
            record->rcpi_valid=raw->ap.rcpi_valid;record->rcpi=raw->ap.rcpi;
            record->from_probe=raw->observation.from_probe;
            memcpy(record->bssid,raw->ap.mac,6);record->ssid_length=(uint8_t)raw->ap.ssid_length;
            record->rate_count=(uint8_t)raw->ap.rate_count;
            memcpy(record->ssid,raw->ap.ssid,record->ssid_length);
            memcpy(record->rates,raw->ap.rates,record->rate_count);
            record->ie_length=(uint16_t)raw->observation.ie_bytes;
            memcpy(record->ies,raw->observation.ies,record->ie_length);
        }
    }
    if(raw)IOFree(raw,sizeof(*raw));
    a04br3_wlan_release_backend(provider);return error;
}
static inline int a04_scan_provider_begin(void *context,unsigned active,uint64_t *generation)
{
    auto *provider=(IOService *)context;
    if(!provider || !generation || active>1)return EINVAL;
#if !defined(A04_WLAN_SCAN_MODE_API) || A04_WLAN_SCAN_MODE_API != 1
#error "Native scan requires real SDIO mode API1 with atomic accepted generation"
#endif
    unsigned accepted=0;int error=a04br3_wlan_scan_mode(provider,active,&accepted);
    if(!error){if(!accepted)return EIO;*generation=accepted;}
    return error;
}
#endif
