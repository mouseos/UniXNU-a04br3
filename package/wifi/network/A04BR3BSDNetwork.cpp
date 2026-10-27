/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Real DLIL interface, using XNU's existing Ethernet family/plumber. Original
 * implementation using the APIs referenced in SOURCES.json; no vendor binary
 * ABI or private IO80211 class is copied/emulated here.
 */
#include "A04BR3BSDNetwork.h"
#include "A04BR3Apple80211IOCTL.h"
#include "A04BR3Apple80211SET.h"
#include "A04BR3WiFiMedia.h"
#include "A04BR3NetworkControlFrame.h"
#include <sys/kauth.h>
#include <libkern/OSAtomic.h>
#include <sys/kern_event.h>
#include <kern/thread.h>
#include <sys/systm.h>
#include <sys/sockio.h>
#include <sys/kpi_mbuf.h>
#include <net/if.h>
#include <net/net_kev.h>
#include <net/if_dl.h>
#include <net/if_types.h>
#include <net/if_ether.h>
#include <net/ethernet.h>

/* Boot-wide, best-effort metadata budget shared across interface reattach.
 * Record each (command,type,errno) once, maximum32. Repeated successful power
 * queries cannot hide later scan/control selectors. No payload or identifiers.
 * Try-lock only: contention may drop diagnostics, never delay an ioctl. The
 * table is published under real OSAtomic + memory barriers; IOLog runs outside
 * that short critical section and never reenters with the guard held. */
struct a04_native_trace_key { unsigned long command;uint32_t type;int error; };
static a04_native_trace_key a04_native_trace_keys[32]={};
static volatile SInt32 a04_native_trace_count=0,a04_native_trace_busy=0;
static void a04_native_trace_unlock()
{
    OSMemoryBarrier();(void)OSCompareAndSwap(1,0,&a04_native_trace_busy);
}
static void a04_native_trace(unsigned long command,uint32_t type,uint32_t value,uint32_t length,int error)
{
    if(!OSCompareAndSwap(0,1,&a04_native_trace_busy))return;
    OSMemoryBarrier();
    SInt32 used=OSAddAtomic(0,&a04_native_trace_count);
    if(used>=32){a04_native_trace_unlock();return;}
    for(SInt32 i=0;i<used;i++){
        const auto &key=a04_native_trace_keys[i];
        if(key.command==command && key.type==type && key.error==error){a04_native_trace_unlock();return;}
    }
    a04_native_trace_keys[used]={command,type,error};
    (void)OSAddAtomic(1,&a04_native_trace_count);a04_native_trace_unlock();
    IOLog("A04BR3 native ioctl: command=0x%lx type=%u value=%u length=%u errno=%d\n",command,type,value,length,error);
}

static int a04_network_copyin(void *,uint64_t address,void *out,unsigned bytes)
{return copyin((user_addr_t)address,out,bytes);}
static int a04_network_copyout(void *,const void *in,uint64_t address,unsigned bytes)
{return copyout(in,(user_addr_t)address,bytes);}

A04BR3BSDNetwork::A04BR3BSDNetwork():interface(nullptr),gate(nullptr),registry(nullptr),
    backend{},stopping(false),admin(false),ownedChild(false),linkReported(false),linkPublishing(false),linkGeneration(0),mac{},name{} {}

int A04BR3BSDNetwork::snapshot(a04_network_state *s)
{
    *s={};
    if(stopping || !backend.state)return ENXIO;
    int error=backend.state(backend.context,s);
    if(error)return error;
    if(!a04_network_mac_valid(s) || memcmp(mac,s->mac,6))return ENXIO;
    return 0;
}

