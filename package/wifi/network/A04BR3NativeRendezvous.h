/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#ifndef A04BR3_NATIVE_RENDEZVOUS_H
#define A04BR3_NATIVE_RENDEZVOUS_H
#include <IOKit/IOService.h>
/* Popper: bind once from SDIO start OUTSIDE transportLock, before deferLive.
 * Keep the returned owner reference in SDIO. On teardown first stop/drain live
 * callbacks, then call unbind outside transportLock. Nonzero unbind preserves
 * owner/context, because an actual DLIL interface may still be live. */
extern "C" int a04br3_network_bind(IOService *provider,IOService **owner);
extern "C" int a04br3_network_unbind(IOService *provider,IOService **owner);
#endif
