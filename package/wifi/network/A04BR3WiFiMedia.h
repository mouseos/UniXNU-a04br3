/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Real MT6630 WiFi media discovery using XNU8796 ifmediareq32/64 and IFM_*.
 * Donor __getIfListCopy gates native binding on SIOCGIFMEDIA success and
 * (ifm_current & IFM_NMASK)==IFM_IEEE80211, before any native GET12 dispatch.
 * REA ev_7ac6514e5ade14fdb95d1982bdf4869575d3402dce1d00bd6c0837ba064bf4ea.
 * Only AUTO selection is supported. No negotiated fixed rate, carrier or
 * association is invented: ACTIVE requires observed radio + real data_ready.
 */
#ifndef A04BR3_WIFI_MEDIA_H
#define A04BR3_WIFI_MEDIA_H
#include "A04BR3Apple80211IOCTL.h"
#include <net/if.h>
#include <net/if_media.h>
#include <sys/sockio.h>
template<class Request> static inline int a04_wifi_media_get(
    const struct a04_network_state *state,Request *request,const char *name,
    const struct a04_network_copy *copy)
{
    if(!state || !request || !name)return EINVAL;
    if(request->ifm_count<0)return EINVAL;
    unsigned i=0;for(;i<16 && request->ifm_name[i];i++){
        if(!name[i] || name[i]!=request->ifm_name[i])return ENXIO;
    }
    if(i==16)return EINVAL;
    if(name[i])return ENXIO;
    if(!a04_network_control_ready(state))return ENETDOWN;
    const uint32_t automatic=IFM_IEEE80211|IFM_AUTO;
    // ifm_count is caller capacity; only one real selectable mode is exposed.
    if(request->ifm_count){
        uint64_t address=(uint64_t)request->ifmu_ulist;
        if(!address || !copy || !copy->out)return EFAULT;
        int error=copy->out(copy->context,&automatic,address,sizeof(automatic));
        if(error)return error;
    }
    const bool linked=state->radio_enabled && a04_network_data_ready(state);
    request->ifm_current=(int)automatic;request->ifm_mask=0;
    request->ifm_status=IFM_AVALID|(linked?IFM_ACTIVE:0);
    request->ifm_active=IFM_IEEE80211|(linked?IFM_AUTO:IFM_NONE);
    request->ifm_count=1;return 0;
}
/* SIOCSIFMEDIA selects a medium, not radio power or association. AUTO is the
 * sole mode already reported above. Accepting that selection is idempotent;
 * reject every rate/option we cannot actually configure. Canonical ifreq is
 * already copied into kernel memory by BSD, with privilege checked in if.c. */
static inline int a04_wifi_media_set(const struct a04_network_state *state,
    const struct ifreq *request,const char *name)
{
    if(!state || !request || !name)return EINVAL;
    unsigned i=0;for(;i<IFNAMSIZ && request->ifr_name[i];i++){
        if(!name[i] || name[i]!=request->ifr_name[i])return ENXIO;
    }
    if(i==IFNAMSIZ)return EINVAL;
    if(name[i])return ENXIO;
    if(!a04_network_control_ready(state))return ENETDOWN;
    return request->ifr_media==(IFM_IEEE80211|IFM_AUTO)?0:EOPNOTSUPP;
}
#endif
