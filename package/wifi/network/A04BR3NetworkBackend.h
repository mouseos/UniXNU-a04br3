/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Observed backend state only. No fabricated MAC, firmware, scan or link data.
 * Caller retains context until BSD detach has completed, and serializes backend
 * operations. Callbacks must not reenter this interface while its gate is held.
 */
#ifndef A04BR3_NETWORK_BACKEND_H
#define A04BR3_NETWORK_BACKEND_H
#include <stdint.h>
#include <stddef.h>
/* Wire-neutral phase, supplied from the real station state under its lock.
 * Zero is deliberately UNKNOWN for older/absent producers, never idle. */
enum a04_network_station_phase {
    A04_NET_PHASE_UNKNOWN=0,A04_NET_PHASE_IDLE=1,A04_NET_PHASE_SCAN=2,
    A04_NET_PHASE_AUTH=3,A04_NET_PHASE_ASSOC=4,A04_NET_PHASE_ASSOCIATED=5
};
/* Internal join observation, not an Apple enum. Zero is unknown, not success
 * or a pending transaction. Only a real open join can reach OPEN_COMPLETE. */
enum a04_network_join_state {
    A04_NET_JOIN_UNKNOWN=0,A04_NET_JOIN_NOT_STARTED=1,
    A04_NET_JOIN_PENDING=2,A04_NET_JOIN_OPEN_COMPLETE=3,A04_NET_JOIN_FAILED=4,
    A04_NET_JOIN_PERSONAL_COMPLETE=5
};
struct a04_network_state {
    /* firmware_running/control_ready describe WLAN firmware and real command
     * transport, not WMT patch delivery. Neither requires association. */
    unsigned present,mac_valid,firmware_running,control_ready,datapath_ready,associated;
    uint8_t mac[6];
    unsigned radio_enabled; /* observed runtime radio/admin state, not association */
    unsigned station_phase; /* enum above; UNKNOWN must not produce INIT */
    unsigned permitted_channels; /* actual enforced 2.4GHz mask; zero=unknown */
    unsigned join_state; /* wire-neutral enum; actual station under transportLock */
    unsigned preauth_ready,authorized,auth_profile_valid;
    unsigned auth_lower,auth_upper,auth_upper_ext;
    uint64_t association_generation; /* actual admitted owner session, not port state */
};
struct a04_network_backend {
    void *context;
    int (*state)(void *,struct a04_network_state *);
    /* Native GET payload is opaque, bounded and initially copied from user.
     * Observed scalar GET5/8/13/15/18/21/57/96 is normalized by the BSD adapter to an internal
     * four-byte little-endian output buffer (wire length0, result req.value).
     * This is our adapter representation, not a new SDIO or Apple payload ABI.
     * Return errno, and actual initialized output length <= supplied capacity.
     * Unsupported operations MUST return EOPNOTSUPP; never synthesize results. */
    int (*native_get)(void *,uint32_t type,uint32_t value,void *,uint32_t *bytes);
    /* Enables/disables radio control before scan/association. Success means
     * that command succeeded, never that association or IP connectivity exists.
     * Callbacks may update state; bridge resamples after this operation. */
    int (*set_admin)(void *,unsigned enabled);
    /* A successful transmit consumes mbuf. On error leave it to the bridge.
     * Backend must queue promptly; no long firmware exchange in output(). */
    int (*transmit)(void *,void *mbuf);
    /* Opaque native SET, dispatched only after kernel credential check and
     * bounded copyin. No user pointer is passed. Input lasts for this call only;
     * asynchronous backend work must copy it. Return real command errno;
     * unsupported type/value MUST return EOPNOTSUPP, never synthetic success.
     * bytes may be zero (no payload); this does not imply any opcode meaning. */
    int (*native_set)(void *,uint32_t type,uint32_t value,const void *,uint32_t bytes);
    /* Strict actual-associated EAPOL only, independent of ordinary IP port.
     * Same mbuf ownership as transmit: consume on success, retain on error. */
    #ifdef __cplusplus
    int (*transmit_eapol)(void *,void *mbuf)=nullptr;
#else
    int (*transmit_eapol)(void *,void *mbuf);
#endif
};
static inline int a04_network_mac_valid(const struct a04_network_state *s)
{
    unsigned any=0;for(unsigned i=0;i<6;i++)any|=s->mac[i];
    return s->present && s->mac_valid && any && !(s->mac[0]&1);
}
static inline int a04_network_control_ready(const struct a04_network_state *s)
{return a04_network_mac_valid(s) && s->firmware_running && s->control_ready;}
static inline int a04_network_data_ready(const struct a04_network_state *s)
{return a04_network_control_ready(s) && s->datapath_ready && s->associated;}
static inline int a04_network_preauth_ready(const struct a04_network_state *s)
{return s && a04_network_control_ready(s) && s->associated && s->preauth_ready;}
#endif
