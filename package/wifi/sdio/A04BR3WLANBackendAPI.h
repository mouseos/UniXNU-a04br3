/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * SDIO-owned kernel interface for the native bridge owner. No private ioctl
 * numbers or Apple payload layouts are guessed. Acquire ONLY succeeds after
 * net_init and real MAC/control discovery; release after BSD detach finishes.
 */
#ifndef A04BR3_WLAN_BACKEND_API_H
#define A04BR3_WLAN_BACKEND_API_H
#define A04_WLAN_SCAN_MODE_API 1
#define A04_WLAN_JOIN_TARGET_API 1
#include "A04BR3WLANData.h"
#include "A04BR3WLANScanSnapshot.h"
#include "A04BR3WLANJoinTarget.h"
struct a04_psk_prepared;
#include "../network/A04BR3NetworkBackend.h"
#ifdef __cplusplus
class IOService;
typedef IOService *a04_wlan_service;
extern "C" {
#else
typedef void *a04_wlan_service;
#endif
struct a04_wlan_native_client {
 a04_wlan_service owner;void *context;
 /* Driver retains owner per callback. Notification/packet callbacks run
  * outside the SDIO lock, may call typed APIs, and must copy packet bytes.
  * ready is delivered only after net_init + dynamic original calibration +
  * real MAC + successful command setup. It is NOT an association callback. */
 void (*ready)(void *,a04_wlan_service);
 void (*changed)(void *);
 void (*packet)(void *,unsigned,const void *,unsigned);
 /* These run in the bridge's gate; do not reenter refresh()/ioctl().
  * Implement only retained/proven native payload ABIs, unknown -> errno45. */
 int (*get)(void *,uint32_t,uint32_t,void *,uint32_t *);
 int (*set)(void *,uint32_t,uint32_t,const void *,uint32_t);
 /* Crypto-only verifier; runs under SDIO lock and MUST NOT reenter driver.
  * Actual native supplicant owns KCK/GTK crypto, no success stub is permitted. */
 a04_wlan_mic_verify verify;
 int (*authenticated)(void *,uint64_t); /* 0 only after link + auth notification published; retry otherwise */
};
int a04br3_wlan_acquire_backend(a04_wlan_service,struct a04_network_backend *);
void a04br3_wlan_release_backend(a04_wlan_service);
int a04br3_wlan_bind_native(a04_wlan_service,const struct a04_wlan_native_client *);
int a04br3_wlan_unbind_native(a04_wlan_service);
/* Legacy entry remains explicit PASSIVE. Mode API is one-shot unfiltered2G4:
 * 0 PASSIVE /1 ACTIVE, canonical native normal active1 maps to mode1.
 * Cached native policy3 is NOT an RF mode and MUST NOT call this API.
 * EBUSY if another scan/join is pending; no cancellation/retry/coalescing. */
int a04br3_wlan_scan(a04_wlan_service);
/* acceptedGeneration is required. Success writes accepted nonzero generation
 * under the SAME transport lock as start; every error writes0. No subsequent
 * info read is needed to identify the accepted scan. */
int a04br3_wlan_scan_mode(a04_wlan_service,unsigned mode,unsigned *acceptedGeneration);
int a04br3_wlan_scan_count(a04_wlan_service,unsigned *count,unsigned *complete);
int a04br3_wlan_scan_copy(a04_wlan_service,unsigned index,struct a04_wlan_ap *);
/* Snapshot info is atomic under SDIO lock. A new successful scan increments
 * generation; copy_record rejects a pending or changed generation with EAGAIN.
 * Caller owns record storage (about3KiB); allocate it off the kernel stack. */
int a04br3_wlan_scan_snapshot(a04_wlan_service,struct a04_wlan_scan_info *);
int a04br3_wlan_scan_copy_record(a04_wlan_service,unsigned generation,unsigned index,struct a04_wlan_scan_record *);
/* Explicit native association request for an ACTUALLY observed result. */
int a04br3_wlan_join(a04_wlan_service,unsigned index,unsigned secured);
/* Open-only admission; generation+index+identity validated atomically with
 * station_select. No cached index race and no association-success return. */
int a04br3_wlan_join_target(a04_wlan_service,const struct a04_wlan_join_target *);
/* Prepared BEFORE association/SDIO lock; consumed and wiped on every result. */
int a04br3_wlan_join_personal(a04_wlan_service,const struct a04_wlan_join_target *,struct a04_psk_prepared *);
/* Open-only SET22 admission. Real deauth TX_DONE precedes associated teardown.
 * Scan/data/key work in flight is rejected, never erased to claim completion.
 * Completed teardown quarantines indices and allocates a distinct context;
 * ENOSPC is explicit after device resource budget, no silent index recycling.
 */
int a04br3_wlan_disassociate(a04_wlan_service);
/* Only actual association permits this peer/observed-IE snapshot. */
int a04br3_wlan_current_record(a04_wlan_service,struct a04_wlan_scan_record *);
int a04br3_wlan_eapol(a04_wlan_service,const void *ethernet,unsigned bytes);
int a04br3_wlan_install_key(a04_wlan_service,unsigned pairwise,unsigned keyid,const uint8_t key[16],const uint8_t rsc[8]);
/* Only real authenticated native supplicant completion, never a key ioctl. */
int a04br3_wlan_authorize(a04_wlan_service,int authenticated_result);
#ifdef __cplusplus
}
#endif
#endif