int A04BR3BSDNetwork::attach(IOService *service,const a04_network_backend *b,unsigned unit,
    AdoptIfnet adopt,void *context)
{
    if(gate || !service || !service->getProvider() || !b || !b->state || unit>999999)return EINVAL;
    a04_network_state state={};int error=b->state(b->context,&state);
    if(error)return error;
    if(!a04_network_mac_valid(&state))return ENXIO;
    gate=IOLockAlloc();if(!gate)return ENOMEM;
    backend=*b;memcpy(mac,state.mac,6);registry=service;registry->retain();
    struct ifnet_init_eparams init={};
    init.ver=IFNET_INIT_CURRENT_VERSION;init.len=sizeof(init);init.flags=IFNET_INIT_LEGACY;
    init.uniqueid=mac;init.uniqueid_len=6;init.name="en";init.unit=unit;
    init.family=IFNET_FAMILY_ETHERNET;init.type=IFT_ETHER;init.subfamily=IFNET_SUBFAMILY_WIFI;
    init.output=output;init.demux=ether_demux;init.add_proto=ether_add_proto;init.del_proto=ether_del_proto;
    init.check_multi=ether_check_multi;init.framer_extended=ether_frameout_extended;
    init.softc=this;init.ioctl=ioctl;init.detach=detached;
    static const uint8_t broadcast[6]={255,255,255,255,255,255};init.broadcast_addr=broadcast;init.broadcast_len=6;
    error=ifnet_allocate_extended(&init,&interface);
    if(error){interface=nullptr;finish();return error;}
    snprintf(name,sizeof(name),"%s%u",ifnet_name(interface),ifnet_unit(interface));
    error=ifnet_set_mtu(interface,1500);
    // This bridge bypasses Apple's default attach, whose media configuration
    // normally sets these fields. ndrv_input prepends ifnet_hdrlen(ifp), so
    // leaving zero would silently strip the native socket's Ethernet header.
    if(!error)error=ifnet_set_addrlen(interface,6);
    if(!error)error=ifnet_set_hdrlen(interface,14);
    if(!error)error=ifnet_set_flags(interface,IFF_BROADCAST|IFF_SIMPLEX|IFF_MULTICAST,
        IFF_BROADCAST|IFF_SIMPLEX|IFF_MULTICAST|IFF_UP|IFF_RUNNING);
    struct sockaddr_dl address={};address.sdl_len=sizeof(address);address.sdl_family=AF_LINK;
    address.sdl_type=IFT_ETHER;address.sdl_alen=6;memcpy(LLADDR(&address),mac,6);
    /* Base interface adoption happens before DLIL attach/publication. Hook
     * must take its own reference; bridge retains original allocation ref. */
    if(!error && adopt)error=adopt(context,interface);
    if(!error)error=ifnet_attach(interface,&address);
    if(error){ifnet_release(interface);interface=nullptr;finish();return error;}
    /* Publish only after the real BSD interface exists, never claim connected. */
    publishIdentity();
    registry->setProperty("WLANBackendReady",false);
    registry->setProperty("WLANDataPathReady",false);
    registry->registerService();
    int status=refresh();
    if(status)IOLog("A04BR3 network: interface attached, backend error=%d; not ready\n",status);
    return 0;
}

void A04BR3BSDNetwork::publishIdentity()
{
    registry->setProperty("IOInterfaceName",name);
    registry->setProperty("IOBSDName",name);
    registry->setProperty("IOInterfaceNamePrefix",ifnet_name(interface));
    registry->setProperty("IOInterfaceUnit",(uint64_t)ifnet_unit(interface),32);
    registry->setProperty("IOInterfaceType",(uint64_t)IFT_ETHER,8);
    registry->setProperty("IOMACAddress",(void *)mac,6);
    registry->setProperty("IO80211InterfaceRole","Infrastructure");
}

int A04BR3BSDNetwork::attachProvider(IOService *provider,const a04_network_backend *b,unsigned unit)
{
    if(gate || !provider)return EINVAL;
    IOService *child=new IOService;
    if(!child)return ENOMEM;
    if(!child->init()){child->release();return ENOMEM;}
    if(!child->attach(provider)){child->release();return ENXIO;}
    int error=attach(child,b,unit);
    if(error)child->detach(provider);
    else ownedChild=true; /* bridge retained child; finish detaches and releases */
    child->release();
    return error;
}

/* Called only with the bridge gate held. Input header points exactly14 bytes
 * into owned headroom; never dereference an unbounded pkt_hdr pointer. TX is
 * the complete raw Ethernet mbuf supplied by ndrv_output/ifnet_output_raw.
 * No DLIL call or ifnet flag promotion occurs while checking this envelope. */
