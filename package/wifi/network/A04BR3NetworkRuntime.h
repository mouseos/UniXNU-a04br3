/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#ifndef A04BR3_NETWORK_RUNTIME_H
#define A04BR3_NETWORK_RUNTIME_H
#include "A04BR3Ethernet.h"
/* Called once from the board's serialized startup work context after runtime
 * NVRAM validation and BSD initialization. No per-user extraction/config/build.
 * Provider/backend context live for the boot, including asynchronous callbacks.
 * The real Apple IONetworkStack service remains a boot-lifetime singleton.
 * On error no controller is returned; errno is set. On success caller owns the
 * initial controller reference. Use stop before detach/release during teardown. */
A04BR3EthernetController *a04br3_network_start(IOService *provider,
    const a04_network_backend *backend,int *error);
/* Native kind2 callback entrance: borrowed COMPLETE Ethernet control frame,
 * 18..1514 bytes, synchronously copied into an owned DLIL mbuf with header14
 * preserved in headroom. Caller serializes controller lifetime/teardown.
 * No authorization/handshake completion is implied. BSD bridge revalidates
 * genuine association and backend.native_get(GET9,value0)->actual peer6 while
 * data port is closed; missing/stale/cached-only peer must be an error.
 */
int a04br3_network_control_input(A04BR3EthernetController *controller,
    const void *ethernet,unsigned bytes);
#endif
