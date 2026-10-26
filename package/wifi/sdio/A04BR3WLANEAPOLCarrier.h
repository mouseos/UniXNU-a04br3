/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Ethernet/EAPOL-key boundary for the EXISTING MT6630 WPA2-CCMP backend.
 * This is a board-private adapter, not a claimed native Apple socket ABI.
 * No MIC/replay/authorization success is inferred from carrier validation.
 */
#ifndef A04BR3_WLAN_EAPOL_CARRIER_H
#define A04BR3_WLAN_EAPOL_CARRIER_H
#include "A04BR3WLANData.h"
struct a04_wlan_eapol_carrier {
    const uint8_t *frame,*eapol;
    unsigned frame_bytes,eapol_bytes,kind;
    uint64_t replay;
};
/* direction 0 RX (AP -> actual station), 1 TX (station -> actual AP).
 * Only existing descriptor-v2 pairwise handshake messages are supported.
 * Enterprise EAP, Start/Logoff, VLAN, multicast and rekey are not added here.
 */
static inline int a04_wlan_eapol_carrier_decode(const struct a04_wlan_station *sta,
    const uint8_t *frame,unsigned bytes,unsigned transmit,struct a04_wlan_eapol_carrier *out)
{
    if(!out)return A04_WLAN_ARGUMENT;
    out->frame=out->eapol=0;out->frame_bytes=out->eapol_bytes=out->kind=0;out->replay=0;
    if(!sta || !frame || transmit>1 || bytes<14 || bytes>1514 ||
        (uintptr_t)frame>UINTPTR_MAX-bytes)return A04_WLAN_ARGUMENT;
    if(!sta->sta_active || sta->failed || sta->join.phase!=4 || !sta->secured)
        return A04_WLAN_STATE;
    if(!a04_wlan_mac_valid(sta->local.mac) || !a04_wlan_mac_valid(sta->ap.mac))return A04_WLAN_STATE;
    if(a04_wlan_be16(frame+12)!=0x888e)return A04_WLAN_UNSUPPORTED;
    const uint8_t *source=transmit?sta->local.mac:sta->ap.mac;
    const uint8_t *destination=transmit?sta->ap.mac:sta->local.mac;
    if(!a04_wlan_equal(frame, destination,6) || !a04_wlan_equal(frame+6,source,6))return A04_WLAN_SEQUENCE;
    struct a04_wlan_eapol parsed;int error=a04_wlan_eapol_parse(frame+14,bytes-14,&parsed);
    if(error)return error;
    if(transmit?(parsed.kind!=2 && parsed.kind!=4):(parsed.kind!=1 && parsed.kind!=3))return A04_WLAN_SEQUENCE;
    out->frame=frame;out->frame_bytes=bytes;out->eapol=frame+14;out->eapol_bytes=bytes-14;
    out->kind=parsed.kind;out->replay=parsed.replay;return 0;
}
/* This preflight cannot authorize or advance the handshake. The actual
 * transport still performs its own security_tx_check and advances ONLY on
 * genuine TX_DONE / ADD_PKEY_DONE. Null/failed cryptographic verifier -> error.
 * send must synchronously copy the frame into the existing bounded queue.
 * Return codes are the existing A04_WLAN domain; send's error is preserved.
 */
struct a04_wlan_eapol_sender {
    void *context;
    int (*send)(void *,const uint8_t *,unsigned);
};
static inline int a04_wlan_eapol_carrier_send(const struct a04_wlan_station *sta,
    struct a04_wlan_security *sec,const uint8_t *frame,unsigned bytes,
    a04_wlan_mic_verify verify,void *verify_context,uint64_t now,
    const struct a04_wlan_eapol_sender *sender)
{
    struct a04_wlan_eapol_carrier carrier;
    int error=a04_wlan_eapol_carrier_decode(sta,frame,bytes,1,&carrier);if(error)return error;
    if(!sec || !verify || !sender || !sender->send)return A04_WLAN_ARGUMENT;
    if(!a04_wlan_port_allowed(sta,sec,1))return A04_WLAN_SECURITY;
    unsigned kind=0;
    error=a04_wlan_security_tx_check(sec,carrier.eapol,carrier.eapol_bytes,
        verify,verify_context,now,&kind);
    if(error)return error;
    return sender->send(sender->context,frame,bytes);
}
#endif