int A04BR3BSDNetwork::preauth(const a04_network_state *state,mbuf_t packet,bool transmit)
{
    if(!a04_network_control_associated(state))return ENETDOWN;
    if(!backend.native_get)return EOPNOTSUPP;
    if(!(mbuf_flags(packet)&MBUF_PKTHDR) || mbuf_nextpkt(packet))return EINVAL;
    size_t n=mbuf_pkthdr_len(packet);
    if(n<(transmit?18u:4u) || n>(transmit?A04_NET_CONTROL_MAX_FRAME:1500u))return EINVAL;
    uint8_t frame[A04_NET_CONTROL_MAX_FRAME];unsigned bytes=(unsigned)n;
    int error;
    if(transmit){
        error=mbuf_copydata(packet,0,n,frame);
    }else{
        const void *header=mbuf_pkthdr_header(packet);
        uintptr_t data=(uintptr_t)mbuf_data(packet),h=(uintptr_t)header;
        if(!header || mbuf_leadingspace(packet)<14 || h>UINTPTR_MAX-14 || h+14!=data)return EINVAL;
        memcpy(frame,header,14);
        error=mbuf_copydata(packet,0,n,frame+14);bytes+=14;
    }
    if(error)return error;
    unsigned type=((unsigned)frame[12]<<8)|frame[13];
    if(type!=0x888e && type!=0x88c7)return ENETDOWN; // ordinary IP/ARP stays closed
    uint8_t peer[6]={};uint32_t peerBytes=sizeof(peer);
    // Never read association request/cache metadata as the actual peer.
    error=backend.native_get(backend.context,9,0,peer,&peerBytes);
    if(error)return error;
    if(peerBytes!=sizeof(peer))return ENETDOWN;
    error=a04_network_control_frame(frame,bytes,mac,peer,transmit?1:0);
    if(error)return error;
    // A backend callback may observe a concurrent disconnect/radio change.
    // Resample state before dispatch; backend TX/RX owner must also validate
    // its real station under its transport lock, including peer on roam.
    a04_network_state after={};error=snapshot(&after);
    return error?error:((a04_network_control_associated(&after) &&
        after.association_generation==state->association_generation)?0:ENETDOWN);
}

int A04BR3BSDNetwork::input(mbuf_t packet)
{
    if(!packet)return EINVAL;
    if(!gate){mbuf_freem_list(packet);return ENXIO;}
    IOLockLock(gate);a04_network_state s={};int error=snapshot(&s);
    if(!error && mbuf_nextpkt(packet))error=EINVAL;
    if(!error && (!interface || !admin))error=ENETDOWN;
    if(!error && !a04_network_data_ready(&s))error=preauth(&s,packet,false);
    ifnet_t ifp=interface;
    if(!error)error=ifnet_reference(ifp);
    IOLockUnlock(gate);
    if(error){mbuf_freem_list(packet);return error;}
    /* Protocol input may reenter output/driver callbacks. Never hold our gate
     * over DLIL; retain ifnet across concurrent asynchronous detach instead. */
    error=ifnet_input(ifp,packet,nullptr); /* consumes mbuf on success and failure */
    ifnet_release(ifp);
    return error;
}

int A04BR3BSDNetwork::scanCompleted()
{
    if(!gate)return ENXIO;
    IOLockLock(gate);a04_network_state state={};int error=snapshot(&state);
    if(!error && (!interface || !a04_network_control_ready(&state)))error=ENETDOWN;
    ifnet_t ifp=interface;
    if(!error)error=ifnet_reference(ifp);
    /* XNU's internal build opts into a zero-length/flexible event_data;
     * external KPI consumers retain event_data[1]. A union supplies aligned
     * storage for the same exact 24+16 wire message in both layouts. */
    union { struct kern_event_msg header; uint8_t bytes[40]; } event={};
    static_assert(offsetof(struct kern_event_msg,event_data)==24,"native event header");
    static_assert(sizeof(event)==40,"native event payload is exact ifname16");
    event.header.total_size=sizeof(event);event.header.vendor_code=1;
    event.header.kev_class=6;event.header.kev_subclass=1;event.header.event_code=10;
    memcpy((uint8_t *)&event+offsetof(struct kern_event_msg,event_data),name,16);
    IOLockUnlock(gate);
    if(error)return error;
    // Like input(), no bridge gate over DLIL callbacks. Independent reference
    // survives asynchronous detach. Exact native filter, not KEV_DL code10.
    error=ifnet_event(ifp,&event.header);ifnet_release(ifp);return error;
}

