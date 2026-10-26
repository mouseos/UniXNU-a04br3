/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Original iOS16 GetWithIOCTL cases1/9: SSID capacity32 / raw BSSID6, no version or
 * length prefix. Identity must come from an actual current-peer producer,
 * never a chosen scan result. No security/controlled-port success is implied.
 */
#ifndef A04BR3_NATIVE_PEER_IDENTITY_H
#define A04BR3_NATIVE_PEER_IDENTITY_H
#include <stdint.h>
#include <string.h>
#include <sys/errno.h>
struct a04_native_peer_identity {unsigned ssid_length;uint8_t ssid[32],bssid[6];};
static inline unsigned a04_native_peer_capacity(uint32_t type){return type==1?32:(type==9?6:0);}
/* Byte-only codec with explicit dependencies: no SDIO/IOKit/mock helpers.
 * Validations precede every output write. Caller owns positive association
 * proof and supplies a copied, immutable identity. SSID may contain NUL bytes.
 */
static inline int a04_native_peer_encode(const struct a04_native_peer_identity *peer,
 uint32_t type,uint32_t value,void *out,uint32_t *bytes)
{
 if(!peer || !out || !bytes)return EINVAL;
 if(value)return EOPNOTSUPP;
 unsigned size=a04_native_peer_capacity(type);if(!size)return EOPNOTSUPP;
 if(*bytes<size)return EMSGSIZE;
 unsigned any=0;for(unsigned i=0;i<6;i++)any|=peer->bssid[i];
 if(!any || (peer->bssid[0]&1) || !peer->ssid_length || peer->ssid_length>32)return ESTALE;
 uint8_t wire[32]={0};
 if(type==1)memcpy(wire,peer->ssid,peer->ssid_length);
 else memcpy(wire,peer->bssid,6);
 unsigned returned=type==1?peer->ssid_length:6;
 memcpy(out,wire,returned);*bytes=returned;return 0;
}
#endif
