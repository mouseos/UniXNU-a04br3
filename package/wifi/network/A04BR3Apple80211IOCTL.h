/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Native iOS16 ARM64 BSD GET envelope observed in retained IO80211 REA.
 * No guessed operation payloads or setter ABI. Unknown commands stay unsupported.
 */
#ifndef A04BR3_APPLE80211_IOCTL_H
#define A04BR3_APPLE80211_IOCTL_H
#include <stdint.h>
#include <stddef.h>
#include <sys/errno.h>
#include "A04BR3NetworkBackend.h"
#define A04_APPLE80211_GET64 0xc02869c9UL
#define A04_APPLE80211_MAX_BYTES 16384u
/* Only observed scalar opcodes in iOS16 GetWithIOCTL's caseD5, corroborated
 * by boot2 length0 calls. GET21 is also proven by __waitForJoinResult:
 * type21, length0, null data, result in req.value. Other zero-length GETs
 * remain rejected. */
static inline int a04_native_scalar_type(uint32_t type)
{
    switch(type){case 5:case 8:case 13:case 15:case 18:case 21:case 57:case 96:return 1;}
    return 0;
}
struct a04_apple80211_req64 {
    char name[16];uint32_t type,value,length,padding;uint64_t user_data;
};
#ifdef __cplusplus
static_assert(sizeof(a04_apple80211_req64)==40,"native GET64 envelope size");
static_assert(offsetof(a04_apple80211_req64,user_data)==32,"native GET64 pointer");
#else
_Static_assert(sizeof(struct a04_apple80211_req64)==40,"native GET64 envelope size");
_Static_assert(offsetof(struct a04_apple80211_req64,user_data)==32,"native GET64 pointer");
#endif
struct a04_network_copy {
    void *context;
    int (*in)(void *,uint64_t,void *,unsigned);
    int (*out)(void *,const void *,uint64_t,unsigned);
};
/* Scratch is caller-owned, zero initialized by the adapter before copyin. The
 * kernel never dereferences user_data and never returns scratch on failure. */
static inline int a04_network_get(const struct a04_network_backend *backend,
    const struct a04_network_state *state,struct a04_apple80211_req64 *req,
    const char *name,void *scratch,unsigned capacity,const struct a04_network_copy *copy)
{
    if(!backend || !state || !req || !name || !scratch || !copy || !copy->in || !copy->out)return EINVAL;
    unsigned n=0;for(;n<16 && req->name[n];n++){}
    if(n==16)return EINVAL;
    unsigned i=0;for(;i<n;i++)if(!name[i] || name[i]!=req->name[i])return ENXIO;
    if(name[i])return ENXIO;
    /* iOS GetWithIOCTL caseD5 uses a zero-length/null-data request and
     * consumes req.value as its scalar output. Normalize ONLY the observed
     * scalar set above to a private4-byte backend buffer; no user pointer is touched.
     * Existing SDIO callback ABI remains unchanged. */
    if(a04_native_scalar_type(req->type)){
        if(req->length || req->user_data)return EINVAL;
        if(capacity<4)return EMSGSIZE;
        if(!a04_network_control_ready(state))return ENETDOWN;
        if(!backend->native_get)return EOPNOTSUPP;
        uint32_t output=4;
        int error=backend->native_get(backend->context,req->type,req->value,scratch,&output);
        if(error)return error;
        if(output!=4)return EOVERFLOW;
        const uint8_t *p=(const uint8_t *)scratch;
        req->value=(uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
        return 0;
    }
    if(!req->length || req->length>A04_APPLE80211_MAX_BYTES || req->length>capacity || !req->user_data)return EINVAL;
    if(!a04_network_control_ready(state))return ENETDOWN;
    if(!backend->native_get)return EOPNOTSUPP;
    int error=copy->in(copy->context,req->user_data,scratch,req->length);if(error)return error;
    uint32_t output=req->length;
    error=backend->native_get(backend->context,req->type,req->value,scratch,&output);
    if(error)return error;
    if(!output || output>req->length)return EOVERFLOW;
    error=copy->out(copy->context,scratch,req->user_data,output);if(error)return error;
    req->length=output;return 0;
}
#endif