/* Standard Apple IONetworkInterface link-event payload: family, unit, name16.
 * Explicit offset24 handles both the kernel flexible event_data and KPI [1].
 * No guessed link speed, quality or private IO80211 success is published. */
int A04BR3BSDNetwork::refresh()
{
    if(!gate)return ENXIO;
    IOLockLock(gate);
    // DLIL can synchronously reenter us. The active publisher resamples after
    // its event and reconciles the newest state; never wait under a callback.
    if(linkPublishing){IOLockUnlock(gate);return EBUSY;}
    for(unsigned attempt=0;attempt<8;attempt++){
        if(!interface || stopping){IOLockUnlock(gate);return ENXIO;}
        a04_network_state s={};int error=snapshot(&s);
        bool control=!error && a04_network_control_ready(&s) && backend.native_get;
        bool data=control && a04_network_data_ready(&s) && backend.transmit;
        bool linked=admin && data;
        // A coalesced ready->new ready join still changes the real network.
        if(linked && linkReported && linkGeneration!=s.association_generation)linked=false;
        int flagError=ifnet_set_flags(interface,linked?IFF_RUNNING:0,IFF_RUNNING);
        registry->setProperty("WLANBackendReady",control);
        registry->setProperty("WLANDataPathReady",data);
        if(flagError){
            (void)ifnet_set_flags(interface,0,IFF_RUNNING);
            IOLockUnlock(gate);return flagError;
        }
        if(linked==linkReported){IOLockUnlock(gate);return error;}
        ifnet_t ifp=interface;int retained=ifnet_reference(ifp);
        if(retained){(void)ifnet_set_flags(ifp,0,IFF_RUNNING);IOLockUnlock(gate);return retained;}
        union { struct kern_event_msg header; uint8_t bytes[48]; } event={};
        static_assert(offsetof(struct kern_event_msg,event_data)==24,"link header offset");
        static_assert(sizeof(event)==48,"link event family4 unit4 name16");
        event.header.total_size=sizeof(event);event.header.vendor_code=KEV_VENDOR_APPLE;
        event.header.kev_class=KEV_NETWORK_CLASS;event.header.kev_subclass=KEV_DL_SUBCLASS;
        event.header.event_code=linked?KEV_DL_LINK_ON:KEV_DL_LINK_OFF;
        uint32_t family=ifnet_family(ifp),unit=ifnet_unit(ifp);
        memcpy((uint8_t *)&event+24,&family,4);memcpy((uint8_t *)&event+28,&unit,4);
        memcpy((uint8_t *)&event+32,ifnet_name(ifp),strlen(ifnet_name(ifp)));
        linkPublishing=true;
        IOLockUnlock(gate);
        int posted=ifnet_event(ifp,&event.header); // filters/protocols may reenter
        IOLockLock(gate);
        linkPublishing=false;
        if(!posted){linkReported=linked;linkGeneration=linked?s.association_generation:0;}
        else (void)ifnet_set_flags(ifp,0,IFF_RUNNING); // fail closed; retry next refresh
        ifnet_release(ifp);
        if(posted){IOLockUnlock(gate);return posted;}
        // Resample after unlocked DLIL: a cancel/down during link-on must
        // receive its matching link-off, rather than retaining an old result.
    }
    IOLockUnlock(gate);return EAGAIN;
}

/* Crypto completion is not upper-link completion. A concurrent admin-down,
 * publication failure or replacement association must not satisfy the native
 * authentication interest. Snapshot the bridge's own actual publication as
 * well as the current backend; never promote flags from authorized alone. */
int A04BR3BSDNetwork::publishedLink(uint64_t generation)
{
    if(!generation)return EINVAL;
    if(!gate)return ENXIO;
    IOLockLock(gate);
    int error=0;
    if(!interface || stopping || !admin || linkPublishing || !linkReported ||
        linkGeneration!=generation ||
        (ifnet_flags(interface)&(IFF_UP|IFF_RUNNING))!=(IFF_UP|IFF_RUNNING))error=ENETDOWN;
    if(!error){
        a04_network_state state={};error=snapshot(&state);
        if(!error && (state.association_generation!=generation ||
            !a04_network_data_ready(&state) || !backend.native_get || !backend.transmit))error=ENETDOWN;
    }
    IOLockUnlock(gate);return error;
}

