/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#include "A04BR3NetworkRuntime.h"
#include "apple/IONetworkStack.h"
#include <sys/errno.h>
#include <sys/kpi_mbuf.h>
#include "A04BR3NetworkControlFrame.h"
/* Board startup is serialized and only one MSDC2 radio exists. Apple's stack
 * itself rejects a second singleton. Keep a real stack reference for the boot,
 * rather than manufacturing a resource or a successful naming notification. */
static IONetworkStack *a04Stack;
A04BR3EthernetController *a04br3_network_start(IOService *provider,
    const a04_network_backend *backend,int *error)
{
    if(error)*error=EINVAL;
    if(!provider || !backend || !error)return nullptr;
    auto *controller=new A04BR3EthernetController;
    if(!controller){*error=ENOMEM;return nullptr;}
    if(!controller->init(nullptr)){controller->release();*error=ENOMEM;return nullptr;}
    if(!controller->configureBackend(backend)){controller->release();*error=ENXIO;return nullptr;}
    if(!a04Stack){
        auto *stack=new IONetworkStack;
        if(!stack){controller->release();*error=ENOMEM;return nullptr;}
        if(!stack->init(nullptr)){stack->release();controller->release();*error=ENOMEM;return nullptr;}
        if(!stack->attach(provider)){stack->release();controller->release();*error=ENXIO;return nullptr;}
        if(!stack->start(provider)){
            stack->detach(provider);stack->release();controller->release();*error=EIO;return nullptr;
        }
        a04Stack=stack;
    }
    if(!controller->attach(provider)){controller->release();*error=ENXIO;return nullptr;}
    if(!controller->start(provider)){
        controller->detach(provider);controller->release();*error=EIO;return nullptr;
    }
    IOReturn registered=a04Stack->registerKernelInterface(controller->networkInterface(),0);
    if(registered!=kIOReturnSuccess || !controller->networkInterface()->getIfnet()){
        IOLog("A04BR3 network: Stack registration failed result=0x%x; no live BSD interface\n",(unsigned)registered);
        controller->stop(provider);controller->detach(provider);controller->release();
        *error=EIO;return nullptr;
    }
    // Success now requires the actual Stack reserve/open/attach path and a
    // live shared ifnet, not merely controller/nub allocation. No fake state.
    IOLog("A04BR3 network: Stack registration complete; real shared BSD interface attached\n");
    *error=0;
    return controller;
}

/* No private socket or alternate listener is manufactured. Existing ether_demux
 * on the controller's shared ifnet delivers to real AF_NDRV registrations. */
int a04br3_network_control_input(A04BR3EthernetController *controller,
    const void *ethernet,unsigned bytes)
{
    if(!controller || !ethernet || bytes<18 || bytes>A04_NET_CONTROL_MAX_FRAME ||
        (uintptr_t)ethernet>UINTPTR_MAX-bytes)return EINVAL;
    const uint8_t *p=(const uint8_t *)ethernet;
    unsigned type=((unsigned)p[12]<<8)|p[13];
    if(type!=0x888e && type!=0x88c7)return EPROTONOSUPPORT;
    mbuf_t m=nullptr;
    int error=mbuf_allocpacket(MBUF_DONTWAIT,bytes,nullptr,&m);
    if(error)return error;
    if(!m)return ENOMEM;
    error=mbuf_copyback(m,0,bytes,ethernet,MBUF_DONTWAIT);
    if(!error && mbuf_len(m)<14)error=EINVAL;
    if(error){mbuf_freem(m);return error;}
    mbuf_pkthdr_setheader(m,mbuf_data(m));
    mbuf_adj(m,14);
    return controller->receive(m); // consumes on EVERY return, including failure
}
