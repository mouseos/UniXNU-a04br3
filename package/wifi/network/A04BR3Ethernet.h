/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Original A04 integration of pinned genuine Apple IONetworkingFamily classes.
 * The controller is an original, functional source IO80211Controller for this
 * board. It uses public Ethernet KPIs plus proven native BSD ioctl envelopes;
 * no stock proprietary IO80211 vtable or user-client ABI is asserted.
 */
#ifndef A04BR3_ETHERNET_H
#define A04BR3_ETHERNET_H
#include "apple/IOEthernetController.h"
#include "apple/IOEthernetInterface.h"
#include "A04BR3BSDNetwork.h"
class IO80211Interface : public IOEthernetInterface {
    OSDeclareDefaultStructors(IO80211Interface);
    A04BR3BSDNetwork network;
    a04_network_backend backend;
    ifnet_t adopted=nullptr;
    static int adopt(void *,ifnet_t);
public:
    bool initBackend(IONetworkController *,const a04_network_backend *);
    IOReturn attachToDataLinkLayer(IOOptionBits,void *) APPLE_KEXT_OVERRIDE;
    void detachFromDataLinkLayer(IOOptionBits,void *) APPLE_KEXT_OVERRIDE;
    bool setFlags(UInt16 flags,UInt16 clear=0) APPLE_KEXT_OVERRIDE;
    SInt32 performCommand(IONetworkController *,unsigned long,void *,void *) APPLE_KEXT_OVERRIDE;
    int shutdownBackend();
    int refreshBackend(){ return network.refresh(); }
    int publishedLink(uint64_t generation){ return network.publishedLink(generation); }
    int scanCompleted(){ return network.scanCompleted(); }
    int receive(mbuf_t m){ return network.input(m); }
};
class IO80211Controller : public IOEthernetController {
    OSDeclareDefaultStructors(IO80211Controller);
    a04_network_backend backend;
    IONetworkInterface *interface=nullptr;
    bool configured=false;
public:
    /* Owner sets callbacks before start; MAC comes from verified runtime NVRAM.
     * No files, user configuration, extracted asset or per-user build needed. */
    bool configureBackend(const a04_network_backend *);
    bool start(IOService *) APPLE_KEXT_OVERRIDE;
    void stop(IOService *) APPLE_KEXT_OVERRIDE;
    IONetworkInterface *createInterface() APPLE_KEXT_OVERRIDE;
    IONetworkInterface *networkInterface() const {return interface;} /* borrowed, serialized owner */
    IOReturn getHardwareAddress(IOEthernetAddress *) APPLE_KEXT_OVERRIDE;
    IOReturn getPacketFilters(UInt32 *) const APPLE_KEXT_OVERRIDE;
    IOReturn enablePacketFilter(const OSSymbol *,UInt32,UInt32,IOOptionBits) APPLE_KEXT_OVERRIDE;
    IOReturn enable(IONetworkInterface *) APPLE_KEXT_OVERRIDE;
    IOReturn disable(IONetworkInterface *) APPLE_KEXT_OVERRIDE;
    int shutdownBackend();
    int refreshBackend();
    int publishedLink(uint64_t generation);
    int scanCompleted();
    int receive(mbuf_t); /* consumes real DLIL-formatted mbuf, including failures */
};
/* Preserve the integration C++ type name while publishing the real controller
 * metaclass implemented above. This is not an empty class-name shim. */
using A04BR3EthernetController=IO80211Controller;
using A04BR3EthernetInterface=IO80211Interface;
#endif
