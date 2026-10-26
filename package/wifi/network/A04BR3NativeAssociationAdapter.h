/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#ifndef A04BR3_NATIVE_ASSOCIATION_ADAPTER_H
#define A04BR3_NATIVE_ASSOCIATION_ADAPTER_H
#include "A04BR3NativeAssociation.h"
#include "A04BR3NativeCurrentNetwork.h"
#include "A04BR3NativePeerIdentity.h"
#include "A04BR3NativePersonalAssociation.h"
#include "../sdio/A04BR3WLANPSKBinding.h"
#include "../sdio/A04BR3WLANBackendAPI.h"
#if !defined(A04_WLAN_JOIN_TARGET_API) || A04_WLAN_JOIN_TARGET_API != 1
#error "Native association requires atomic target admission API1"
#endif
static inline int a04_assoc_provider_info(void *p,struct a04_wlan_scan_info *out)
{return a04br3_wlan_scan_snapshot((IOService *)p,out);}
static inline int a04_assoc_provider_record(void *p,unsigned generation,unsigned index,struct a04_wlan_scan_record *out)
{return a04br3_wlan_scan_copy_record((IOService *)p,generation,index,out);}
static inline int a04_assoc_provider_join(void *p,const struct a04_wlan_join_target *target)
{return a04br3_wlan_join_target((IOService *)p,target);}
/* Provider ref held by caller; no SDIO lock across PBKDF2. Original raw
 * passphrase-derived PMK extension, when present, must match actual derivation.
 */
static inline int a04_assoc_personal_set(IOService *provider,unsigned value,const void *in,unsigned bytes,struct a04_wlan_scan_record *work){
 struct a04_native_personal_request request={};int e=a04_native_personal_decode(value,in,bytes,&request);if(e)return e;
 struct a04_wlan_scan_info info={};e=a04br3_wlan_scan_snapshot(provider,&info);
 struct a04_wlan_join_target target={};unsigned matched=0;
 if(!e && (!info.generation || !info.complete))e=EAGAIN;
 if(!e && (info.error || info.count>16))e=EIO;
 for(unsigned i=0;!e && i<info.count;i++){
  e=a04br3_wlan_scan_copy_record(provider,info.generation,i,work);if(e)break;
  const auto *ap=&work->ap;
  if(ap->ssid_length!=request.peer.ssid_length || memcmp(ap->ssid,request.peer.ssid,ap->ssid_length) ||
   (request.peer.force_bssid && memcmp(ap->mac,request.peer.bssid,6)))continue;
  if(!(ap->capability&16) || a04_wlan_rsn_psk_ccmp(ap->rsn,ap->rsn_length)){e=EOPNOTSUPP;break;}
  if(++matched>1){e=EEXIST;break;}
  target.generation=info.generation;target.index=i;target.ssid_length=ap->ssid_length;target.channel=ap->channel;target.band=ap->band;
  memcpy(target.bssid,ap->mac,6);memcpy(target.ssid,ap->ssid,ap->ssid_length);
 }
 if(!e && !matched)e=ENOENT;
 struct a04_psk_prepared prepared={};auto ops=a04_psk_native_crypto();
 if(!e)e=a04_psk_prepare(&prepared,&ops,request.key.secret,request.key.length,
  request.key.kind==A04_NATIVE_KEY_PASSPHRASE,target.ssid,target.ssid_length)?EIO:0;
 if(!e && request.derived_bytes && !a04_psk_equal(request.derived,prepared.pmk,32))e=EINVAL;
 if(!e)e=a04br3_wlan_join_personal(provider,&target,&prepared);
 a04_psk_prepared_clear(&prepared);a04_wlan_secret_clear(&request,sizeof(request));return e;
}
static inline int a04_assoc_provider_set(IOService *provider,uint32_t value,const void *in,uint32_t bytes)
{
 if(!provider)return ENXIO;
 struct a04_network_backend backend={};int error=a04br3_wlan_acquire_backend(provider,&backend);
 if(error)return error;
 struct a04_network_state state={};error=backend.state(backend.context,&state);
 if(!error && (!a04_network_control_ready(&state) || !state.radio_enabled))error=ENETDOWN;
 struct a04_wlan_scan_record *work=nullptr;
 if(!error){work=(a04_wlan_scan_record *)IOMallocZero(sizeof(*work));if(!work)error=ENOMEM;}
 if(!error){struct a04_association_backend api={provider,a04_assoc_provider_info,a04_assoc_provider_record,a04_assoc_provider_join};
  error=(in && bytes==592 && a04_assoc_u16((const uint8_t *)in+8)==8)?
   a04_assoc_personal_set(provider,value,in,bytes,work):a04_native_association_set(&api,value,in,bytes,work);}
 if(work)IOFree(work,sizeof(*work));
 a04br3_wlan_release_backend(provider);return error;
}
/* Original native SetWithIOCTL case22 with NULL args leaves value/len/pointer
 * zero. Reason-dictionary carrier8 and all other forms remain unsupported. */
