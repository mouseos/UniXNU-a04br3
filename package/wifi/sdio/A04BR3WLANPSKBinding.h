/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Thin production binding only; real primitives live in PersonalCrypto.
 * No second crypto implementation, SDK dependency or deterministic RNG exists.
 * See A04BR3WLANPSK4Way.sources.json for the audited startup/entropy path.
 */
#ifndef A04BR3_WLAN_PSK_BINDING_H
#define A04BR3_WLAN_PSK_BINDING_H
#include "A04BR3WLANPSK4Way.h"
#include "A04BR3WLANPersonalCrypto.h"
static inline int a04_psk_native_pmk(void *ctx,const uint8_t *pass,unsigned n,const uint8_t *ssid,unsigned sn,uint8_t out[32]){
 (void)ctx;return a04_wlan_personal_pmk(pass,n,ssid,sn,out);
}
static inline int a04_psk_native_ptk(void *ctx,const uint8_t pmk[32],const uint8_t ap[6],const uint8_t sta[6],const uint8_t an[32],const uint8_t sn[32],uint8_t out[64]){
 (void)ctx;return a04_wlan_personal_ptk(pmk,ap,sta,an,sn,out);
}
static inline int a04_psk_native_mic(void *ctx,const uint8_t kck[16],const uint8_t *p,unsigned n,uint8_t out[16]){
 (void)ctx;return a04_wlan_personal_mic(kck,p,n,out);
}
static inline int a04_psk_native_unwrap(void *ctx,const uint8_t kek[16],const uint8_t *p,unsigned n,uint8_t *out,unsigned cap,unsigned *bytes){
 (void)ctx;return a04_wlan_personal_unwrap(kek,p,n,out,cap,bytes);
}
#if defined(KERNEL) && defined(A04BR3_WIFI)
// Current UniXNU does not refresh its ADT seed each boot. The board mixes
// fresh, checked hardware TRNG bytes with source-kernel read_random; unavailable
// hardware returns failure rather than relying on public image bytes.
extern "C" int a04br3_wifi_random(uint8_t out[32]);
static inline int a04_psk_native_random(void *ctx,uint8_t out[32]){
 (void)ctx;return a04br3_wifi_random(out);
}
static inline struct a04_psk_crypto a04_psk_native_crypto(void){
 struct a04_psk_crypto ops={0,a04_psk_native_pmk,a04_psk_native_random,
  a04_psk_native_ptk,a04_psk_native_mic,a04_psk_native_unwrap};return ops;
}
#endif
#endif
