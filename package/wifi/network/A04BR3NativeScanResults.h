/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Original codec using retained iOS16 REA scan/helper/policy evidence and
 * corroborating itlwm53c51c2 apple80211_var.h (mac12+ exact1164) + producer.
 * No code copied from itlwm. Scalar offsets/constants are reproduced from the
 * observed ABI; reference hashes/credits are retained in worker logs.
 * Only board's observed ordinary beacon/probe 2.4GHz subset is supported.
 * Optional native rate words remain absent (count0) until conversion is
 * proven; actual rate IEs are preserved in full. No optional bandwidth,
 * active/DFS flags or noise/SNR measurement is invented. Full IEs are retained.
 */
#ifndef A04BR3_NATIVE_SCAN_RESULTS_H
#define A04BR3_NATIVE_SCAN_RESULTS_H
#include "A04BR3ScanObservation.h"
#include <sys/errno.h>
#include <string.h>
static inline uint32_t a04_scan_result_u32(const uint8_t *p)
{return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static inline void a04_scan_result_put16(uint8_t *p,uint16_t v)
{p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static inline void a04_scan_result_put32(uint8_t *p,uint32_t v)
{for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i));}
/* 1=active,2=passive,3=cached, corroborated source enum + donor type3 wait
 * bypass. Only canonical unfiltered version1/BSSany/PHY1 request is accepted.
 * Donor __getScanData leaves +0x1310 zero for NULL options, but explicitly
 * writes 0x80000000 for a non-NULL dictionary without a key override
 * (ev_371521687beae85eb7e32175bef34ad1e5b80173f752700d8b64896957bbe0d9).
 * Accept those two default carriers only; no arbitrary option/filter values.
 * An unsupported policy leaves *mode unchanged, never silently drops filters.
 *
 * Layout (itlwm apple80211_scan_data, iOS16 channel array of 400 entries):
 * +0x3c dwell u16, +0x40 rest u32, +0x44 num_channels, +0x48 channels[400]
 * of {version1,channel,flags}, +0x1308 SCAN_FLAGS u64, +0x1310
 * SCAN_RSSI_THRESHOLD (CFString keys resolved from donor io80211 cfstrings
 * 0x1dabadcd8..0x1dabade18 used by __getScanData). Real Setup requests carry
 * SCAN_LOW_LATENCY(0x20) and a 2.4GHz channel list 1..13 flags0x0a.
 * Only scheduling hints LOW_PRIORITY/LOW_LATENCY/PRIO_OVERRIDE/IS_THROTTLED
 * are accepted; result-shaping flags (OFFCHANNEL, 6GHz, FILS, RNR) are not.
 * A channel list becomes a result filter: 2.4GHz channels form *channels,
 * 5GHz entries are valid requests the 2.4GHz-only backend cannot report.
 */
enum { A04_SCAN_CHANNELS=400, A04_SCAN_FLAG_HINTS=0x2|0x20|0x40|0x80,
    A04_SCAN_CHANNEL_FLAGS=0x1|0x2|0x4|0x8|0x10|0x20|0x40|0x80|0x100|0x200|0x400|0x800 };
