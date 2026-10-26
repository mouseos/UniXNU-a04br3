/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Actual native EAP8021X _eapol_socket registration, not an invented userclient.
 * Pure validation of an ALREADY-copied ndrv_demux_desc array. Existing XNU
 * ndrv_setspec handles credential, sockaddr/bind, pointer copy and DLIL attach.
 * This helper neither registers sockets nor claims a listener is present.
 */
#ifndef A04BR3_NATIVE_EAPOL_PROTOCOL_H
#define A04BR3_NATIVE_EAPOL_PROTOCOL_H
#include <stdint.h>
#include <stddef.h>
#include <sys/errno.h>
#define A04_NATIVE_EAPOL_FAMILY 0x8021ecu
#define A04_NATIVE_EAPOL_INTEREST_DONE 0xe0000130u
struct a04_native_eapol_demux {unsigned eapol,preauth;};
static inline int a04_native_eapol_demux_decode(uint32_t version,uint32_t family,
    const void *descriptors,unsigned bytes,unsigned count,struct a04_native_eapol_demux *out)
{
    if(!out)return EINVAL;
    out->eapol=out->preauth=0;
    if(version!=1 || family!=A04_NATIVE_EAPOL_FAMILY)return EOPNOTSUPP;
    if(!descriptors || count<1 || count>2 || bytes!=count*32 ||
        (uintptr_t)descriptors>UINTPTR_MAX-bytes)return EINVAL;
    const uint8_t *p=(const uint8_t *)descriptors;
    unsigned eapol=0,preauth=0;
    for(unsigned i=0;i<count;i++,p+=32){
        if(p[0]!=4 || p[1] || p[2]!=2 || p[3])return EOPNOTSUPP;
        /* Unused union bytes are unspecified malloc data in the native producer;
         * they are not a feature flag or grounds for fake ABI assumptions. */
        unsigned type=((unsigned)p[4]<<8)|p[5];
        if(type==0x888e){if(eapol)return EINVAL;eapol=1;}
        else if(type==0x88c7){if(preauth)return EINVAL;preauth=1;}
        else return EOPNOTSUPP;
    }
    if(!eapol || (count==2 && !preauth))return EINVAL;
    out->eapol=eapol;out->preauth=preauth;return 0;
}
#endif
