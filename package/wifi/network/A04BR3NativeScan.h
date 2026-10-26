/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Observed iOS16 Apple80211Scan raw ABI, not a private C++ class ABI.
 * SET10/5456, GET11/1164; cached SCAN_TYPE3 skips wait and MUST NOT start RF.
 * Only canonical unfiltered cached requests are currently implemented. Real
 * nonempty observations need the complete IE/rate/channel/age codec before
 * they can be returned. Their absence is never disguised as an empty scan.
 * Evidence: wifi-worker/native-scan-result-abi.json and native-scan-kernel-event.json.
 * Caller serializes state and obtains count/complete from the real backend.
 */
#ifndef A04BR3_NATIVE_SCAN_H
#define A04BR3_NATIVE_SCAN_H
#include <stdint.h>
#include <sys/errno.h>
struct a04_native_scan_state { unsigned cached_empty; unsigned complete_seen; };
static inline uint32_t a04_scan_u32(const uint8_t *p)
{return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static inline int a04_native_scan_policy(uint32_t value,const void *in,uint32_t bytes)
{
    if(value)return EOPNOTSUPP;
    if(!in || bytes!=5456)return EINVAL;
    const uint8_t *p=(const uint8_t *)in;
    if(a04_scan_u32(p)!=1)return EINVAL;
    if(a04_scan_u32(p+0x34)!=3)return EOPNOTSUPP;
    // Accept only the observed default unfiltered policy with type changed to
    // cached. Unknown filtering/dwell/merge fields must not be silently ignored.
    for(unsigned i=4;i<bytes;i++){
        uint8_t expected=i==4?3:(i==0x34?3:(i==0x38?1:0));
        if(p[i]!=expected)return EOPNOTSUPP;
    }
    return 0;
}
static inline int a04_native_scan_cached(struct a04_native_scan_state *s,
    unsigned count,unsigned complete)
{
    if(!s)return EINVAL;
    // Invalidate the earlier iteration even on failure; never expose stale end.
    s->cached_empty=0;
    if(!complete)return EAGAIN;
    if(count)return EOPNOTSUPP; // full observed-result codec not yet available
    s->cached_empty=1;return 0;
}
static inline int a04_native_scan_get(const struct a04_native_scan_state *s,
    uint32_t value,void *out,uint32_t *bytes,unsigned count,unsigned complete)
{
    if(value)return EOPNOTSUPP;
    if(!s || !out || !bytes || *bytes!=1164)return EINVAL;
    const uint8_t *p=(const uint8_t *)out;
    if(a04_scan_u32(p)!=1 || ((unsigned)p[0x8a]|((unsigned)p[0x8b]<<8))!=1024)return EINVAL;
    if(!s->cached_empty)return EOPNOTSUPP;
    if(!complete)return EAGAIN;
    if(count)return EOPNOTSUPP;
    return EIO; // proven iterator end ONLY for actual completed empty cache
}
static inline unsigned a04_native_scan_completion(struct a04_native_scan_state *s,
    unsigned complete)
{
    if(!complete){s->complete_seen=0;s->cached_empty=0;return 0;}
    if(s->complete_seen)return 0;
    s->complete_seen=1;return 1;
}
#endif
