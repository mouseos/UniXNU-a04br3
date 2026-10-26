/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Native ARM64 SET envelope: retained REA ev_c248f36492c550125e8a6a34347abb41c4f826d026abad427664f16b77e806e5.
 * No operation payload/type/value semantics are inferred here.
 */
#ifndef A04BR3_APPLE80211_SET_H
#define A04BR3_APPLE80211_SET_H
#include "A04BR3Apple80211IOCTL.h"
#define A04_APPLE80211_SET64 0x802869c8UL
/* authorized is supplied exclusively by the kernel credential adapter, never
 * from request fields/backend state. SET has no copyout or length mutation. */
static inline int a04_network_set(const struct a04_network_backend *backend,
    const struct a04_network_state *state,const struct a04_apple80211_req64 *req,
    const char *name,void *scratch,unsigned capacity,const struct a04_network_copy *copy,
    int authorized)
{
    if(!authorized)return EPERM;
    if(!backend || !state || !req || !name || !copy || !copy->in)return EINVAL;
    unsigned n=0;for(;n<16 && req->name[n];n++){}
    if(n==16)return EINVAL;
    unsigned i=0;for(;i<n;i++)if(!name[i] || name[i]!=req->name[i])return ENXIO;
    if(name[i])return ENXIO;
    if(req->length>A04_APPLE80211_MAX_BYTES || req->length>capacity)return EINVAL;
    if(req->length && (!req->user_data || !scratch))return EINVAL;
    if(!a04_network_control_ready(state))return ENETDOWN;
    if(!backend->native_set)return EOPNOTSUPP;
    if(req->length){
        int error=copy->in(copy->context,req->user_data,scratch,req->length);
        if(error)return error;
    }
    return backend->native_set(backend->context,req->type,req->value,
        req->length?scratch:NULL,req->length);
}
#endif