static inline int a04_assoc_provider_disassociate(IOService *provider,uint32_t value,const void *in,uint32_t bytes){
 if(value || bytes || in)return EOPNOTSUPP;
 if(!provider)return ENXIO;
 return a04br3_wlan_disassociate(provider);
}
/* BSD gate may be held by the caller; no SDIO lock is held on entry.
 * acquire_backend adds one provider reference, not a held lock. state() and
 * current_record() separately take/release transportLock; current_record
 * rechecks real association and copies all bytes before dropping that lock.
 * Encode/copy runs lock-free with respect to SDIO, then frees work and drops
 * exactly that temporary provider reference. No refresh/ioctl/DLIL reentry.
 */
static inline int a04_assoc_provider_current(IOService *provider,uint32_t value,void *out,uint32_t *bytes,uint64_t now)
{
 if(!provider)return ENXIO;
 if(value)return EOPNOTSUPP;
 struct a04_network_backend backend={};int error=a04br3_wlan_acquire_backend(provider,&backend);if(error)return error;
 struct a04_network_state state={};error=backend.state(backend.context,&state);
 if(!error && !a04_network_control_ready(&state))error=ENETDOWN;
 if(!error && !state.associated)error=ENOTCONN;
 struct a04_current_network_work *work=nullptr;
 if(!error){work=(a04_current_network_work *)IOMallocZero(sizeof(*work));if(!work)error=ENOMEM;}
 if(!error)error=a04br3_wlan_current_record(provider,&work->raw);
 if(!error)error=a04_native_current_encode(work,now,out,bytes);
 if(work)IOFree(work,sizeof(*work));
 a04br3_wlan_release_backend(provider);return error;
}
/* Same positive currentRecord producer as GET103, but SSID/BSSID do not
 * require its optional RSSI/IE<=1024 result codec. Still open-only: producer's
 * secure gate is unchanged. No preauth/authenticated-success inference.
 * Temporary provider ref acquired once; record is heap-owned, transport lock
 * is held only by state/current_record. No callback, refresh or scan lock.
 */
static inline int a04_assoc_provider_peer(IOService *provider,uint32_t type,uint32_t value,void *out,uint32_t *bytes){
 if(!provider)return ENXIO;
 if(!out || !bytes)return EINVAL;
 unsigned size=a04_native_peer_capacity(type);if(!size || value)return EOPNOTSUPP;
 if(*bytes<size)return EMSGSIZE;
 struct a04_network_backend backend={};int error=a04br3_wlan_acquire_backend(provider,&backend);if(error)return error;
 struct a04_network_state state={};error=backend.state(backend.context,&state);
 if(!error && !a04_network_control_ready(&state))error=ENETDOWN;
 if(!error && !state.associated)error=ENOTCONN;
 struct a04_wlan_scan_record *record=nullptr;
 if(!error){record=(a04_wlan_scan_record *)IOMallocZero(sizeof(*record));if(!record)error=ENOMEM;}
 if(!error)error=a04br3_wlan_current_record(provider,record);
 /* A secured record additionally needs the actual admitted profile, not
  * merely a privacy IE injected by a stale/unrelated producer. */
 if(!error && ((record->ap.capability&16) || record->ap.rsn_length) &&
  (!state.auth_profile_valid || !state.association_generation || state.auth_lower!=1 || state.auth_upper!=8 || state.auth_upper_ext ||
   a04_wlan_rsn_psk_ccmp(record->ap.rsn,record->ap.rsn_length)))error=EOPNOTSUPP;
 if(!error){struct a04_native_peer_identity identity={};identity.ssid_length=record->ap.ssid_length;
  memcpy(identity.bssid,record->ap.mac,6);
  if(identity.ssid_length<=32)memcpy(identity.ssid,record->ap.ssid,identity.ssid_length);
  error=a04_native_peer_encode(&identity,type,value,out,bytes);}
 if(record)IOFree(record,sizeof(*record));
 a04br3_wlan_release_backend(provider);return error;
}
#endif