struct a04_scan_request { unsigned mode,filtered; uint32_t channels; };
static inline int a04_scan_result_request(uint32_t value,const void *in,
    uint32_t bytes,struct a04_scan_request *out)
{
    if(!in || !out || bytes!=5456)return EINVAL;
    if(value)return EOPNOTSUPP;
    const uint8_t *p=(const uint8_t *)in;
    if(a04_scan_result_u32(p)!=1)return EINVAL;
    unsigned type=a04_scan_result_u32(p+0x34);
    if(type<1 || type>3)return EOPNOTSUPP;
    if(a04_scan_result_u32(p+4)!=3 || a04_scan_result_u32(p+0x38)!=1)return EOPNOTSUPP;
    for(unsigned i=8;i<0x34;i++)if(p[i])return EOPNOTSUPP; // no directed BSSID/SSID
    if(p[0x3e] || p[0x3f])return EOPNOTSUPP; // padding after u16 dwell
    uint32_t count=a04_scan_result_u32(p+0x44);
    if(count>A04_SCAN_CHANNELS)return EOPNOTSUPP;
    uint32_t channels=0;
    for(unsigned i=0;i<A04_SCAN_CHANNELS;i++){
        const uint8_t *c=p+0x48+12*i;
        uint32_t version=a04_scan_result_u32(c),channel=a04_scan_result_u32(c+4),
            flags=a04_scan_result_u32(c+8);
        if(i>=count){if(version || channel || flags)return EOPNOTSUPP;continue;}
        if(version!=1 || !channel || (flags & ~(uint32_t)A04_SCAN_CHANNEL_FLAGS))return EOPNOTSUPP;
        unsigned band2=(flags & 0x8)!=0,band5=(flags & 0x10)!=0;
        if(band2==band5)return EOPNOTSUPP;
        if(band2){if(channel>14)return EOPNOTSUPP;channels|=1u<<channel;}
        else if(channel>196)return EOPNOTSUPP;
    }
    uint64_t scan_flags=(uint64_t)a04_scan_result_u32(p+0x1308)|
        ((uint64_t)a04_scan_result_u32(p+0x130c)<<32);
    if(scan_flags & ~(uint64_t)A04_SCAN_FLAG_HINTS)return EOPNOTSUPP;
    uint32_t rssi=a04_scan_result_u32(p+0x1310);
    if(rssi && rssi!=0x80000000u)return EOPNOTSUPP;
    for(unsigned i=0x1314;i<bytes;i++)if(p[i])return EOPNOTSUPP;
    out->mode=type;out->filtered=count!=0;out->channels=channels;return 0;
}
static inline int a04_scan_result_policy(uint32_t value,const void *in,
    uint32_t bytes,unsigned *mode)
{
    if(!mode)return EINVAL;
    struct a04_scan_request request={};
    int error=a04_scan_result_request(value,in,bytes,&request);
    if(!error)*mode=request.mode;
    return error;
}
static inline unsigned a04_scan_result_selected(unsigned filtered,uint32_t channels,
    const struct a04_scan_observation *r)
{
    if(!filtered)return 1;
    return r && r->band==1 && r->channel && r->channel<=14 && ((channels>>r->channel)&1u);
}
/* GET11 caller header: CoreWiFi on hardware passes version0 with ie_len1024
 * (kernel 5f41857 UART "scan get ... ver=0 ielen=1024"); version1 is the
 * itlwm producer form. Output is always written as version1.
 */
static inline unsigned a04_scan_result_get_header(const uint8_t *p)
{
    uint32_t version=a04_scan_result_u32(p);
    return (version==0 || version==1) && ((unsigned)p[0x8a]|((unsigned)p[0x8b]<<8))==1024;
}
/* Record-level validation. Codes: 1 bounds, 2 band/channel/rcpi, 3 bssid,
 * 4 TLV framing, 5 SSID IE mismatch, 6 rate IE mismatch, 7 DS channel
 * mismatch, 8 missing SSID/rates or rate count mismatch. A failing record is
 * skipped by GET iteration instead of aborting every other observed AP.
 */
