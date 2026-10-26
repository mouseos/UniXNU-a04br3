/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Strict iOS16 SET20 Personal producer subset. No opaque592 passthrough.
 * REA __performAssociation, see PSK4Way.sources.json. Canonical non-PMF RSN
 * only; unsupported private MAC/FT/SAE/enterprise/extensions fail closed.
 */
#ifndef A04BR3_NATIVE_PERSONAL_ASSOCIATION_H
#define A04BR3_NATIVE_PERSONAL_ASSOCIATION_H
#include "A04BR3NativeAssociationCodec.h"
#include "A04BR3NativePersonalAuth.h"
#include "A04BR3NativeCipherCarrier.h"
#include "../sdio/A04BR3WLANPSK4Way.h"
struct a04_native_personal_request {
 struct a04_native_association peer;
 struct a04_native_key_fields key;
 unsigned derived_bytes;uint8_t derived[32];
};
static inline int a04_native_personal_decode(unsigned value,const void *raw,unsigned bytes,struct a04_native_personal_request *out){
 if(!out)return EINVAL;
 a04_wlan_secret_clear(out,sizeof(*out));
 struct a04_native_personal_auth auth;int e=a04_native_personal_auth_decode(value,raw,bytes,&auth);if(e)return e;
 const uint8_t *p=(const uint8_t *)raw;unsigned sn=a04_assoc_u32(p+12),forced=p[463]&0x20;
 if(!sn || sn>32 || (p[463]&~0x20u))return EOPNOTSUPP;
 unsigned any=0;for(unsigned i=0;i<6;i++)any|=p[48+i];
 if(forced?!a04_assoc_unicast_address(p+48):any!=0)return EINVAL;
 e=a04_native_assoc_key_fields(raw,bytes,&out->key);if(e)return e;
 unsigned ie=a04_assoc_u16(p+204),rsn_bytes=0;const uint8_t *rsn=a04_wlan_rsn_selected(&rsn_bytes);
 if(ie!=rsn_bytes || !a04_psk_equal(p+206,rsn,rsn_bytes)){e=EOPNOTSUPP;goto fail;}
 out->derived_bytes=a04_assoc_u32(p+468);
 if(out->derived_bytes && (out->derived_bytes!=32 || out->key.kind!=A04_NATIVE_KEY_PASSPHRASE)){e=EOPNOTSUPP;goto fail;}
 for(unsigned i=0;i<592;i++){
  if(i<10 || (i>=12 && i<16+sn) || (i>=48 && i<54) ||
   (i>=56 && i<204) || (i>=204 && i<206+ie) || i==463 ||
   (i>=468 && i<472+out->derived_bytes) || (i>=504 && i<508))continue;
  if(p[i]){e=EOPNOTSUPP;goto fail;}
 }
 out->peer.ssid_length=sn;out->peer.force_bssid=forced?1:0;
 for(unsigned i=0;i<sn;i++)out->peer.ssid[i]=p[16+i];
 for(unsigned i=0;i<6;i++)out->peer.bssid[i]=p[48+i];
 for(unsigned i=0;i<out->derived_bytes;i++)out->derived[i]=p[472+i];
 return 0;
fail:a04_wlan_secret_clear(out,sizeof(*out));return e;
}
#endif
