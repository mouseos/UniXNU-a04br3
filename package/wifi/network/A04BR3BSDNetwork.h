/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#ifndef A04BR3_BSD_NETWORK_H
#define A04BR3_BSD_NETWORK_H
#include <IOKit/IOLib.h>
#include <IOKit/IOService.h>
#include <net/kpi_interface.h>
#include "A04BR3NetworkBackend.h"
/* Owner supplies an attached real IOService child with a provider parent.
 * Object and backend context must live until isDetached() after detach().
 * This class is not an IO80211Interface/IONetworkingFamily vtable substitute.
 */
class A04BR3BSDNetwork {
public:
    A04BR3BSDNetwork();
    A04BR3BSDNetwork(const A04BR3BSDNetwork &)=delete;
    A04BR3BSDNetwork &operator=(const A04BR3BSDNetwork &)=delete;
    typedef int (*AdoptIfnet)(void *,ifnet_t);
    int attach(IOService *registry,const a04_network_backend *backend,unsigned unit,
        AdoptIfnet adopt=nullptr,void *context=nullptr);
    /* Creates a real IOService child attached to provider; registers it only
     * after successful BSD attach. Does not claim IONetworkInterface ancestry. */
    int attachProvider(IOService *provider,const a04_network_backend *backend,unsigned unit);
    IOService *registryEntry() const { return registry; } /* borrowed; owner serialized */
    /* Consumes packet on every return. Caller supplies a real Ethernet RX mbuf
     * in DLIL format, with pkt-header frame pointer and payload adjusted for
     * ether_demux. No raw firmware descriptor may be passed here. */
    int input(mbuf_t packet);
    int detachAndWait(); /* sleepable owner context, never under backend lock */
    ifnet_t ifnet() const { return interface; } /* borrowed; owner serialized */
    int dispatch(unsigned long command,void *data);
    int refresh();
    /* Snapshot only: current admin/link publication for this exact session.
     * Does not publish/promote flags or grant a port; no lock spans clients. */
    int publishedLink(uint64_t associationGeneration);
    int scanCompleted(); /* caller must have observed real matching FW completion */
    int detach();
    bool isDetached();
    int finish(); /* releases retained registry/gate only after detach callback */
private:
    ifnet_t interface;
    IOLock *gate;
    IOService *registry;
    a04_network_backend backend;
    bool stopping,admin,ownedChild;
    bool linkReported,linkPublishing;
    uint64_t linkGeneration;
    void publishIdentity();
    uint8_t mac[6];
    char name[16];
    int snapshot(a04_network_state *);
    /* Gate held: only real associated/radio/control-ready peer traffic may
     * bypass the closed authorized data port. GET9 must return live peer6. */
    int preauth(const a04_network_state *,mbuf_t,bool transmit);
    static errno_t output(ifnet_t,mbuf_t);
    static errno_t ioctl(ifnet_t,unsigned long,void *);
    static void detached(ifnet_t);
};
#endif
