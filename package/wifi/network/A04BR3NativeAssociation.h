/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Strict original iOS16 __performAssociation carrier, not macOS sizeof().
 * Root592 bytes: version0/u32, mode4/u16, lower-auth6/u16, upper-auth8/u16,
 * SSIDlen12/u32, SSID16/32bytes, forcedBSSID48/6bytes, flags463/byte.
 * Only observed open-system/infrastructure defaults and ForceBSSID0x20 are
 * supported. Key/private-MAC/IE/unknown extensions must remain zero.
 */
#ifndef A04BR3_NATIVE_ASSOCIATION_H
#define A04BR3_NATIVE_ASSOCIATION_H
#include "A04BR3NativeAssociationCodec.h"
#include "../sdio/A04BR3WLANJoinTarget.h"
#include <string.h>
#include <sys/errno.h>
struct a04_association_backend {
 void *context;
 int (*info)(void *,struct a04_wlan_scan_info *);
 int (*record)(void *,unsigned,unsigned,struct a04_wlan_scan_record *);
 int (*join)(void *,const struct a04_wlan_join_target *);
};
/* work is heap-owned by the caller (~3KiB); never put a scan record on the
 * kernel stack. No pointer escapes a callback. Join revalidates the exact
 * generation/index/identity under the real transport lock, then queues work.
 * Success is admission only; actual completion is polled separately by GET21.
 */
static inline int a04_native_association_set(const struct a04_association_backend *backend,
 uint32_t value,const void *in,uint32_t bytes,struct a04_wlan_scan_record *work)
{
 struct a04_native_association request={};
 int error=a04_native_association_decode(value,in,bytes,&request);if(error)return error;
 if(!backend || !backend->info || !backend->record || !backend->join || !work)return EINVAL;
 struct a04_wlan_scan_info info={};error=backend->info(backend->context,&info);if(error)return error;
 if(info.error)return EIO;
 if(!info.generation || !info.complete)return EAGAIN;
 if(info.count>16)return EOVERFLOW;
 struct a04_wlan_join_target target={};unsigned matched=0;
 for(unsigned index=0;index<info.count;index++){
  error=backend->record(backend->context,info.generation,index,work);if(error)return error;
  if(work->observation.generation!=info.generation)return ESTALE;
  const struct a04_wlan_ap *ap=&work->ap;
  if(ap->ssid_length!=request.ssid_length || memcmp(ap->ssid,request.ssid,request.ssid_length) ||
     (request.force_bssid && memcmp(ap->mac,request.bssid,6)))continue;
  if((ap->capability&16) || ap->rsn_length)return EOPNOTSUPP;
  if(++matched>1)return EEXIST; // no guessed roam/strongest-AP policy
  target.generation=info.generation;target.index=index;target.ssid_length=ap->ssid_length;
  target.channel=ap->channel;target.band=ap->band;memcpy(target.bssid,ap->mac,6);
  memcpy(target.ssid,ap->ssid,ap->ssid_length);
 }
 if(!matched)return ENOENT;
 return backend->join(backend->context,&target);
}
#endif
