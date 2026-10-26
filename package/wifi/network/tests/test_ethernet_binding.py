# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Execute real adapter/bridge code with typed Apple/DLIL lifecycle mocks.
Full pinned family SDK compilation is a separate integration gate.
"""
from pathlib import Path
import hashlib,json,subprocess,tempfile,unittest
from test_bsd_network import STUB,CASE,SOURCE
APPLE=r'''
#pragma once
using UInt16=uint16_t;using UInt32=uint32_t;using SInt32=int32_t;using IOOptionBits=unsigned;using IOReturn=int;
#define TRUE true
#define APPLE_KEXT_OVERRIDE override
#define OSDeclareDefaultStructors(x)
#define OSDefineMetaClassAndStructors(x,y)
#define OSDynamicCast(t,p) dynamic_cast<t *>(p)
static const int kIOReturnSuccess=0,kIOReturnError=1,kIOReturnBadArgument=2,kIOReturnNotReady=3,kIOReturnUnsupported=4,kIOReturnBusy=5,kIOReturnNoSpace=6;
static const unsigned kConfigFrozen=1,kConfigTxPull=2,kConfigRxPoll=4,kConfigDataRates=8;
struct IOEthernetAddress{uint8_t bytes[6];};
struct OSSymbol {};
static OSSymbol mockNetworkGroup,mockOtherGroup;
static const OSSymbol *gIONetworkFilterGroup=&mockNetworkGroup;
static const UInt32 kIOPacketFilterUnicast=1,kIOPacketFilterBroadcast=2,kIOPacketFilterMulticast=16,kIOPacketFilterPromiscuous=256;
class IONetworkController:public IOService {};
struct ifnet_stat_increment_param {unsigned packets_in,bytes_in,errors_in,collisions,packets_out,bytes_out,errors_out;};
struct if_bandwidths_t {uint64_t max_bw,eff_bw;};
struct IONetworkLinkEventData {uint64_t linkSpeed;};
static const unsigned kIONetworkEventTypeLinkUp=1,kIONetworkEventTypeLinkDown=2,kIONetworkEventTypeLinkSpeedChange=3,kIONetworkEventTypeDLIL=4,kIONetworkEventWakeOnLANSupportChanged=5;

using thread_call_param_t=void *;
static int baseInputs;
static int ifnet_input_extended(ifnet_t p,mbuf_t a,mbuf_t b,const ifnet_stat_increment_param *stats){assert(p && p==current && a==b && stats->packets_in==1);lastBaseIfnet=p;baseInputs++;mbuf_freem(a);return 0;}

static int ifnet_set_bandwidths(ifnet_t p,if_bandwidths_t *,if_bandwidths_t *){assert(p);return 0;}
static bool thread_call_enter1(void *,thread_call_param_t){return true;}
class IONetworkInterface:public IOService {
protected:
 ifnet_t _backingIfnet=nullptr;IOLock *_privateLock=nullptr;unsigned _configFlags=0;UInt16 _flags=0;
 struct Stats {unsigned inputErrors=0,collisions=0;} _driverStats,_lastDriverStats;
 ifnet_stat_increment_param _inputDeltas={};void *_inputEventThreadCall=nullptr;
 IOReturn adoptExistingIfnet(ifnet_t);
 IOReturn clearExistingIfnet(ifnet_t);
public:
 virtual ~IONetworkInterface(){FREE_BACKING_FRAGMENT if(_privateLock)IOLockFree(_privateLock);}
 void pushInputPacket(mbuf_t,uint32_t);
 bool inputEvent(UInt32,void *);
 virtual bool init(IONetworkController *){_privateLock=IOLockAlloc();return IOService::init();}
 virtual IOReturn attachToDataLinkLayer(IOOptionBits,void *){assert(false);return 1;}
 virtual void detachFromDataLinkLayer(IOOptionBits,void *){assert(false);}
 virtual ifnet_t getIfnet() const;
 virtual UInt16 getFlags() const;
 virtual bool setFlags(UInt16,UInt16=0);
 virtual SInt32 performCommand(IONetworkController *,unsigned long,void *,void *){return EOPNOTSUPP;}
 virtual bool setInterfaceSubType(unsigned v){assert(v==IFNET_SUBFAMILY_WIFI);return true;}
 bool assigned=false;
 virtual bool setUnitNumber(uint32_t unit){assert(unit==7 && !assigned);assigned=true;return true;}
 virtual UInt16 getUnitNumber() const{assert(assigned);return 7;}
};
class IOEthernetInterface:public IONetworkInterface {};
static IONetworkInterface *mockAttachedInterface;
class IOEthernetController:public IONetworkController {
public:
 virtual bool start(IOService *){return true;}
 virtual void stop(IOService *){}
 virtual IONetworkInterface *createInterface()=0;
 virtual IOReturn getHardwareAddress(IOEthernetAddress *)=0;
 virtual IOReturn getPacketFilters(UInt32 *) const{return 1;}
 virtual IOReturn enablePacketFilter(const OSSymbol *,UInt32,UInt32,IOOptionBits){return kIOReturnUnsupported;}
 virtual IOReturn enable(IONetworkInterface *){return 1;}
 virtual IOReturn disable(IONetworkInterface *){return 1;}
 virtual bool attachInterface(IONetworkInterface **out,bool doRegister){
  auto *p=createInterface();if(!p)return false;
  if(!p->attach(this)){p->release();return false;}
  // Genuine Apple attachInterface only creates/attaches and optionally
  // publishes; it NEVER attaches to DLIL. Regression must not hide that gap.
  assert(!doRegister && !p->getIfnet());
  mockAttachedInterface=p;*out=p;return true;
 }
 virtual void detachInterface(IONetworkInterface *p,bool){p->detachFromDataLinkLayer(0,nullptr);p->detach(this);p->release();}
};
static unsigned stackPublishes,stackReserves,stackAttaches;
class IONetworkStack:public IOService {
 bool published=false;
 bool interfacePublished(void *,IOService *service,void *){
  auto *netif=dynamic_cast<IONetworkInterface *>(service);assert(netif && !netif->getIfnet());
  assert(!published);published=true;stackPublishes++;return true;
 }
 bool reserveInterfaceUnitNumber(IONetworkInterface *netif,uint32_t unit,bool fixed,bool *attach){
  assert(published && unit==0 && !fixed && !netif->getIfnet());
  published=false;stackReserves++;*attach=true;return netif->setUnitNumber(7); // mock occupied units0..6
 }
 IOReturn attachNetworkInterfaceToBSD(IONetworkInterface *netif){
  assert(netif->assigned && !netif->getIfnet());stackAttaches++;
  return netif->attachToDataLinkLayer(0,nullptr);
 }
 IOReturn registerNetworkInterface(IONetworkInterface *,uint32_t,bool);
public:
 bool start(IOService *){return true;}
 IOReturn registerKernelInterface(IONetworkInterface *,uint32_t);
};

'''
EXTRA=r'''
class BackingProbe:public IOEthernetInterface {
public:
 IOReturn take(ifnet_t p){return adoptExistingIfnet(p);}
 void mode(unsigned v){_configFlags=v;}
};
static void seamFallbackCase(const a04_network_backend &b){
 reset();A04BR3BSDNetwork bridge;IOService service;
 assert(!bridge.attach(&service,&b,7));auto *probe=new BackingProbe;
 assert(probe->init(nullptr));assert(probe->take(nullptr)==kIOReturnBadArgument);
 probe->mode(kConfigRxPoll);assert(probe->take(current)==kIOReturnUnsupported && current->refs==1);
 probe->mode(0);assert(!probe->take(current) && current->refs==2);
 assert(probe->take(current)==kIOReturnBusy && current->refs==2);
 assert(!bridge.detachAndWait() && current && current->refs==1 && probe->getIfnet()==current);
 probe->release(); // execute genuine base free() backing-reference fragment
 assert(!current && releaseCount==1 && !lockCount && !childCount);
}
static void bindingCases(){
 reset();a04_network_backend b={nullptr,state,get,admin,tx,set};IOService provider;
 realState.firmware_running=realState.control_ready=1;
 int error=99;auto *controller=a04br3_network_start(&provider,&b,&error);
 assert(controller && !error && current && childCount==3);
 assert(stackPublishes && stackPublishes==stackReserves && stackReserves==stackAttaches);
 assert(dynamic_cast<IO80211Controller *>(controller));
 assert(dynamic_cast<IO80211Interface *>(mockAttachedInterface));
 assert(current->refs==2); // one allocated ifnet: bridge ref + genuine base ref
 auto *actual=OSDynamicCast(A04BR3EthernetInterface,mockAttachedInterface);assert(actual);
 assert(actual->getIfnet()==current); // actual Apple getter sees private field
 ifmediareq64 media={};strcpy(media.ifm_name,"en7");
 assert(!actual->performCommand(controller,SIOCGIFMEDIA64,nullptr,&media));
 assert((media.ifm_current&IFM_NMASK)==IFM_IEEE80211 && !(media.ifm_status&IFM_ACTIVE));
 ifreq selection={};strcpy(selection.ifr_name,"en7");selection.ifr_media=IFM_IEEE80211|IFM_AUTO;
 assert(!actual->performCommand(controller,SIOCSIFMEDIA,nullptr,&selection));
 selection.ifr_media|=0x100;assert(actual->performCommand(controller,SIOCSIFMEDIA,nullptr,&selection)==EOPNOTSUPP);

 actual->retain();
 Packet basePacket={false};actual->pushInputPacket(&basePacket,64);
 assert(basePacket.freed && baseInputs==1 && lastBaseIfnet==current);
 kern_event_msg event={};assert(actual->inputEvent(kIONetworkEventTypeDLIL,&event) && baseEvents==1 && lastBaseIfnet==current);
 actual->setFlags(IFF_UP|IFF_RUNNING);assert(!(current->flags&(IFF_UP|IFF_RUNNING)));
 IOEthernetAddress mac;assert(controller->getHardwareAddress(&mac)==0 && !memcmp(mac.bytes,realState.mac,6));
 assert(controller->enable(nullptr)==0 && lastAdmin==1 && !realState.associated);
 UInt32 filters=999;assert(!controller->getPacketFilters(&filters) && filters==(kIOPacketFilterUnicast|kIOPacketFilterBroadcast));
 assert(controller->enablePacketFilter(gIONetworkFilterGroup,kIOPacketFilterUnicast,0,0)==0);
 assert(controller->enablePacketFilter(gIONetworkFilterGroup,kIOPacketFilterBroadcast,0,0)==0);
 assert(controller->enablePacketFilter(gIONetworkFilterGroup,kIOPacketFilterMulticast,0,0)==kIOReturnUnsupported);
 assert(controller->enablePacketFilter(gIONetworkFilterGroup,kIOPacketFilterPromiscuous,0,0)==kIOReturnUnsupported);
 assert(controller->enablePacketFilter(&mockOtherGroup,kIOPacketFilterUnicast,0,0)==kIOReturnUnsupported);
 realState.control_ready=0;assert(controller->enablePacketFilter(gIONetworkFilterGroup,kIOPacketFilterUnicast,0,0)==kIOReturnNotReady);realState.control_ready=1;
 assert(controller->refreshBackend()==0 && !(current->flags&IFF_RUNNING));
 assert(!controller->scanCompleted() && scanEvents==1 && current->refs==2 && !realState.associated);
 realState.control_ready=0;assert(controller->scanCompleted()==ENETDOWN && scanEvents==1);realState.control_ready=1;
 eventError=EIO;assert(controller->scanCompleted()==EIO && current->refs==2);eventError=0;
 auto *child=OSDynamicCast(A04BR3EthernetInterface,controller->createInterface());assert(child);child->release();
 auto r=req();assert(!current->init.ioctl(current,A04_APPLE80211_SET64,&r));
 assert(!realState.associated && !(current->flags&IFF_RUNNING));
 failDetach=EIO;assert(controller->shutdownBackend()==EIO && current->refs==2 && actual->getIfnet()==current);failDetach=0;
 controller->stop(&provider);assert(!actual->getIfnet() && !current && releaseCount==1);
 actual->release(); // genuine base fallback cannot release an already-cleared ref
 controller->detach(&provider);controller->release();
 // Apple stack is boot-lifetime in production; test explicitly ends the boot.
 a04Stack->detach(&provider);a04Stack->release();a04Stack=nullptr;
 assert(!current && !childCount && !lockCount);
 for(unsigned fault=0;fault<2;fault++){
  reset();realState.firmware_running=realState.control_ready=1;
  A04BR3EthernetController failed;
  assert(failed.configureBackend(&b));if(fault==0)failReference=EIO;else failAttach=EIO;
  assert(failed.start(&provider) && !current); // nub exists, no premature DLIL
  IONetworkStack stack;assert(stack.registerKernelInterface(failed.networkInterface(),0)!=kIOReturnSuccess);
  failed.stop(&provider);assert(!current && !childCount && !lockCount);failReference=0;
 }
 reset();realState.mac_valid=0;assert(!a04br3_network_start(&provider,&b,&error) && error==ENXIO && !childCount && !current);
 seamFallbackCase(b);
}
'''
NATIVE_API=r'''
#pragma once
#include "A04BR3WLANBackendAPI.h"
#include "A04BR3WLANPSK4Way.h"
static a04_wlan_native_client nativeClient={};
static unsigned observedCount,observedDone,observedGeneration=1,acquires,releases,beginCalls,beginActive;
static int runtimeError,beginError,copyError,currentRecordError,disassociateCalls,disassociateError;
static a04_wlan_scan_record scanRecords[2];
static void recordInit(unsigned i){
 auto &r=scanRecords[i];r={};auto &a=r.ap;auto &x=r.observation;
 a.mac[0]=2;a.mac[5]=(uint8_t)(i+1);memcpy(a.ssid,"abc",3);a.ssid_length=3;
 a.rate_count=2;a.rates[0]=0x82;a.rates[1]=0x84;a.channel=6;a.band=1;a.capability=0x431;a.beacon=100;a.rcpi_valid=1;a.rcpi=100;
 x.generation=observedGeneration;x.observed_us=1000000;x.from_probe=i;
 const uint8_t ies[]={0,3,'a','b','c',1,2,0x82,0x84};x.ie_bytes=sizeof(ies);memcpy(x.ies,ies,sizeof(ies));
}
// Match SDIO nativeDispatch: backend points to the retained client callbacks,
// rather than testing a mock response that bypasses production Rendezvous.
static int providerGet(void *,uint32_t t,uint32_t v,void *p,uint32_t *n){
 return nativeClient.get?nativeClient.get(nativeClient.context,t,v,p,n):EOPNOTSUPP;
}
static int providerSet(void *,uint32_t t,uint32_t v,const void *p,uint32_t n){
 return nativeClient.set?nativeClient.set(nativeClient.context,t,v,p,n):EOPNOTSUPP;
}
extern "C" int a04br3_wlan_acquire_backend(IOService *p,a04_network_backend *b){
 if(!realState.control_ready)return ENXIO;
 p->retain();acquires++;*b={nullptr,state,providerGet,admin,tx,providerSet};return 0;
}
extern "C" void a04br3_wlan_release_backend(IOService *p){releases++;p->release();}
extern "C" int a04br3_wlan_scan_snapshot(IOService *,a04_wlan_scan_info *out){
 *out={};out->count=observedCount;out->complete=observedDone;out->generation=observedGeneration;out->error=runtimeError;out->mode=beginActive;return 0;
}
extern "C" int a04br3_wlan_scan_copy_record(IOService *,unsigned generation,unsigned i,a04_wlan_scan_record *out){
 if(copyError)return copyError;
 if(generation!=observedGeneration || !observedDone)return EAGAIN;
 if(i>=observedCount)return ENOENT;*out=scanRecords[i];return 0;
}
extern "C" int a04br3_wlan_scan_mode(IOService *,unsigned active,unsigned *accepted){
 beginCalls++;beginActive=active;if(beginError)return beginError;
 *accepted=++observedGeneration;observedDone=observedCount=0;return 0;
}
static unsigned joinCalls;static int joinError;static a04_wlan_join_target lastJoin={};
extern "C" int a04br3_wlan_disassociate(IOService *){disassociateCalls++;return disassociateError;}
extern "C" int a04br3_wlan_current_record(IOService *,a04_wlan_scan_record *out){if(currentRecordError)return currentRecordError;if(!realState.associated)return ENOTCONN;*out=scanRecords[0];return 0;}
extern "C" int a04br3_wifi_random(uint8_t[32]){assert(false);return EIO; /* not used by OPEN host fixture */}
extern "C" int a04br3_wlan_join_personal(IOService *,const a04_wlan_join_target *,a04_psk_prepared *prepared){a04_psk_prepared_clear(prepared);return EOPNOTSUPP;}
extern "C" int a04br3_wlan_join_target(IOService *,const a04_wlan_join_target *target){joinCalls++;lastJoin=*target;return joinError;}
extern "C" int a04br3_wlan_bind_native(IOService *,const a04_wlan_native_client *c){nativeClient=*c;c->owner->retain();return 0;}
extern "C" int a04br3_wlan_unbind_native(IOService *){nativeClient.owner->release();nativeClient={};return 0;}
'''
NATIVE_CASE=r'''
static void nativeCases(){
 reset();IOService provider;IOService *owner=nullptr;
 realState.firmware_running=realState.control_ready=1;
 assert(!a04br3_network_bind(&provider,&owner) && owner && !current);
 auto client=nativeClient;client.ready(client.context,&provider);
 assert(current && current->refs==2 && provider.refs==2);
 // Real authenticated callback: publication failure must be retriable.
 realState.associated=realState.datapath_ready=realState.authorized=realState.auth_profile_valid=1;
 realState.auth_upper=8;realState.association_generation=41;
 current->flags|=IFF_UP;assert(!current->init.ioctl(current,SIOCSIFFLAGS,nullptr));
 realState.associated=realState.datapath_ready=0;client.changed(client.context);
 realState.associated=realState.datapath_ready=1;eventError=EIO;
 assert(client.authenticated(client.context,41)==EIO&&!authMessages&&!(current->flags&IFF_RUNNING));
 eventError=0;authMessageError=EIO;
 assert(client.authenticated(client.context,41)==EIO&&authMessages==1);
 authMessageError=0;assert(!client.authenticated(client.context,41)&&authMessages==2);
 assert(!client.authenticated(client.context,41)&&authMessages==2); // duplicate suppressed
 realState.association_generation=42;assert(client.authenticated(client.context,41)==ENETDOWN);
 realState.authorized=realState.auth_profile_valid=realState.auth_upper=0;
 realState.associated=realState.datapath_ready=0;client.changed(client.context);
 current->flags&=~IFF_UP;assert(!current->init.ioctl(current,SIOCSIFFLAGS,nullptr));
 // Exact first binder request: GET12, value0, thirteen bytes, no prefix.
 a04_apple80211_req64 bindReq={};strcpy(bindReq.name,"en7");bindReq.type=12;
 bindReq.length=13;bindReq.user_data=(uintptr_t)userBuffer;memset(userBuffer,0xa5,sizeof(userBuffer));
 assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&bindReq) && bindReq.length==13);
 for(unsigned i=0;i<13;i++)assert(!userBuffer[i]);assert(userBuffer[13]==0xa5);
 // Real Rendezvous scalar source state; no optional capability bits needed.
 a04_apple80211_req64 scalar={};strcpy(scalar.name,"en7");scalar.type=15;
 assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&scalar) && scalar.value==1 && !scalar.length);
 scalar.type=18;scalar.value=0;assert(current->init.ioctl(current,A04_APPLE80211_GET64,&scalar)==EOPNOTSUPP && scalar.value==0);
 scalar.type=13;scalar.value=0;
 assert(current->init.ioctl(current,A04_APPLE80211_GET64,&scalar)==EOPNOTSUPP);
 for(unsigned phase=1;phase<=5;phase++){
  realState.station_phase=phase;realState.associated=phase==5;scalar.value=0;
  assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&scalar) && scalar.value==phase-1 && !scalar.length);
 }
 realState.station_phase=A04_NET_PHASE_UNKNOWN;realState.associated=0;
 // Exact native hardware/permitted channel queries use actual snapshot mask.
 realState.permitted_channels=(1u<<1)|(1u<<6);auto channels=bindReq;
 channels.type=254;channels.length=4824;memset(userBuffer,0xa5,4825);
 assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&channels) && userBuffer[4]==2 && userBuffer[12]==1 && userBuffer[24]==6 && userBuffer[4824]==0xa5);
 realState.permitted_channels=0;

 auto absent=bindReq;absent.type=103;absent.length=1164;memset(userBuffer,0xa5,1164);
 assert(current->init.ioctl(current,A04_APPLE80211_GET64,&absent)==ENOTCONN && userBuffer[0]==0xa5 && absent.length==1164);
 // GET19 is firmware control/radio state even before any association.
 bindReq.type=19;bindReq.length=24;memset(userBuffer,0,24);userBuffer[0]=1;
 realState.radio_enabled=1;
 assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&bindReq));
 assert(userBuffer[0]==1 && userBuffer[4]==1 && userBuffer[8]==1 && !(current->flags&IFF_RUNNING));
 int priorAdmin=adminCalls;credentialRoot=0;
 assert(current->init.ioctl(current,A04_APPLE80211_SET64,&bindReq)==EPERM && adminCalls==priorAdmin);
 credentialRoot=1;assert(!current->init.ioctl(current,A04_APPLE80211_SET64,&bindReq) && adminCalls==priorAdmin+1 && lastAdmin==1);
 adminError=EOPNOTSUPP;userBuffer[8]=0;
 assert(current->init.ioctl(current,A04_APPLE80211_SET64,&bindReq)==EOPNOTSUPP && realState.radio_enabled==1);
 adminError=EIO;assert(current->init.ioctl(current,A04_APPLE80211_SET64,&bindReq)==EIO && realState.radio_enabled==1);
 adminError=0;assert(!realState.associated && !(current->flags&IFF_RUNNING));

 bindReq.type=0xffffffffu;bindReq.length=13;memset(userBuffer,0xa5,13);
 assert(current->init.ioctl(current,A04_APPLE80211_GET64,&bindReq)==EOPNOTSUPP && userBuffer[0]==0xa5);

 uint8_t policy[5456]={},out[1164]={};uint32_t n=sizeof(out);
 policy[0]=1;policy[4]=3;policy[0x34]=3;policy[0x38]=1;out[0]=1;out[0x8b]=4;
 observedDone=observedCount=0;client.changed(client.context);assert(!scanEvents);
 assert(client.set(client.context,10,0,policy,sizeof(policy))==EAGAIN && !scanEvents);
 observedCount=2;client.changed(client.context);assert(!scanEvents); // AP observation is not completion
 observedDone=1;client.changed(client.context);assert(scanEvents==1);
 recordInit(0);recordInit(1);
 assert(!client.set(client.context,10,0,policy,sizeof(policy)));
 for(unsigned i=0;i<2;i++){
  memset(out,0,sizeof(out));out[0]=1;out[0x8b]=4;n=sizeof(out);
  assert(!client.get(client.context,11,0,out,&n) && out[0x21]==i+1 && out[0x22]==0 && a04_scan_result_u32(out+0x84)==1234);
  assert(out[0x8a]==9 && !memcmp(out+0x8c,scanRecords[i].observation.ies,9));
 }
 memset(out,0,sizeof(out));out[0]=1;out[0x8b]=4;n=sizeof(out);
 assert(client.get(client.context,11,0,out,&n)==EIO);
 // Actual provider adapter rejects oversized full IEs off-stack, no truncation.
 assert(!client.set(client.context,10,0,policy,sizeof(policy)));
 scanRecords[0].observation.ie_bytes=1025;int oldAllocations=allocations;
 uint8_t saved[1164];memcpy(saved,out,sizeof(out));
 assert(client.get(client.context,11,0,out,&n)==EMSGSIZE && allocations==oldAllocations && !memcmp(saved,out,sizeof(out)));
 scanRecords[0].observation.ie_bytes=9;copyError=EAGAIN;
 assert(client.get(client.context,11,0,out,&n)==EAGAIN && allocations==oldAllocations);copyError=0;
 runtimeError=83;assert(client.get(client.context,11,0,out,&n)==EIO && allocations==oldAllocations);runtimeError=0;
 client.changed(client.context);assert(scanEvents==1);
 observedDone=0;client.changed(client.context);assert(scanEvents==1);
 observedGeneration++;observedCount=0;observedDone=1;client.changed(client.context);assert(scanEvents==2);
 assert(!client.set(client.context,10,0,policy,sizeof(policy)) && scanEvents==2);
 for(unsigned i=0;i<3;i++){assert(client.get(client.context,11,0,out,&n)==EIO && n==sizeof(out) && scanEvents==2);}
 policy[0x34]=1;beginError=EOPNOTSUPP;
 assert(client.set(client.context,10,0,policy,sizeof(policy))==EOPNOTSUPP && scanEvents==2 && beginActive==1);
 beginError=0;assert(!client.set(client.context,10,0,policy,sizeof(policy)) && !observedDone && beginActive==1);
 memset(out,0,sizeof(out));out[0]=1;out[0x8b]=4;n=sizeof(out);
 assert(client.get(client.context,11,0,out,&n)==EAGAIN && scanEvents==2);
 policy[0x34]=2;assert(!client.set(client.context,10,0,policy,sizeof(policy)) && beginActive==0 && scanEvents==2);
 assert(client.set(client.context,99,0,nullptr,0)==EOPNOTSUPP);
 observedDone=0;client.changed(client.context);observedDone=1;realState.control_ready=0;
 client.changed(client.context);assert(scanEvents==2); // poisoned/offline backend must not emit completion
 realState.control_ready=1;client.changed(client.context);assert(scanEvents==3);
 // Actual BSD credential/copyin -> production Rendezvous -> typed join API.
 observedCount=observedDone=1;recordInit(0);scanRecords[0].ap.capability&=~16u;
 memset(userBuffer,0,592);userBuffer[0]=1;userBuffer[4]=2;userBuffer[6]=1;
 userBuffer[12]=3;memcpy(userBuffer+16,"abc",3);
 auto association=bindReq;association.type=20;association.value=0;association.length=592;
 association.user_data=(uintptr_t)userBuffer;
 unsigned beforeJoin=joinCalls;int oldCopies=copyIns;
 credentialRoot=0;assert(current->init.ioctl(current,A04_APPLE80211_SET64,&association)==EPERM && joinCalls==beforeJoin && copyIns==oldCopies);
 credentialRoot=1;assert(!current->init.ioctl(current,A04_APPLE80211_SET64,&association));
 assert(joinCalls==beforeJoin+1 && copyIns==oldCopies+1 && lastJoin.generation==observedGeneration && lastJoin.index==0 && lastJoin.ssid_length==3);
 assert(!realState.associated && !(current->flags&IFF_RUNNING));
 joinError=ESTALE;assert(current->init.ioctl(current,A04_APPLE80211_SET64,&association)==ESTALE);joinError=0;
 beforeJoin=joinCalls;userBuffer[56]=1;
 assert(current->init.ioctl(current,A04_APPLE80211_SET64,&association)==EOPNOTSUPP && joinCalls==beforeJoin);userBuffer[56]=0;
 scanRecords[0].ap.capability|=16;
 assert(current->init.ioctl(current,A04_APPLE80211_SET64,&association)==EOPNOTSUPP && joinCalls==beforeJoin);
 disassociateError=EBUSY;
 assert(client.set(client.context,22,0,nullptr,0)==EBUSY && disassociateCalls==1 && joinCalls==beforeJoin);
 assert(client.set(client.context,22,1,nullptr,0)==EOPNOTSUPP && disassociateCalls==1);
 assert(client.set(client.context,22,0,(void *)1,8)==EOPNOTSUPP && disassociateCalls==1);
 disassociateError=0;assert(!client.set(client.context,22,0,nullptr,0) && disassociateCalls==2);
 auto cancel=association;cancel.type=22;cancel.length=0;cancel.user_data=0;
 oldCopies=copyIns;credentialRoot=0;
 assert(current->init.ioctl(current,A04_APPLE80211_SET64,&cancel)==EPERM && disassociateCalls==2 && copyIns==oldCopies);
 credentialRoot=1;disassociateError=EIO;
 assert(current->init.ioctl(current,A04_APPLE80211_SET64,&cancel)==EIO && disassociateCalls==3 && copyIns==oldCopies);
 disassociateError=0;assert(!current->init.ioctl(current,A04_APPLE80211_SET64,&cancel) && disassociateCalls==4 && copyIns==oldCopies);
 // Current103 must come from a real associated backend snapshot, with the
 // exact zero-version native query and observed IEs/RSSI, never scan-cache
 // substitution while unassociated. Source currentRecord is separately run.
 scanRecords[0].ap.capability&=~16u;realState.associated=1;
 // Original native GET1 has capacity32 and returns actual SSID length; GET9 is raw6. Full real BSD -> Rendezvous
 // -> current-record producer path; no scalar/native snapshot substitution.
 for(unsigned type:{1u,9u}){
  auto peerReq=association;peerReq.type=type;peerReq.length=type==1?32:6;
  memset(userBuffer,0xa5,65);unsigned refs=provider.refs,a=acquires,r=releases;
  assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&peerReq));
  assert(peerReq.length==(type==1?3u:6u) && userBuffer[peerReq.length]==0xa5);
  if(type==1){assert(!memcmp(userBuffer,"abc",3));for(unsigned i=3;i<32;i++)assert(userBuffer[i]==0xa5);}
  else assert(!memcmp(userBuffer,scanRecords[0].ap.mac,6));
  assert(provider.refs==refs && acquires==a+1 && releases==r+1);
  uint8_t peerBytes[33];
  auto ownership=[&](int expected,bool acquired){
   memset(peerBytes,0xa5,sizeof(peerBytes));uint32_t length=type==1?32:6;
   unsigned acq=acquires,rel=releases,ref=provider.refs;int storage=allocations;
   assert(a04_assoc_provider_peer(&provider,type,0,peerBytes,&length)==expected);
   assert(acquires==acq+(acquired?1u:0u) && releases==rel+(acquired?1u:0u));
   assert(provider.refs==ref && allocations==storage && length==(type==1?(expected?32u:3u):6u));
   if(expected)assert(peerBytes[0]==0xa5);
  };
  ownership(0,true);
  stateError=EIO;ownership(EIO,true);stateError=0;
  failMemory=1;ownership(ENOMEM,true);failMemory=0;
  currentRecordError=EAGAIN;ownership(EAGAIN,true);currentRecordError=0;
  // Association snapshot may become stale before the producer locks again.
  currentRecordError=ENOTCONN;ownership(ENOTCONN,true);currentRecordError=0;
  currentRecordError=ESTALE;ownership(ESTALE,true);currentRecordError=0;
  realState.associated=0;ownership(ENOTCONN,true);realState.associated=1;
  realState.control_ready=0;ownership(ENXIO,false);realState.control_ready=1;
  // Optional RSSI/large IE do not gate valid SSID/BSSID identity.
  unsigned priorRCPI=scanRecords[0].ap.rcpi_valid;scanRecords[0].observation.ie_bytes=1025;scanRecords[0].ap.rcpi_valid=0;
  ownership(0,true);scanRecords[0].observation.ie_bytes=9;scanRecords[0].ap.rcpi_valid=priorRCPI;
  scanRecords[0].ap.capability|=16;ownership(EOPNOTSUPP,true);scanRecords[0].ap.capability&=~16u;
  scanRecords[0].ap.rsn_length=2;ownership(EOPNOTSUPP,true);scanRecords[0].ap.rsn_length=0;
  auto savedPeer=scanRecords[0].ap;memset(scanRecords[0].ap.mac,0,6);
  ownership(ESTALE,true);scanRecords[0].ap=savedPeer;
 }

 auto currentReq=association;currentReq.type=103;currentReq.length=1164;
 memset(userBuffer,0,1164);userBuffer[0x8b]=4;userBuffer[1164]=0xa5;
 assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&currentReq));
 assert(userBuffer[0]==1 && userBuffer[0x60]==3 && !memcmp(userBuffer+0x61,"abc",3) && userBuffer[1164]==0xa5);
 scanRecords[0].observation.ie_bytes=1025;memset(userBuffer,0,1164);userBuffer[0x8b]=4;
 assert(current->init.ioctl(current,A04_APPLE80211_GET64,&currentReq)==EMSGSIZE && !userBuffer[0]);
 scanRecords[0].observation.ie_bytes=9;
 // Execute the actual current adapter's complete temporary ownership paths.
 // Acquiring a backend retains provider once; copied state/record own no refs.
 uint8_t query[1164];
 auto currentOwnership=[&](int expected,bool acquired){
  memset(query,0,sizeof(query));query[0x8b]=4;uint32_t size=sizeof(query);
  unsigned a=acquires,r=releases,refs=provider.refs;int storage=allocations;
  assert(a04_assoc_provider_current(&provider,0,query,&size,a04_scan_provider_now())==expected);
  assert(acquires==a+(acquired?1u:0u) && releases==r+(acquired?1u:0u));
  assert(provider.refs==refs && allocations==storage && size==sizeof(query));
  if(expected)assert(!query[0]);
 };
 currentOwnership(0,true);
 stateError=EIO;currentOwnership(EIO,true);stateError=0;
 failMemory=1;currentOwnership(ENOMEM,true);failMemory=0;
 currentRecordError=EIO;currentOwnership(EIO,true);currentRecordError=0;
 scanRecords[0].observation.ie_bytes=1025;currentOwnership(EMSGSIZE,true);scanRecords[0].observation.ie_bytes=9;
 realState.associated=0;currentOwnership(ENOTCONN,true);realState.associated=1;
 realState.control_ready=0;currentOwnership(ENXIO,false);realState.control_ready=1;
 realState.associated=0;

 assert(current->init.ioctl(current,A04_APPLE80211_GET64,&currentReq)==ENOTCONN && !userBuffer[0]);

 assert(!a04br3_network_unbind(&provider,&owner) && !owner && !current && provider.refs==1);
 assert(acquires==releases);
 a04Stack->detach(&provider);a04Stack->release();a04Stack=nullptr;
 assert(!childCount && !lockCount);
}
'''
class EthernetBinding(unittest.TestCase):
 def test_adapter(self):
  stub=STUB.replace('static int childCount,failChildInit','static int failReference;\nstatic int childCount,failChildInit').replace('assert(p&&p->refs);p->refs++;return 0;','assert(p&&p->refs);if(failReference)return failReference;p->refs++;return 0;').replace('class IOService {','class IOService {\npublic: virtual ~IOService()=default;').replace('public: virtual ~IOService()=default;', 'public: virtual ~IOService()=default; virtual void free(){}').replace('childCount--;delete this;', 'childCount--;free();delete this;').replace('bool init(){heap=true;','bool init(void * =nullptr){heap=true;')
  stub='static unsigned authMessages=0;static int authMessageError=0;static int authMessage(unsigned);\n'+stub
  stub=stub.replace('virtual void free(){}','virtual void free(){} int messageClients(unsigned type){return authMessage(type);}')
  stub+='\nstatic int authMessage(unsigned type){assert(type==0xe0000130 && current && (current->flags&IFF_RUNNING));authMessages++;return authMessageError;}\n'
  case=CASE.replace(' mediaCases();\n','').replace(' traceCases();\n a04_network_backend b=', ' a04_network_backend b=').replace('#include "driver.cpp"','#include "driver.cpp"\n#include "apple/IOEthernetController.h"\n#include "A04BR3Ethernet.cpp"\n#include "A04BR3NetworkRuntime.cpp"').replace('int main(){',EXTRA+'\n#include \"sdio/mock_provider.h\"\n#include \"A04BR3NativeRendezvous.cpp\"\n'+NATIVE_CASE+'\nint main(){mediaCases();traceCases();nativeCases();bindingCases();')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'stub.h').write_text(stub)
   for name in ['IOKit/IOLib.h','IOKit/IOService.h','net/kpi_interface.h','sys/systm.h','sys/sockio.h','sys/kpi_mbuf.h','sys/kauth.h','sys/kern_event.h','libkern/OSAtomic.h','kern/clock.h','kern/thread.h','net/if.h','net/net_kev.h','net/if_dl.h','net/if_types.h','net/if_ether.h','net/ethernet.h','net/if_media.h']:
    h=p/name;h.parent.mkdir(parents=True,exist_ok=True);h.write_text('#include "stub.h"\n')
   (p/'sdio').mkdir();(p/'sdio/mock_provider.h').write_text(NATIVE_API)
   sdio=SOURCE.parent/'sdio'
   (p/'sdio/A04BR3WLANBackendAPI.h').write_text((sdio/'A04BR3WLANBackendAPI.h').read_text().replace('../network/','../'))
   for header in sdio.glob('*.h'):
    if header.name=='A04BR3WLANBackendAPI.h':continue
    text=header.read_text().replace('../network/','../')
    if header.name=='A04BR3WLANPersonalCrypto.h':
     text=text.replace('../bearssl/',str((SOURCE.parent/'host-fixture')/'libkern/crypto/a04br3_bearssl')+'/')
    (p/'sdio'/header.name).write_text(text)
   (p/'sys/random.h').write_text('extern "C" void read_random(void *,unsigned);\n')
   (p/'A04BR3NativeRendezvous.cpp').write_text((SOURCE/'A04BR3NativeRendezvous.cpp').read_text().replace('../sdio/','sdio/'))
   (p/'apple').mkdir()
   original=(SOURCE/'apple/IONetworkInterface.cpp').read_text()
   def function(signature):
    start=original.index(signature);at=original.index('{',start);depth=1;end=at+1
    while depth:
     if original[end]=='{':depth+=1
     elif original[end]=='}':depth-=1
     end+=1
    return original[start:end]+'\n'
   macros=original[original.index('#define IO_IFNET_GET'):original.index('//------------------------------------------------------------------------------',original.index('#define IO_IFNET_GET'))]
   real=macros+'\nIO_IFNET_GET(getFlags, UInt16, flags, ifnet_flags)\nIO_IFNET_RMW(setFlags, UInt16, flags, ifnet_set_flags)\n'
   real+=function('ifnet_t IONetworkInterface::getIfnet')
   real+='#define ABS(a) ((a)<0?-(a):(a))\n'+function('void IONetworkInterface::pushInputPacket')+function('bool IONetworkInterface::inputEvent')
   real+=(SOURCE/'apple/A04BR3IONetworkBacking.inc').read_text()
   stackSource=(SOURCE/'apple/IONetworkStack.cpp').read_text()
   for signature in ['IOReturn IONetworkStack::registerKernelInterface(', 'IOReturn IONetworkStack::registerNetworkInterface(']:
    a=stackSource.index(signature);i=stackSource.index('{',a);depth=1;j=i+1
    while depth:
     if stackSource[j]=='{':depth+=1
     elif stackSource[j]=='}':depth-=1
     j+=1
    real+=stackSource[a:j]+'\n'
   freeStart=original.index('    if (_backingIfnet)',original.index('void IONetworkInterface::free'))
   freeEnd=original.index('\n    }',freeStart)+6
   apple=APPLE.replace('FREE_BACKING_FRAGMENT',original[freeStart:freeEnd])
   (p/'apple/base.h').write_text('#include "stub.h"\n'+apple+'\n'+real)
   for name in ['IOEthernetController.h','IOEthernetInterface.h','IONetworkStack.h']:(p/'apple'/name).write_text('#include "base.h"\n')
   for h in SOURCE.glob('*.h'):(p/h.name).write_text(h.read_text().replace('../sdio/','sdio/'))
   for name in ['A04BR3Ethernet.cpp','A04BR3NetworkRuntime.cpp']:(p/name).write_text((SOURCE/name).read_text())
   (p/'driver.cpp').write_text((SOURCE/'A04BR3BSDNetwork.cpp').read_text());(p/'case.cpp').write_text(case)
   bear=(SOURCE.parent/'host-fixture')/'libkern/crypto/a04br3_bearssl';objects=[]
   for index,name in enumerate(['hash/sha1.c','codec/dec32be.c','codec/enc32be.c','symcipher/aes_ct.c','symcipher/aes_ct_dec.c','symcipher/aes_ct_cbcdec.c']):
    obj=p/('crypto'+str(index)+'.o');objects.append(str(obj))
    subprocess.run(['cc','-fsanitize=address,undefined','-I'+str(bear/'inc'),'-I'+str(bear/'src'),'-c',str(bear/'src'/name),'-o',str(obj)],check=True)
   for words in (0,1):
    with self.subTest(event_header_bytes=24+4*words):
     subprocess.run(['c++','-std=c++14','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-fsanitize=address,undefined','-fno-sanitize-recover=all','-DKERNEL','-DA04BR3_WIFI=1','-DA04_TEST_EVENT_WORDS='+str(words),'-I',str(p),str(p/'case.cpp'),*objects,'-o',str(p/'case')],check=True)
     subprocess.run([str(p/'case')],check=True)
 def test_source_wireless_registration(self):
  # Genuine source-defined metaclasses, one functional implementation each;
  # not IOClass properties or guessed private Apple vtable declarations.
  impl=(SOURCE/'A04BR3Ethernet.cpp').read_text()
  header=(SOURCE/'A04BR3Ethernet.h').read_text()
  for child,parent in [('IO80211Controller','IOEthernetController'),('IO80211Interface','IOEthernetInterface')]:
   self.assertIn('class '+child+' : public '+parent,header)
   declaration='OSDefineMetaClassAndStructors('+child+','+parent+')'
   self.assertIn(declaration,impl)
   definitions=[]
   for f in SOURCE.parent.rglob('*.cpp'):
    if declaration in f.read_text(errors='replace'):definitions.append(f)
   self.assertEqual(definitions,[SOURCE/'A04BR3Ethernet.cpp'])
  self.assertIn('return network.dispatch(command,arg1)',impl)
  self.assertNotIn('setProperty("IOClass"',impl)
  self.assertIn('if(type!=10 && type!=19 && type!=20 && type!=22)return EOPNOTSUPP;',(SOURCE/'A04BR3NativeRendezvous.cpp').read_text())
  import sys
  sys.path.insert(0,str(SOURCE.parents[2]/'scripts'))
  from a04br3_wifi_build import source_files
  files=source_files(SOURCE.parent)
  self.assertEqual(files.count(SOURCE/'A04BR3Ethernet.cpp'),1)
  self.assertFalse(any('IO80211Family' in str(path) for path in files))
 def test_pinned_provenance(self):
  manifest=json.loads((SOURCE/'apple/SOURCE.json').read_text())
  self.assertEqual(manifest['revision'],'bb910e2b458939f6b1f53c0fa6907f258b431ea4')
  for f in manifest['files']:
   self.assertEqual(hashlib.sha256((SOURCE/'apple'/f['path']).read_bytes()).hexdigest(),f['vendored_sha256'])
if __name__=='__main__':unittest.main()
