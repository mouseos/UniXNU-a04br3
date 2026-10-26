/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Snapshot only the actually associated open peer. Scanned APs alone never
 * satisfy this API. Caller owns output and holds the real transport lock.
 */
#ifndef A04BR3_WLAN_CURRENT_NETWORK_H
#define A04BR3_WLAN_CURRENT_NETWORK_H
#include "A04BR3WLANRuntime.h"
#include <sys/errno.h>
static inline int a04_wlan_current_record(const struct a04_wlan_runtime *r,struct a04_wlan_scan_record *out)
{
 if(!r || !out)return EINVAL;
 if(!r->configured || r->failed || r->station.failed || r->station.join.failed)return ENETDOWN;
 const struct a04_wlan_station *s=&r->station;
 if(!s->selected || !s->sta_active || s->join.phase!=4)return ENOTCONN;
 if(s->secured){
  if(!r->personal_admitted || !r->association_generation || r->auth_lower!=1 || r->auth_upper!=8 || r->auth_upper_ext ||
   !(s->ap.capability&16) || a04_wlan_rsn_psk_ccmp(s->ap.rsn,s->ap.rsn_length))return EOPNOTSUPP;
 }else if((s->ap.capability&16) || s->ap.rsn_length)return EOPNOTSUPP;
 if(s->sta_pending || s->channel_pending || !s->join.aid || s->join.aid>2007)return EAGAIN;
 if(r->scan_count>16 || !r->scan_generation || s->scan.pending)return ESTALE;
 for(unsigned i=0;i<r->scan_count;i++){
  const struct a04_wlan_ap *a=&r->scan[i];
  if(a->ssid_length!=s->ap.ssid_length || a->ssid_length>32 || a->channel!=s->ap.channel || a->band!=s->ap.band ||
     !a04_wlan_equal(a->mac,s->ap.mac,6) || !a04_wlan_equal(a->ssid,s->ap.ssid,a->ssid_length))continue;
  if(r->scan_extra[i].generation!=r->scan_generation || r->scan_extra[i].ie_bytes>A04_WLAN_SCAN_MAX_IE)return ESTALE;
  a04_wlan_zero((uint8_t *)out,sizeof(*out));out->ap=s->ap;out->observation.observed_us=r->scan_extra[i].observed_us;
  out->observation.generation=r->scan_extra[i].generation;
  out->observation.from_probe=r->scan_extra[i].from_probe;
  out->observation.ie_bytes=r->scan_extra[i].ie_bytes;
  for(unsigned j=0;j<out->observation.ie_bytes;j++)out->observation.ies[j]=r->scan_extra[i].ies[j];
  return 0;
 }
 return ESTALE;
}
#endif
