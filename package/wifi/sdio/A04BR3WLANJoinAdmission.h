/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#ifndef A04BR3_WLAN_JOIN_ADMISSION_H
#define A04BR3_WLAN_JOIN_ADMISSION_H
#include "A04BR3WLANJoinTarget.h"
#include "A04BR3WLANRuntime.h"
#include <sys/errno.h>
static inline int a04_wlan_join_target_check_mode(const struct a04_wlan_runtime *r,
 const struct a04_wlan_join_target *t,uint64_t now,unsigned personal)
{
 if(!r || !t || !t->generation || !t->ssid_length || t->ssid_length>32 ||
    !a04_wlan_mac_valid(t->bssid))return EINVAL;
 if(!r->configured || !r->admin || r->failed || !r->station.configured ||
    r->station.failed || r->station.join.failed)return ENETDOWN;
 if(t->generation!=r->scan_generation)return ESTALE;
 if(r->teardown.pending || r->station.selected || r->station.scan.pending)return EBUSY;
 if(!r->station.scan.complete)return EAGAIN;
 if(r->scan_count>16 || t->index>=r->scan_count)return ENOENT;
 const struct a04_wlan_ap *a=&r->scan[t->index];
 const struct a04_wlan_scan_extra *x=&r->scan_extra[t->index];
 if(x->generation!=t->generation || a->ssid_length!=t->ssid_length ||
    a->channel!=t->channel || a->band!=t->band ||
    !a04_wlan_equal(a->mac,t->bssid,6) ||
    !a04_wlan_equal(a->ssid,t->ssid,t->ssid_length))return ESTALE;
 if(now<x->observed_us || now-x->observed_us>=10000000)return EAGAIN;
 if(personal){if(!(a->capability&16) || a04_wlan_rsn_psk_ccmp(a->rsn,a->rsn_length))return EOPNOTSUPP;}
 else if((a->capability&16) || a->rsn_length)return EOPNOTSUPP;
 if(a->band!=1 || !a->channel || a->channel>13 ||
    !(r->station.local.permitted_channels&(1u<<a->channel)))return EOPNOTSUPP;
 return 0;
}
static inline int a04_wlan_join_target_check(const struct a04_wlan_runtime *r,const struct a04_wlan_join_target *t,uint64_t now){return a04_wlan_join_target_check_mode(r,t,now,0);}
static inline int a04_wlan_personal_target_check(const struct a04_wlan_runtime *r,const struct a04_wlan_join_target *t,uint64_t now){return a04_wlan_join_target_check_mode(r,t,now,1);}
#endif