static inline int a04_scan_result_record_check(const struct a04_scan_snapshot *s,
    const struct a04_scan_observation *r,unsigned *why)
{
    unsigned dummy;if(!why)why=&dummy;*why=0;
    if(r->seen_us>s->now_us || r->ssid_length>32 || r->rate_count>15 ||
       r->ie_length<2 || r->ie_length>1024 || r->ie_truncated || r->from_probe>1 ||
       r->beacon>65535 || r->capability>65535){*why=1;return EINVAL;}
    if(r->band!=1 || !r->channel || r->channel>13 || !r->rcpi_valid || r->rcpi>220){*why=2;return EOPNOTSUPP;}
    unsigned any=0;for(unsigned i=0;i<6;i++)any|=r->bssid[i];
    if(!any || (r->bssid[0]&1)){*why=3;return EINVAL;}
    // Full captured TLVs must agree with separately observed metadata. This
    // prevents accidentally combining an updated cache entry with older IEs.
    unsigned ssids=0,rates=0;
    for(unsigned i=0;i<r->ie_length;){
        if(r->ie_length-i<2 || (unsigned)r->ies[i+1]>r->ie_length-i-2){*why=4;return EINVAL;}
        unsigned id=r->ies[i],n=r->ies[i+1];const uint8_t *data=r->ies+i+2;
        if(id==0){
            if(ssids++ || n!=r->ssid_length || memcmp(data,r->ssid,n)){*why=5;return EINVAL;}
        }
        if(id==1 || id==50){
            if(!n || rates+n>r->rate_count || memcmp(data,r->rates+rates,n)){*why=6;return EINVAL;}
            rates+=n;
        }
        if(id==3 && (n!=1 || data[0]!=r->channel)){*why=7;return EINVAL;}
        i+=2+n;
    }
    if(!ssids || !rates || rates!=r->rate_count){*why=8;return EINVAL;}
    return 0;
}
static inline int a04_scan_result_encode(const struct a04_scan_snapshot *s,
    const struct a04_scan_observation *r,void *out,uint32_t *bytes)
{
    if(!s || !r || !out || !bytes || *bytes!=1164)return EINVAL;
    uint8_t *p=(uint8_t *)out;
    if(!a04_scan_result_get_header(p))return EINVAL;
    if(!s->generation || r->generation!=s->generation)return ESTALE;
    if(!s->complete)return EAGAIN;
    int error=a04_scan_result_record_check(s,r,nullptr);
    if(error)return error;
    uint64_t age=(s->now_us-r->seen_us)/1000;
    if(age>UINT32_MAX)age=UINT32_MAX;
    memset(p,0,1164);a04_scan_result_put32(p,1);a04_scan_result_put32(p+4,1);
    a04_scan_result_put32(p+8,r->channel);
    a04_scan_result_put32(p+0xc,8); // observed 2GHz band, no inferred width/active
    a04_scan_result_put16(p+0x16,(uint16_t)(int16_t)((int)(r->rcpi>>1)-110));
    a04_scan_result_put16(p+0x18,(uint16_t)r->beacon);
    a04_scan_result_put16(p+0x1a,(uint16_t)r->capability);
    memcpy(p+0x1c,r->bssid,6);
    // Native helper expects integer Mbps; reference rate-copy loop precedes
    // count assignment and cannot prove conversion. Optional count0 omits
    // those derived words; complete received rate IEs remain unchanged below.
    p[0x22]=0;
    p[0x60]=r->ssid_length;memcpy(p+0x61,r->ssid,r->ssid_length);
    a04_scan_result_put32(p+0x84,(uint32_t)age);
    a04_scan_result_put16(p+0x88,(uint16_t)r->from_probe); // donor probe-origin bit0
    a04_scan_result_put16(p+0x8a,r->ie_length);memcpy(p+0x8c,r->ies,r->ie_length);
    return 0;
}
struct a04_scan_result_session {
    uint64_t generation;
    uint32_t cursor,count,pinned;
    uint32_t filtered,channels; // requested 2.4GHz channel bitmap when filtered
    uint32_t skipped,skip_why;  // records not encodable, last reason code
};
static inline int a04_scan_result_accept(struct a04_scan_result_session *session,
    const struct a04_scan_snapshot *snapshot,unsigned cached)
{
    if(!session || !snapshot || !snapshot->generation)return EINVAL;
    if(cached && !snapshot->complete)return EAGAIN;
    *session={snapshot->generation,0,snapshot->complete?snapshot->count:0,snapshot->complete?1u:0u,0,0,0,0};
    return 0;
}
/* Get snapshot/record atomically from backend for session.generation and
 * session.cursor first. Do not fetch record when cursor already reaches count.
 * EIO terminates iteration; EAGAIN is pending; ESTALE must not restart at AP0.
 */
static inline int a04_scan_result_next(struct a04_scan_result_session *session,
    const struct a04_scan_snapshot *snapshot,const struct a04_scan_observation *r,
    void *out,uint32_t *bytes)
{
    if(!session || !snapshot || !session->generation)return EINVAL;
    if(session->generation!=snapshot->generation)return ESTALE;
    if(!snapshot->complete)return EAGAIN;
    if(session->pinned && snapshot->count!=session->count)return ESTALE;
    if(session->cursor>=snapshot->count)return EIO;
    int error=a04_scan_result_encode(snapshot,r,out,bytes);
    if(!error){session->count=snapshot->count;session->pinned=1;session->cursor++;}
    return error;
}
#endif
