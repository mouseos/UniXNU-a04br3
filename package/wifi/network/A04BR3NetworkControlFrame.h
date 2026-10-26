/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Native NDRV EAPOL envelope, not a supplicant/crypto/authorization engine.
 * Native endpoint: AF_NDRV27, protocol family0x8021ec, demux888e/88c7.
 * The actual XNU ndrv.c registers those demux records on the shared ifnet;
 * this helper neither claims registration/listener success nor changes flags.
 */
#ifndef A04BR3_NETWORK_CONTROL_FRAME_H
#define A04BR3_NETWORK_CONTROL_FRAME_H
#include "A04BR3NetworkBackend.h"
#include <sys/errno.h>
#define A04_NET_CONTROL_MAX_FRAME 1514u
static inline int a04_network_control_mac(const uint8_t *p)
{
    unsigned any=0;for(unsigned i=0;i<6;i++)any|=p[i];
    return any && !(p[0]&1);
}
static inline int a04_network_control_equal(const uint8_t *a,const uint8_t *b)
{
    unsigned diff=0;for(unsigned i=0;i<6;i++)diff|=a[i]^b[i];return !diff;
}
static inline int a04_network_control_associated(const struct a04_network_state *s)
{
    return s && a04_network_preauth_ready(s) && s->radio_enabled && s->association_generation &&
        s->station_phase==A04_NET_PHASE_ASSOCIATED;
}
/* A complete owned Ethernet frame, no VLAN/FCS/aggregate or arbitrary pointer.
 * EAPOL 1..3 packet/start/logoff/key envelopes only. Crypto owner must still
 * check EAP/Key grammar, replay, MIC and hardware completion. Ethernet minimum
 * padding may be nonzero; never interpret it as EAPOL body or authentication.
 * Deliberately limits RSN-preauth to the actual current peer, not a cached AP
 * or off-channel future AP. Only unicast peer/local delivery is enabled here;
 * group Start/Logoff and other multicast remain unsupported by this board path.
 */
static inline int a04_network_control_frame(const void *frame,unsigned bytes,
    const uint8_t local[6],const uint8_t peer[6],unsigned transmit)
{
    if(!frame || !local || !peer || transmit>1 || bytes<18 || bytes>A04_NET_CONTROL_MAX_FRAME ||
        (uintptr_t)frame>UINTPTR_MAX-bytes)return EINVAL;
    if(!a04_network_control_mac(local) || !a04_network_control_mac(peer) ||
        a04_network_control_equal(local,peer))return ENETDOWN;
    const uint8_t *p=(const uint8_t *)frame;
    unsigned type=((unsigned)p[12]<<8)|p[13];
    if(type!=0x888e && type!=0x88c7)return EPROTONOSUPPORT;
    if(!a04_network_control_equal(p,transmit?peer:local) ||
        !a04_network_control_equal(p+6,transmit?local:peer))return EACCES;
    if(p[14]<1 || p[14]>3 || p[15]>3)return EPROTONOSUPPORT;
    unsigned body=((unsigned)p[16]<<8)|p[17],needed=18+body;
    if(needed>bytes || (needed!=bytes && bytes!=60))return EINVAL;
    if((p[15]==1 || p[15]==2) && body)return EINVAL;
    if((p[15]==0 && body<4) || (p[15]==3 && body<1))return EINVAL;
    return 0;
}
#endif
