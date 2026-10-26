/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#include "A04BR3Ethernet.h"
#include "A04BR3Apple80211SET.h"
#include <sys/errno.h>
#include <sys/sockio.h>
#include <sys/kpi_mbuf.h>
#include <net/if.h>
OSDefineMetaClassAndStructors(IO80211Interface,IOEthernetInterface);
OSDefineMetaClassAndStructors(IO80211Controller,IOEthernetController);

bool IO80211Interface::initBackend(IONetworkController *controller,const a04_network_backend *b)
{
    if(!b || !b->state)return false;
    backend=*b;
    if(!IOEthernetInterface::init(controller))return false;
    return setInterfaceSubType(IFNET_SUBFAMILY_WIFI);
}

IOReturn IO80211Interface::attachToDataLinkLayer(IOOptionBits,void *)
{
    /* Use the documented virtual extension point. Do NOT call Apple's default
     * attach here: it would allocate another ifnet and a different softc.
     * IONetworkStack owns name/unit assignment and registered-state transition. */
    int error=network.attach(this,&backend,getUnitNumber(),adopt,this);
    if(error){
        // Allocation/attach failure: bridge dropped its original reference.
        // Any base-adoption reference still needs its one matching release.
        if(adopted){(void)clearExistingIfnet(adopted);adopted=nullptr;}
        IOLog("A04BR3 network: genuine interface DLIL attach errno=%d\n",error);return kIOReturnError;
    }
    return kIOReturnSuccess;
}

void IO80211Interface::detachFromDataLinkLayer(IOOptionBits,void *)
{
    /* Apple invokes this in sleepable thread-call context. No busy wait and
     * no release of callback context before real DLIL detach completes. */
    int error=shutdownBackend();
    if(error)IOLog("A04BR3 network: genuine interface detach errno=%d; live bridge retained\n",error);
}

int IO80211Interface::adopt(void *context,ifnet_t interface)
{
    auto *self=(IO80211Interface *)context;
    IOReturn result=self->adoptExistingIfnet(interface);
    if(result!=kIOReturnSuccess)return EIO;
    self->adopted=interface;return 0;
}
int IO80211Interface::shutdownBackend()
{
    int error=network.detachAndWait();if(error)return error;
    if(adopted){
        IOReturn result=clearExistingIfnet(adopted);
        if(result!=kIOReturnSuccess)return EIO;
        adopted=nullptr;
    }
    return 0;
}
bool IO80211Interface::setFlags(UInt16 flags,UInt16 clear)
{
    // Real radio/admin + association/datapath are owned by the bridge. Apple's
    // power/filter path must not turn radio-enabled into premature RUNNING.
    if(getIfnet()){flags&=~(IFF_UP|IFF_RUNNING);clear&=~(IFF_UP|IFF_RUNNING);}
    return IOEthernetInterface::setFlags(flags,clear);
}
SInt32 IO80211Interface::performCommand(IONetworkController *controller,unsigned long command,void *arg0,void *arg1)
{
    if(command==A04_APPLE80211_GET64 || command==A04_APPLE80211_SET64 || command==SIOCSIFFLAGS ||
       command==SIOCGIFMEDIA32 || command==SIOCGIFMEDIA64 || command==SIOCSIFMEDIA)
        return network.dispatch(command,arg1);
    return IOEthernetInterface::performCommand(controller,command,arg0,arg1);
}

bool IO80211Controller::configureBackend(const a04_network_backend *b)
{
    if(configured || !b || !b->state)return false;
    a04_network_state state={};
    if(b->state(b->context,&state) || !a04_network_mac_valid(&state))return false;
    backend=*b;configured=true;return true;
}

bool IO80211Controller::start(IOService *provider)
{
    if(!configured || !IOEthernetController::start(provider))return false;
    // A published nub alone waits for user-space InterfaceNamer. Board startup
    // must first enter the real Stack naming/attach path synchronously.
    if(!attachInterface(&interface,false)){
        IOEthernetController::stop(provider);return false;
    }
    return true;
}
void IO80211Controller::stop(IOService *provider)
{
    int error=shutdownBackend();
    if(error){IOLog("A04BR3 network: controller stop deferred errno=%d; backend must remain alive\n",error);return;}
    IOEthernetController::stop(provider);
}
IONetworkInterface *IO80211Controller::createInterface()
{
    auto *child=new IO80211Interface;
    if(child && !child->initBackend(this,&backend)){child->release();return nullptr;}
    return child;
}
IOReturn IO80211Controller::getHardwareAddress(IOEthernetAddress *address)
{
    if(!configured || !address)return kIOReturnBadArgument;
    a04_network_state state={};
    if(backend.state(backend.context,&state) || !a04_network_mac_valid(&state))return kIOReturnNotReady;
    memcpy(address->bytes,state.mac,6);return kIOReturnSuccess;
}
IOReturn IO80211Controller::getPacketFilters(UInt32 *filters) const
{
    if(!filters)return kIOReturnBadArgument;
    /* Actual A04BR3WLANStation::setup submits CID0x0a mask13, including
     * directed+broadcast. Advertise only those mandatory, always-on filters;
     * optional multicast/promiscuous/wake control is not implemented here. */
    *filters=kIOPacketFilterUnicast|kIOPacketFilterBroadcast;return kIOReturnSuccess;
}
IOReturn IO80211Controller::enablePacketFilter(const OSSymbol *group,
    UInt32 filter,UInt32,IOOptionBits)
{
    if(group!=gIONetworkFilterGroup ||
        (filter!=kIOPacketFilterUnicast && filter!=kIOPacketFilterBroadcast))return kIOReturnUnsupported;
    a04_network_state state={};
    if(!configured || backend.state(backend.context,&state) || !a04_network_control_ready(&state))return kIOReturnNotReady;
    // These baseline filters are already enabled by real station setup. No
    // synthetic firmware transaction or optional-mode success is returned.
    return kIOReturnSuccess;
}
static IOReturn a04_radio(const a04_network_backend *b,unsigned enable)
{
    a04_network_state state={};
    if(!b->state || b->state(b->context,&state))return kIOReturnNotReady;
    if(enable && !a04_network_control_ready(&state))return kIOReturnNotReady;
    if(!b->set_admin)return kIOReturnUnsupported;
    return b->set_admin(b->context,enable)?kIOReturnError:kIOReturnSuccess;
}
IOReturn IO80211Controller::enable(IONetworkInterface *) { return a04_radio(&backend,1); }
IOReturn IO80211Controller::disable(IONetworkInterface *) { return a04_radio(&backend,0); }
int IO80211Controller::shutdownBackend()
{
    auto *child=OSDynamicCast(IO80211Interface,interface);
    if(child){int error=child->shutdownBackend();if(error)return error;}
    if(interface){detachInterface(interface,true);interface=nullptr;}
    return 0;
}
int IO80211Controller::refreshBackend()
{
    auto *child=OSDynamicCast(IO80211Interface,interface);
    return child?child->refreshBackend():ENXIO;
}
int IO80211Controller::publishedLink(uint64_t generation)
{
    auto *child=OSDynamicCast(IO80211Interface,interface);
    return child?child->publishedLink(generation):ENXIO;
}
int IO80211Controller::scanCompleted()
{
    auto *child=OSDynamicCast(IO80211Interface,interface);
    return child?child->scanCompleted():ENXIO;
}
int IO80211Controller::receive(mbuf_t m)
{
    auto *child=OSDynamicCast(IO80211Interface,interface);
    if(!child){if(m)mbuf_freem(m);return ENXIO;}
    return child->receive(m);
}