errno_t A04BR3BSDNetwork::output(ifnet_t ifp,mbuf_t packet)
{
    auto *s=(A04BR3BSDNetwork *)ifnet_softc(ifp);
    if(!packet)return EINVAL;
    if(!s || !s->gate){mbuf_freem_list(packet);return ENXIO;}
    IOLockLock(s->gate);a04_network_state state={};int error=s->snapshot(&state);
    if(!error && mbuf_nextpkt(packet))error=EINVAL;
    if(!error && !s->admin)error=ENETDOWN;
    uint8_t header[14]={};
    bool control=!error && (mbuf_flags(packet)&MBUF_PKTHDR) && mbuf_pkthdr_len(packet)>=14 &&
        !mbuf_copydata(packet,0,sizeof(header),header) && header[12]==0x88 &&
        (header[13]==0x8e || header[13]==0xc7);
    if(!error && (control || !a04_network_data_ready(&state)))error=s->preauth(&state,packet,true);
    if(!error){
        auto send=control?s->backend.transmit_eapol:s->backend.transmit;
        error=send?send(s->backend.context,packet):EOPNOTSUPP;
    }
    IOLockUnlock(s->gate);
    if(error)mbuf_freem_list(packet); /* only successful backend TX owns the packet */
    return error;
}

errno_t A04BR3BSDNetwork::ioctl(ifnet_t ifp,unsigned long command,void *data)
{
    auto *s=(A04BR3BSDNetwork *)ifnet_softc(ifp);if(!s || !s->gate)return ENXIO;
    const bool native=command==A04_APPLE80211_GET64 || command==A04_APPLE80211_SET64;
    const bool media=command==SIOCGIFMEDIA32 || command==SIOCGIFMEDIA64 || command==SIOCSIFMEDIA;
    if(!native && !media && command!=SIOCSIFFLAGS)return EOPNOTSUPP;
    const auto *request=native?(const a04_apple80211_req64 *)data:nullptr;
    const uint32_t traceType=request?request->type:0,traceValue=request?request->value:0;
    const uint32_t traceLength=request?request->length:0;
    /* Default BSD ioctl fallback does not supply a write authorization check
     * for this native command. Check the active calling thread, not cached
     * provider credentials or an untrusted field in the native request. */
    if(command==A04_APPLE80211_SET64 && !kauth_cred_issuser(kauth_cred_get())){
        a04_native_trace(command,traceType,traceValue,traceLength,EPERM);return EPERM;
    }
    IOLockLock(s->gate);a04_network_state state={};int error=s->snapshot(&state);
    if(media){
        if(!error){
            const a04_network_copy copy={nullptr,a04_network_copyin,a04_network_copyout};
            if(command==SIOCSIFMEDIA)
                error=a04_wifi_media_set(&state,(const struct ifreq *)data,s->name);
            else if(command==SIOCGIFMEDIA64)
                error=a04_wifi_media_get(&state,(struct ifmediareq64 *)data,s->name,&copy);
            else error=a04_wifi_media_get(&state,(struct ifmediareq32 *)data,s->name,&copy);
        }
    }else if(command==SIOCSIFFLAGS){
        bool enabled=(ifnet_flags(ifp)&IFF_UP)!=0;
        /* UP enables radio/control before scan/association. It must not depend
         * on association or a usable packet datapath (that would deadlock setup).
         * RUNNING remains derived from observed association + datapath only. */
        if(!error && enabled && !a04_network_control_ready(&state))error=ENETDOWN;
        if(!error)error=s->backend.set_admin?s->backend.set_admin(s->backend.context,enabled):EOPNOTSUPP;
        if(!error){
            s->admin=enabled;
            /* Radio control can change backend state synchronously. Refresh
             * the snapshot rather than promoting pre-command state to link. */
            a04_network_state after={};int status=s->snapshot(&after);
            bool control=!status && a04_network_control_ready(&after) && s->backend.native_get;
            bool linked=control && a04_network_data_ready(&after) && s->backend.transmit;
            ifnet_set_flags(ifp,(enabled && linked)?IFF_RUNNING:0,IFF_RUNNING);
            s->registry->setProperty("WLANBackendReady",control);
            s->registry->setProperty("WLANDataPathReady",linked);
        }
        else{s->admin=false;ifnet_set_flags(ifp,0,IFF_UP|IFF_RUNNING);}
    }else if(!error){
        if(!data)error=EINVAL;
        else{
            auto *req=(a04_apple80211_req64 *)data;
            const bool scalar=command==A04_APPLE80211_GET64 && a04_native_scalar_type(req->type) && !req->length;
            if((command==A04_APPLE80211_GET64 && !req->length && !scalar) || req->length>A04_APPLE80211_MAX_BYTES)error=EINVAL;
            else{
                unsigned bytes=scalar?4:(req->length?req->length:1);void *scratch=IOMallocZero(bytes);
                if(!scratch)error=ENOMEM;
                else{
                    const a04_network_copy copy={nullptr,a04_network_copyin,a04_network_copyout};
                    if(command==A04_APPLE80211_SET64)
                        error=a04_network_set(&s->backend,&state,req,s->name,scratch,bytes,&copy,1);
                    else error=a04_network_get(&s->backend,&state,req,s->name,scratch,bytes,&copy);
                    IOFree(scratch,bytes);
                }
            }
        }
    }
    IOLockUnlock(s->gate);
    if(command==SIOCSIFFLAGS){int publication=s->refresh();if(!error)error=publication;}
    if(native)a04_native_trace(command,traceType,traceValue,traceLength,error);
    return error;
}

