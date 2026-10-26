/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Pure snapshot of the actual locked runtime, independent of native ABI.
 * Profile and association are distinct from authenticated controlled port.
 */
#ifndef A04BR3_WLAN_NETWORK_PROFILE_H
#define A04BR3_WLAN_NETWORK_PROFILE_H
#include "A04BR3WLANRuntime.h"
#include "../network/A04BR3StationPhase.h"
static inline void a04_wlan_network_profile(const struct a04_wlan_runtime *r,struct a04_network_state *out,unsigned packet_delivery){
 out->station_phase=out->control_ready?a04_network_phase_from_station(&r->station):A04_NET_PHASE_UNKNOWN;
 out->join_state=out->control_ready && !r->teardown.pending?a04_network_join_from_station(&r->station):A04_NET_JOIN_UNKNOWN;
 out->permitted_channels=out->control_ready?r->station.local.permitted_channels:0;
 out->associated=out->control_ready && !r->teardown.pending && r->station.selected && r->station.sta_active &&
  r->station.join.phase==4 && !r->station.failed && !r->station.join.failed && !r->failed;
 out->association_generation=r->association_generation;
 unsigned personal=r->personal_admitted && r->auth_upper==8 && r->station.secured &&
  (r->station.ap.capability&16) && !a04_wlan_rsn_psk_ccmp(r->station.ap.rsn,r->station.ap.rsn_length);
 unsigned open=!r->station.secured && !r->auth_upper && !(r->station.ap.capability&16) && !r->station.ap.rsn_length;
 out->auth_profile_valid=out->associated && r->association_generation && r->auth_lower==1 && (open || personal) && !r->auth_upper_ext;
 if(out->auth_profile_valid){out->auth_lower=r->auth_lower;out->auth_upper=r->auth_upper;out->auth_upper_ext=r->auth_upper_ext;}
 out->authorized=out->associated && (open || (personal && r->security.authorized && r->security.gtk_accepted &&
  r->security.ptk_confirmed && r->security.m4_confirmed && a04_psk_authenticated(&r->personal)));
 out->preauth_ready=out->control_ready && out->associated && out->auth_profile_valid && personal && r->security.enabled && !r->security.failed && !r->personal.failed;
 if(r->personal_admitted && out->control_ready && !r->teardown.pending){
  out->join_state=r->personal.failed?A04_NET_JOIN_FAILED:(out->authorized?A04_NET_JOIN_PERSONAL_COMPLETE:A04_NET_JOIN_PENDING);
 }
 out->datapath_ready=out->control_ready && out->associated && out->authorized && packet_delivery && a04_wlan_port_allowed(&r->station,&r->security,0);
}
#endif
