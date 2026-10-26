/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Pure native iOS16 SET20 strict592-byte decoder. No IOKit, SDIO, runtime,
 * fixture or transitively declared helper dependency. Byte reads are unaligned
 * safe. Only the observed open/infra + optional ForceBSSID carrier is accepted.
 */
#ifndef A04BR3_NATIVE_ASSOCIATION_CODEC_H
#define A04BR3_NATIVE_ASSOCIATION_CODEC_H
#include <stdint.h>
#include <string.h>
#include <sys/errno.h>
struct a04_native_association {
 unsigned ssid_length,force_bssid;uint8_t ssid[32],bssid[6];
};
/* Byte-based native validation; no SDIO helper or implicit include scope. */
static inline int a04_assoc_unicast_address(const uint8_t *mac)
{
 if(!mac)return 0;
 unsigned any=0;for(unsigned i=0;i<6;i++)any|=mac[i];
 return any && !(mac[0]&1);
}
static inline unsigned a04_assoc_u16(const uint8_t *p){return p[0]|((unsigned)p[1]<<8);}
static inline uint32_t a04_assoc_u32(const uint8_t *p)
{return p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static inline int a04_native_association_decode(uint32_t value,const void *in,
 uint32_t bytes,struct a04_native_association *out)
{
 if(!in || !out || bytes!=592)return EINVAL;
 if(value)return EOPNOTSUPP;
 const uint8_t *p=(const uint8_t *)in;
 if(a04_assoc_u32(p)!=1)return EINVAL;
 if(a04_assoc_u16(p+4)!=2 || a04_assoc_u16(p+6)!=1 || a04_assoc_u16(p+8))return EOPNOTSUPP;
 unsigned length=a04_assoc_u32(p+12);if(!length || length>32)return EINVAL;
 unsigned forced=p[463]&0x20;
 if(p[463]&~0x20u)return EOPNOTSUPP;
 unsigned any=0;for(unsigned i=0;i<6;i++)any|=p[48+i];
 if(forced?!a04_assoc_unicast_address(p+48):any!=0)return EINVAL;
 for(unsigned i=0;i<592;i++){
  if(i<10 || (i>=12 && i<16+length) || (i>=48 && i<54) || i==463)continue;
  if(p[i])return EOPNOTSUPP;
 }
 struct a04_native_association result;memset(&result,0,sizeof(result));result.ssid_length=length;result.force_bssid=forced?1:0;
 memcpy(result.ssid,p+16,length);memcpy(result.bssid,p+48,6);*out=result;return 0;
}
#endif