int A04BR3BSDNetwork::dispatch(unsigned long command,void *data)
{
    if(!gate || !interface)return ENXIO;
    return ioctl(interface,command,data);
}

int A04BR3BSDNetwork::detachAndWait()
{
    int error=detach();if(error || !gate)return error;
    IOLockLock(gate);
    while(interface)IOLockSleep(gate,this,THREAD_UNINT);
    IOLockUnlock(gate);
    return finish();
}

int A04BR3BSDNetwork::detach()
{
    if(!gate)return 0;
    IOLockLock(gate);if(!interface || stopping){IOLockUnlock(gate);return 0;}
    if(linkPublishing){IOLockUnlock(gate);return EBUSY;}
    admin=false;IOLockUnlock(gate);
    int publication=refresh(); // standard LINK_OFF precedes actual detach
    IOLockLock(gate);
    if(linkPublishing || linkReported){IOLockUnlock(gate);return publication?publication:EBUSY;}
    if(!interface || stopping){IOLockUnlock(gate);return 0;}
    stopping=true;auto ifp=interface;
    ifnet_set_flags(ifp,0,IFF_UP|IFF_RUNNING);
    registry->removeProperty("IOInterfaceName");registry->removeProperty("IOInterfaceNamePrefix");
    registry->removeProperty("IOInterfaceUnit");registry->removeProperty("IO80211InterfaceRole");
    registry->removeProperty("IOBSDName");registry->removeProperty("IOInterfaceType");
    registry->removeProperty("IOMACAddress");
    registry->setProperty("WLANBackendReady",false);registry->setProperty("WLANDataPathReady",false);
    IOLockUnlock(gate);
    int error=ifnet_detach(ifp); /* may invoke detached() inline */
    if(error){
        /* DLIL still owns a live interface. Restore discovery identity, but
         * retain admin-down state; a new explicit UP is required to enable it. */
        IOLockLock(gate);stopping=false;
        publishIdentity();
        IOLockUnlock(gate);
        (void)refresh();
    }
    return error;
}

void A04BR3BSDNetwork::detached(ifnet_t ifp)
{
    auto *s=(A04BR3BSDNetwork *)ifnet_softc(ifp);
    ifnet_release(ifp); /* release the original allocation reference, once */
    IOLockLock(s->gate);s->interface=nullptr;IOLockWakeup(s->gate,s,false);IOLockUnlock(s->gate);
}

bool A04BR3BSDNetwork::isDetached()
{
    if(!gate)return true;
    IOLockLock(gate);bool done=interface==nullptr;IOLockUnlock(gate);return done;
}

int A04BR3BSDNetwork::finish()
{
    if(!isDetached())return EBUSY;
    if(registry){
        if(ownedChild)registry->detach(registry->getProvider());
        registry->release();registry=nullptr;
    }
    ownedChild=false;
    if(gate){IOLockFree(gate);gate=nullptr;}
    backend={};stopping=admin=linkReported=linkPublishing=false;linkGeneration=0;return 0;
}
