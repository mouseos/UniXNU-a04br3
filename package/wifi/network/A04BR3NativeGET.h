/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * iOS16 IO80211 GetWithIOCTL codecs observed with REA at0x1d3ec3f64.
 * Plus scalar GET21 from original __waitForJoinResult/result mapper.
 * Type19: version/count/four state words,24bytes; type43:256byte C string.
 * Type12: thirteen raw capability bytes; advertise no optional native features.
 * Only observed state and this driver's own identity are returned. No invented
 * scan, RSSI, capability bits, Apple firmware version or association status.
 */
#ifndef A04BR3_NATIVE_GET_H
#define A04BR3_NATIVE_GET_H
#include "A04BR3NetworkBackend.h"
#include <sys/errno.h>
#include <string.h>
static inline void a04_native_put32(uint8_t *p,uint32_t n)
{for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(n>>(8*i));}
static inline int a04_native_get(const struct a04_network_state *s,
    uint32_t type,uint32_t value,void *out,uint32_t *bytes)
{
    if(!s || !out || !bytes)return EINVAL;
    if(!a04_network_control_ready(s))return ENETDOWN;
    if(value)return EOPNOTSUPP;
    if(type==27 || type==254){
        /* iOS GetWithIOCTL case1b/fe: raw0x12d8, count at4,
         * channel descriptors(version/channel/flags) at8, stride12.
         * Publish only actual host-enforced policy, not all silicon channels.
         * Unknown domain, wider widths/ACTIVE/DFS/5GHz remain unadvertised. */
        unsigned mask=s->permitted_channels;
        if(!mask || (mask&~0x3ffeu))return EOPNOTSUPP;
        if(*bytes<4824)return EMSGSIZE;
        uint8_t *p=(uint8_t *)out;memset(p,0,4824);a04_native_put32(p,1);
        unsigned count=0;
        for(unsigned channel=1;channel<=13;channel++)if(mask&(1u<<channel)){
            uint8_t *entry=p+8+12*count++;
            a04_native_put32(entry,1);a04_native_put32(entry+4,channel);
            // Runtime programs CMD_SET_DOMAIN_INFO with uc2G4Bandwidth20M=1
            // (default_domain p[52]=1), then enforces this exact channel mask.
            // Pinned flags: 2GHZ0x8 |20MHZ0x2. No40/80/ACTIVE/DFS inference.
            a04_native_put32(entry+8,0xa);
        }
        a04_native_put32(p+4,count);*bytes=4824;return 0;
    }
    if(type==2){
        // Actual iOS raw16; first word is opaque to consumer, request starts0.
        // EXT comes from an explicitly admitted profile, NEVER SET20flags504.
        if(*bytes<16)return EMSGSIZE;
        if(!s->associated)return ENOTCONN;
        if(!s->auth_profile_valid || !s->association_generation)return EOPNOTSUPP;
        if(s->auth_lower!=1 || (s->auth_upper!=0 && s->auth_upper!=8) || s->auth_upper_ext)return EOPNOTSUPP;
        uint8_t wire[16]={0};a04_native_put32(wire+4,s->auth_lower);a04_native_put32(wire+8,s->auth_upper);
        a04_native_put32(wire+12,s->auth_upper_ext);memcpy(out,wire,16);*bytes=16;return 0;
    }
    if(type==21){
        // Original __waitForJoinResult polls scalar21; zero is pending and
        // __errorForAssociateResult table entry1 maps to success0. Actual open
        // station evidence only, never SET acceptance or secure key success.
        if(*bytes<4)return EMSGSIZE;
        uint32_t result;
        switch(s->join_state){
        case A04_NET_JOIN_NOT_STARTED:return ENOTCONN;
        case A04_NET_JOIN_FAILED:return EIO; // no invented native failure code
        case A04_NET_JOIN_PENDING:
            // Personal may be associated while controlled port is still closed.
            if(s->associated && !(s->auth_profile_valid && s->auth_upper==8 && !s->authorized))return EAGAIN;
            result=0;break;
        case A04_NET_JOIN_PERSONAL_COMPLETE:
            if(!s->authorized || !s->auth_profile_valid || s->auth_upper!=8 || !s->datapath_ready)return EAGAIN;
            if(!s->associated || s->station_phase!=A04_NET_PHASE_ASSOCIATED)return EAGAIN;
            result=1;break;
        case A04_NET_JOIN_OPEN_COMPLETE:
            if(!s->associated || s->station_phase!=A04_NET_PHASE_ASSOCIATED)return EAGAIN;
            result=1;break;
        default:return EOPNOTSUPP;
        }
        a04_native_put32((uint8_t *)out,result);*bytes=4;return 0;
    }
    if(type==13){
        if(*bytes<4)return EMSGSIZE;
        if(s->station_phase<A04_NET_PHASE_IDLE || s->station_phase>A04_NET_PHASE_ASSOCIATED)return EOPNOTSUPP;
        if(!!s->associated!=(s->station_phase==A04_NET_PHASE_ASSOCIATED))return EAGAIN;
        // Pinned Apple state enum INIT/SCAN/AUTH/ASSOC/RUN =0/1/2/3/4.
        a04_native_put32((uint8_t *)out,s->station_phase-A04_NET_PHASE_IDLE);*bytes=4;return 0;
    }
    /* CURRENT_NETWORK103 is NOT a scan cache. SSID/BSSID/current RSSI,
     * noise and rate are likewise association data. Report actual absence;
     * never substitute a scanned AP, zero RSSI, invented rate or empty success.
     * Snapshot-only associated metadata remains unsupported here. Production
     * Rendezvous serves1/9/103 via the separate real current-record API. */
    unsigned associationSize=type==103?1164:(type==1?32:(type==9?6:
        ((type==16 || type==17)?52:(type==8?4:0))));
    if(associationSize){
        if(*bytes<associationSize)return EMSGSIZE;
        return s->associated?EOPNOTSUPP:ENOTCONN;
    }
    /* Existing runtime implements AIS infrastructure station only
     * (A04BR3WLANStation.h activate_encode network0). Pinned Apple enum
     * APPLE80211_M_STA=1; no association or carrier is implied.
     * INT_MIT18 remains unsupported: polled host I/O does not establish
     * the native firmware mitigation setting/GET enum semantics.
     * Scalars are internal4-byte outputs; BSD puts them in req.value. */
    unsigned size=type==15?4:(type==0x13?24:(type==0x2b?256:(type==0xc?13:0)));
    if(!size)return EOPNOTSUPP;
    if(*bytes<size)return EMSGSIZE;
    uint8_t *p=(uint8_t *)out;
    if(type==0x13 && (p[0]!=1 || p[1] || p[2] || p[3]))return EINVAL;
    memset(p,0,size);
    if(type==15)a04_native_put32(p,1);
    else if(type==0x13){a04_native_put32(p,1);a04_native_put32(p+4,1);a04_native_put32(p+8,!!s->radio_enabled);}
    else if(type==0x2b) {static const char identity[]="A04BR3 MT6630 source SDIO runtime";memcpy(p,identity,sizeof(identity));}
    /* A zero optional capability mask is deliberate: unported MT6630 features
     * are not advertised as native Apple operations. This is not a statement
     * that the hardware lacks them. Raw ABI has no version/header prefix. */
    *bytes=size;return 0;
}
#endif
