/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * iOS16 SetWithIOCTL case19 (REA ev_5e881c73cf48dcae4c9998d6b0cb1141d250ce703276ccea80c54972577503c5):
 * raw24: version1, count at4, up to4 integer state words at8. Original codec;
 * no private Apple class ABI copied. One physical radio: only nonempty uniform
 * boolean requests are meaningful. This delegates the REAL backend operation;
 * unsupported transitions/errors remain errors, with no local state mutation.
 * Caller is the credential-checked BSD SET gate, never an unchecked user path.
 */
#ifndef A04BR3_NATIVE_POWER_H
#define A04BR3_NATIVE_POWER_H
#include "A04BR3NetworkBackend.h"
#include <sys/errno.h>
static inline uint32_t a04_power_u32(const uint8_t *p)
{return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static inline int a04_native_power_set(const struct a04_network_backend *backend,
    uint32_t value,const void *input,uint32_t bytes)
{
    if(!backend || !input || bytes!=24)return EINVAL;
    if(value)return EOPNOTSUPP;
    const uint8_t *p=(const uint8_t *)input;
    if(a04_power_u32(p)!=1)return EINVAL;
    uint32_t count=a04_power_u32(p+4);
    if(!count || count>4)return EINVAL;
    uint32_t enabled=a04_power_u32(p+8);
    if(enabled>1)return EOPNOTSUPP;
    for(unsigned i=1;i<count;i++)
        if(a04_power_u32(p+8+4*i)!=enabled)return EOPNOTSUPP;
    if(!backend->state || !backend->set_admin)return EOPNOTSUPP;
    struct a04_network_state state={};int error=backend->state(backend->context,&state);
    if(error)return error;
    if(!a04_network_control_ready(&state))return ENETDOWN;
    return backend->set_admin(backend->context,enabled);
}
#endif
