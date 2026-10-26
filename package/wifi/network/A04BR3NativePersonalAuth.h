/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Original bounded AUTH field decoder for native iOS16 SET20. This is NOT a
 * whole592 carrier decoder nor association admission. Unknown carrier fields,
 * key/IE/private-MAC policy still require the owning runtime's validation.
 * REA __performAssociation ev45252d12b57e35e7517b86d8fabd6e8c0c7937e59b9f25f8d0b8bb8c315bb2cd:
 * local_2c8 root; mode local_2c4(+4)=2; lower local_2c2(+6)=1;
 * upper local_2c0(+8)=8 in ordinary RSN-PSK; local_d0(+504) is zero or
 * mask0x04 in observed PSK branches, NOT an authorization result.
 * FT(upper16), SHA256/SAE/enterprise and extension flags remain unsupported.
 */
#ifndef A04BR3_NATIVE_PERSONAL_AUTH_H
#define A04BR3_NATIVE_PERSONAL_AUTH_H
#include <stdint.h>
#include <sys/errno.h>
struct a04_native_personal_auth {unsigned upper,producer_flags;};
static inline unsigned a04_personal_auth_u16(const uint8_t *p){return p[0]|((unsigned)p[1]<<8);}
static inline uint32_t a04_personal_auth_u32(const uint8_t *p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static inline int a04_native_personal_auth_decode(unsigned value,const void *raw,unsigned bytes,struct a04_native_personal_auth *out){
 if(!raw || !out || bytes!=592)return EINVAL;
 const uint8_t *p=(const uint8_t *)raw;
 if(a04_personal_auth_u32(p)!=1)return EINVAL;
 if(value || a04_personal_auth_u16(p+4)!=2 || a04_personal_auth_u16(p+6)!=1 ||
  a04_personal_auth_u16(p+8)!=8 || (a04_personal_auth_u32(p+504)&~4u))return EOPNOTSUPP;
 struct a04_native_personal_auth result={8,a04_personal_auth_u32(p+504)};*out=result;return 0;
}
#endif
