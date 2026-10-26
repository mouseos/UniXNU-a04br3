# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Compile actual bridge source with typed DLIL/IOKit mocks and ASan/UBSan."""
from pathlib import Path
import subprocess,tempfile,unittest
SOURCE=Path(__file__).resolve().parents[1]

STUB=r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
#include <map>
#include <string>
#include <stdarg.h>
#include <vector>
using UInt32=uint32_t;using SInt32=int32_t;
static SInt32 OSAddAtomic(SInt32 n,volatile SInt32 *p){return __atomic_fetch_add(p,n,__ATOMIC_SEQ_CST);}
static bool OSCompareAndSwap(UInt32 old,UInt32 next,volatile SInt32 *p){SInt32 expected=(SInt32)old;return __atomic_compare_exchange_n(p,&expected,(SInt32)next,false,__ATOMIC_SEQ_CST,__ATOMIC_SEQ_CST);}
static void OSMemoryBarrier(){__atomic_thread_fence(__ATOMIC_SEQ_CST);}
using clock_sec_t=int64_t;using clock_usec_t=int32_t;
static inline void clock_get_system_microtime(clock_sec_t *s,clock_usec_t *u){*s=2;*u=234567;}
static const unsigned IFNAMSIZ=16;
using user64_addr_t=uint64_t;using user32_addr_t=uint32_t;
using errno_t=int;using user_addr_t=uint64_t;using protocol_family_t=uint32_t;
struct Packet {bool freed;unsigned flags=2;size_t length=0,offset=0,used=0;uint8_t storage[1600]={};Packet *nextpkt=nullptr,*segment=nullptr;void *header=nullptr;bool heap=false;};using mbuf_t=Packet *;
#define MBUF_PKTHDR 2
#define MBUF_DONTWAIT 0
static inline unsigned mbuf_flags(mbuf_t p){return p->flags;}
static inline mbuf_t mbuf_nextpkt(mbuf_t p){return p->nextpkt;}
static inline size_t mbuf_pkthdr_len(mbuf_t p){return p->length;}
static inline void *mbuf_data(mbuf_t p){return p->storage+p->offset;}
static inline void *mbuf_pkthdr_header(mbuf_t p){return p->header;}
static inline size_t mbuf_leadingspace(mbuf_t p){return p->offset;}
static inline int mbuf_copydata(mbuf_t p,size_t offset,size_t n,void *out){
 uint8_t *d=(uint8_t *)out;
 for(;p&&n;p=p->segment){size_t available=p->used-p->offset;if(offset>=available){offset-=available;continue;}available-=offset;if(available>n)available=n;memcpy(d,p->storage+p->offset+offset,available);d+=available;n-=available;offset=0;}
 return n?EINVAL:0;
}
static int rxAllocError,rxCopyError,rxShortHeader,rxAllocNull;
static inline int mbuf_allocpacket(int how,size_t bytes,unsigned *,mbuf_t *out){assert(how==MBUF_DONTWAIT&&bytes<=1514);if(rxAllocError)return rxAllocError;if(rxAllocNull){*out=nullptr;return 0;}*out=new Packet{false};(*out)->heap=true;return 0;}
static inline int mbuf_copyback(mbuf_t p,size_t offset,size_t bytes,const void *in,int how){assert(!offset&&how==MBUF_DONTWAIT);if(rxCopyError)return rxCopyError;memcpy(p->storage,in,bytes);p->length=p->used=bytes;return 0;}
static inline size_t mbuf_len(mbuf_t p){return rxShortHeader?13:p->used-p->offset;}
static inline void mbuf_pkthdr_setheader(mbuf_t p,void *h){p->header=h;}
static inline void mbuf_adj(mbuf_t p,int bytes){assert(bytes==14);p->offset+=14;p->length-=14;}

struct IF;using ifnet_t=IF *;
using Out=int (*)(ifnet_t,mbuf_t);using Ioctl=int (*)(ifnet_t,unsigned long,void *);using Detached=void (*)(ifnet_t);
struct IOLock {bool held;};
static int lockCount,allocations,registrations,releaseCount,packetFrees,txCalls,nativeCalls,copyIns,copyOuts;
static int failAlloc,failMtu,failFlags,failAttach,failDetach,failMemory,copyInError,copyOutError;
static int sleeps,wakes;
#define THREAD_UNINT 0
static int childCount,failChildInit,failChildAttach,inputCalls,inputError,heldLocks;
static bool inlineDetach;
static int credentialRoot=1,credentialChecks;
using kauth_cred_t=int;
static kauth_cred_t kauth_cred_get(){credentialChecks++;return credentialRoot;}
static int kauth_cred_issuser(kauth_cred_t c){return c;}
static IOLock *IOLockAlloc(){lockCount++;return new IOLock{false};}
static void IOLockLock(IOLock *p){assert(p&&!p->held);p->held=true;heldLocks++;}
static void IOLockUnlock(IOLock *p){assert(p&&p->held);p->held=false;heldLocks--;}
static void IOLockFree(IOLock *p){assert(p&&!p->held);delete p;lockCount--;}
static int IOLockSleep(IOLock *,void *,int);
static void IOLockWakeup(IOLock *p,void *,bool){assert(p->held);wakes++;}
static void *IOMallocZero(unsigned n){if(failMemory)return nullptr;allocations++;return calloc(1,n);}
static void IOFree(void *p,unsigned){assert(p&&allocations>0);allocations--;free(p);}
static std::vector<std::string> traceRows;
static void IOLog(const char *format,...){
 if(strncmp(format,"A04BR3 native ioctl:",19))return;
 char text[128];va_list args;va_start(args,format);vsnprintf(text,sizeof(text),format,args);va_end(args);
 traceRows.push_back(text);
}
class IOService {
public:
 bool parent=true,heap=false;IOService *provider=nullptr;unsigned refs=1;std::map<std::string,std::string> text;std::map<std::string,uint64_t> number;
 IOService *getProvider(){return parent?(provider?provider:this):nullptr;}
 bool init(){heap=true;childCount++;return !failChildInit;}
 bool attach(IOService *p){if(failChildAttach)return false;provider=p;return true;}
 void detach(IOService *p){assert(p==provider);provider=nullptr;parent=false;}
 void retain(){refs++;}void release(){assert(refs);refs--;if(!refs){assert(heap);childCount--;delete this;}}
 void setProperty(const char *k,const char *v){text[k]=v;}
 void setProperty(const char *k,uint64_t v,int){number[k]=v;}
 void setProperty(const char *k,bool v){number[k]=v;}
 void setProperty(const char *k,void *p,unsigned n){text[k]=std::string((char *)p,n);}
 void removeProperty(const char *k){text.erase(k);number.erase(k);}
 void registerService(){registrations++;}
};
#define IFNET_INIT_CURRENT_VERSION 2
#define IFNET_INIT_LEGACY 1
#define IFNET_FAMILY_ETHERNET 2
#define IFNET_SUBFAMILY_WIFI 3
#define IFT_ETHER 6
#define AF_LINK 18
#define IFF_UP 1
#define IFF_RUNNING 64
#define IFF_BROADCAST 2
#define IFF_SIMPLEX 2048
#define IFF_MULTICAST 32768
#define SIOCSIFFLAGS 0x80206910UL
#define SIOCSIFMEDIA 0x80206937UL
struct ifreq {char ifr_name[16];int ifr_media;};
#define SIOCGIFMEDIA32 0xc0286938UL
#define SIOCGIFMEDIA64 0xc02c6938UL
#define IFM_IEEE80211 0x80
#define IFM_AUTO 0
#define IFM_NONE 2
#define IFM_NMASK 0xe0
#define IFM_AVALID 1
#define IFM_ACTIVE 2
MEDIA_STRUCTS
struct sockaddr {};
struct sockaddr_dl {uint8_t sdl_len,sdl_family;uint16_t sdl_index;uint8_t sdl_type,sdl_nlen,sdl_alen,sdl_slen;char sdl_data[24];};
#define LLADDR(p) ((p)->sdl_data+(p)->sdl_nlen)
static int ether_demux(ifnet_t,mbuf_t,char *,protocol_family_t *){return 0;}
struct ifnet_demux_desc {};
static int ether_add_proto(ifnet_t,protocol_family_t,const ifnet_demux_desc *,uint32_t){return 0;}
static int ether_del_proto(ifnet_t,protocol_family_t){return 0;}
static int ether_check_multi(ifnet_t,const sockaddr *){return 0;}
static int ether_frameout_extended(ifnet_t,mbuf_t *,const sockaddr *,const char *,const char *,uint32_t *,uint32_t *){return 0;}
struct ifnet_init_eparams {
 unsigned ver,len,flags;const void *uniqueid;unsigned uniqueid_len;const char *name;unsigned unit,family,type,subfamily;
 Out output;decltype(&ether_demux) demux;decltype(&ether_add_proto) add_proto;decltype(&ether_del_proto) del_proto;
 decltype(&ether_check_multi) check_multi;decltype(&ether_frameout_extended) framer_extended;
 void *softc;Ioctl ioctl;Detached detach;const void *broadcast_addr;unsigned broadcast_len;
};
struct IF {ifnet_init_eparams init;unsigned flags,refs;bool attached;unsigned addrlen=0,hdrlen=0;};
static IF *current,*pending;
static int ifnet_allocate_extended(const ifnet_init_eparams *p,ifnet_t *out){
 assert(p->ver==2 && p->len==sizeof(*p) && p->flags==IFNET_INIT_LEGACY);
 assert(!strcmp(p->name,"en") && p->family==IFNET_FAMILY_ETHERNET && p->type==IFT_ETHER && p->subfamily==IFNET_SUBFAMILY_WIFI);
 assert(p->uniqueid_len==6 && !((const uint8_t *)p->uniqueid)[0]);
 if(failAlloc)return ENOMEM;assert(!current);current=new IF{*p,0,1,false};*out=current;return 0;
}
static void *ifnet_softc(ifnet_t p){return p->init.softc;}
static unsigned ifnet_family(ifnet_t p){return p->init.family;}
static int failMediaLength;
static int ifnet_set_addrlen(ifnet_t p,unsigned n){assert(n==6);if(failMediaLength==1)return EINVAL;p->addrlen=n;return 0;}
static int ifnet_set_hdrlen(ifnet_t p,unsigned n){assert(n==14);if(failMediaLength==2)return EINVAL;p->hdrlen=n;return 0;}
static int ifnet_set_mtu(ifnet_t,unsigned n){assert(n==1500);return failMtu;}
static int ifnet_set_flags(ifnet_t p,unsigned v,unsigned mask){p->flags=(p->flags&~mask)|(v&mask);return failFlags;}
static int ifnet_attach(ifnet_t p,const sockaddr_dl *a){assert(a->sdl_alen==6 && a->sdl_family==AF_LINK && !memcmp(LLADDR(a),p->init.uniqueid,6));if(failAttach)return failAttach;p->attached=true;return 0;}
static int ifnet_release(ifnet_t p){assert(p->refs);p->refs--;if(p->refs)return 0;if(p==current)current=nullptr;if(p==pending)pending=nullptr;delete p;releaseCount++;return 0;}
#ifndef A04_TEST_EVENT_WORDS
#define A04_TEST_EVENT_WORDS 1
#endif
#if A04_TEST_EVENT_WORDS == 0
#define A04_TEST_EVENT_ARRAY 0 /* GCC host ABI model of XNU flexible member: sizeof24, offset24 */
#else
#define A04_TEST_EVENT_ARRAY 1
#endif
struct kern_event_msg {uint32_t total_size,vendor_code,kev_class,kev_subclass,id,event_code,event_data[A04_TEST_EVENT_ARRAY];};
static_assert(offsetof(kern_event_msg,event_data)==24,"both XNU event layouts share header offset");
static_assert(sizeof(kern_event_msg)==24+4*A04_TEST_EVENT_WORDS,"exercise actual24 and legacy28 headers");
static const unsigned KEV_VENDOR_APPLE=1,KEV_NETWORK_CLASS=1,KEV_DL_SUBCLASS=2,KEV_DL_LINK_ON=13,KEV_DL_LINK_OFF=12;
static int linkUps,linkDowns;static void (*eventHook)()=nullptr;
static int baseEvents,scanEvents,eventError;static ifnet_t lastBaseIfnet;
static int ifnet_event(ifnet_t p,kern_event_msg *e){
 assert(p && p==current && !heldLocks);lastBaseIfnet=p;baseEvents++;
 if(e->event_code==10){
  assert(e->total_size==40 && e->vendor_code==1 && e->kev_class==6 && e->kev_subclass==1);
  assert(!memcmp((uint8_t *)e+24,"en7\0\0\0\0\0\0\0\0\0\0\0\0\0",16));
  assert(p->refs>=2);scanEvents++;
 }
 if(e->kev_class==KEV_NETWORK_CLASS && e->kev_subclass==KEV_DL_SUBCLASS &&
    (e->event_code==KEV_DL_LINK_ON || e->event_code==KEV_DL_LINK_OFF)){
  assert(e->total_size==48 && e->vendor_code==KEV_VENDOR_APPLE && p->refs>=2);
  uint32_t family,unit;memcpy(&family,(uint8_t *)e+24,4);memcpy(&unit,(uint8_t *)e+28,4);
  assert(family==IFNET_FAMILY_ETHERNET&&unit==7&&!memcmp((uint8_t *)e+32,"en\0\0\0\0\0\0\0\0\0\0\0\0\0\0",16));
  if(!eventError){if(e->event_code==KEV_DL_LINK_ON)linkUps++;else linkDowns++;}
  if(eventHook)eventHook();
 }
 return eventError;
}
static int failInputReference;
static int ifnet_reference(ifnet_t p){if(failInputReference)return failInputReference;assert(p&&p->refs);p->refs++;return 0;}
static const char *ifnet_name(ifnet_t p){return p->init.name;}
static unsigned ifnet_unit(ifnet_t p){return p->init.unit;}
static unsigned ifnet_flags(ifnet_t p){return p->flags;}
static int ifnet_detach(ifnet_t p){if(failDetach)return failDetach;pending=p;if(inlineDetach)p->init.detach(p);return 0;}
static void completeDetach(){assert(pending);auto p=pending;p->init.detach(p);}
static int IOLockSleep(IOLock *g,void *,int){sleeps++;IOLockUnlock(g);completeDetach();IOLockLock(g);return 0;}
static void mbuf_freem(mbuf_t p){assert(!p->freed);p->freed=true;packetFrees++;if(p->segment)mbuf_freem(p->segment);if(p->heap)delete p;}
static int mbuf_freem_list(mbuf_t p){int n=0;while(p){auto next=p->nextpkt;mbuf_freem(p);p=next;++n;}return n;}
static void (*inputObserver)(ifnet_t,mbuf_t)=nullptr;
static int ifnet_input(ifnet_t p,mbuf_t m,const void *){assert(p->refs==2 && !heldLocks);inputCalls++;if(inputObserver)inputObserver(p,m);mbuf_freem(m);return inputError;}
static uint8_t userBuffer[16384];
static int copyin(user_addr_t a,void *out,size_t n){copyIns++;if(copyInError)return copyInError;if(a!=(uintptr_t)userBuffer || n>sizeof(userBuffer))return EFAULT;memcpy(out,userBuffer,n);return 0;}
static int copyout(const void *in,user_addr_t a,size_t n){copyOuts++;if(copyOutError)return copyOutError;if((a!=(uintptr_t)userBuffer && a!=0x1234) || n>sizeof(userBuffer))return EFAULT;memcpy(userBuffer,in,n);return 0;}
'''

_private=((SOURCE.parent/'host-fixture')/'bsd/net/if_private.h').read_text()
_a=_private.index('struct ifmediareq64 {');_b=_private.index('\nstruct ifdrv32',_a)
STUB=STUB.replace('MEDIA_STRUCTS','#pragma pack(4)\n'+_private[_a:_b]+'\n#pragma pack()\nstatic_assert(sizeof(ifmediareq64)==44 && sizeof(ifmediareq32)==40,"native media layouts");\n')

CASE=r'''
#include "driver.cpp"
static a04_network_state realState;
static int setCalls,setError;static uint32_t lastSetType,lastSetValue,lastSetBytes;
static int stateError,nativeError,adminError,transmitError,adminCalls;static unsigned backendOutput,lastAdmin;
static int state(void *,a04_network_state *s){*s=realState;return stateError;}
static int get(void *,uint32_t type,uint32_t value,void *p,uint32_t *n){nativeCalls++;assert((type==12 && value==7) || (a04_native_scalar_type(type) && value==0 && *n==4));if(nativeError)return nativeError;memset(p,0x53,*n);*n=backendOutput;return 0;}
static int set(void *,uint32_t type,uint32_t value,const void *p,uint32_t n){
 setCalls++;lastSetType=type;lastSetValue=value;lastSetBytes=n;
 if(n){assert(p && p!=userBuffer && !memcmp(p,userBuffer,n));}else assert(!p);
 return setError;
}
static int admin(void *,unsigned enabled){adminCalls++;lastAdmin=enabled;return adminError;}
static int tx(void *,void *p){txCalls++;if(transmitError)return transmitError;mbuf_freem((mbuf_t)p);return 0;}
static void reset(){linkUps=linkDowns=0;eventHook=nullptr;baseEvents=scanEvents=eventError=0;lastBaseIfnet=nullptr;assert(!childCount && !heldLocks);inputCalls=inputError=failChildInit=failChildAttach=0;assert(!lockCount && !allocations && !current && !pending);realState={};realState.present=realState.mac_valid=1;const uint8_t initialMac[6]={0,2,3,4,5,6};memcpy(realState.mac,initialMac,6);
 stateError=nativeError=adminError=transmitError=failAlloc=failMtu=failFlags=failAttach=failDetach=failMemory=copyInError=copyOutError=0;
 credentialRoot=1;credentialChecks=setCalls=setError=0;adminCalls=0;lastAdmin=99;registrations=releaseCount=packetFrees=txCalls=nativeCalls=copyIns=copyOuts=0;backendOutput=4;inlineDetach=false;}
static a04_apple80211_req64 req(){a04_apple80211_req64 r={};strcpy(r.name,"en7");r.type=12;r.value=7;r.length=8;r.user_data=(uintptr_t)userBuffer;return r;}
static void ready(){realState.firmware_running=realState.control_ready=realState.datapath_ready=realState.associated=1;}
static void cleanup(A04BR3BSDNetwork &n,IOService &s){assert(n.detach()==0);if(pending)completeDetach();assert(n.isDetached() && n.finish()==0 && s.refs==1);assert(!lockCount && !current);}
static void mediaCases(){
 reset();a04_network_backend b={};b.state=state;b.native_get=get;b.set_admin=admin;b.transmit=tx;b.native_set=set;A04BR3BSDNetwork bridge;IOService service;
 realState.firmware_running=realState.control_ready=1;assert(!bridge.attach(&service,&b,7));
 ifmediareq64 m={};strcpy(m.ifm_name,"en7");
 assert(!current->init.ioctl(current,SIOCGIFMEDIA64,&m));
 // Exact donor __getIfListCopy predicate: discovery BEFORE native GET12.
 assert((m.ifm_current&0xe0)==0x80 && m.ifm_count==1 && m.ifm_mask==0);
 assert(m.ifm_status==IFM_AVALID && m.ifm_active==(IFM_IEEE80211|IFM_NONE));
 assert(!nativeCalls && !setCalls && !adminCalls && !txCalls && !(current->flags&IFF_RUNNING));
 memset(userBuffer,0xa5,sizeof(userBuffer));m.ifm_count=1000000;m.ifmu_ulist=(uintptr_t)userBuffer;
 assert(!current->init.ioctl(current,SIOCGIFMEDIA64,&m) && m.ifm_count==1);
 uint32_t word;memcpy(&word,userBuffer,4);assert(word==(IFM_IEEE80211|IFM_AUTO));
 for(unsigned i=4;i<sizeof(userBuffer);i++)assert(userBuffer[i]==0xa5);
 ifmediareq32 n={};strcpy(n.ifm_name,"en7");n.ifm_count=1;n.ifmu_ulist=0x1234;
 assert(!current->init.ioctl(current,SIOCGIFMEDIA32,&n) && n.ifm_count==1 && (n.ifm_current&IFM_NMASK)==IFM_IEEE80211);
 realState.radio_enabled=realState.associated=1;realState.datapath_ready=0;
 m.ifm_count=0;assert(!current->init.ioctl(current,SIOCGIFMEDIA64,&m) && !(m.ifm_status&IFM_ACTIVE));
 realState.datapath_ready=1;assert(!current->init.ioctl(current,SIOCGIFMEDIA64,&m) && (m.ifm_status&IFM_ACTIVE));
 realState.radio_enabled=0;assert(!current->init.ioctl(current,SIOCGIFMEDIA64,&m) && !(m.ifm_status&IFM_ACTIVE));
 for(unsigned fault=0;fault<5;fault++){
  m={};strcpy(m.ifm_name,"en7");m.ifm_status=12345;
  int want=EINVAL;
  if(fault==0){m.ifm_count=-1;}
  if(fault==1){m.ifm_name[2]='8';want=ENXIO;}
  if(fault==2){m.ifm_count=1;want=EFAULT;}
  if(fault==3){m.ifm_count=1;m.ifmu_ulist=(uintptr_t)userBuffer;copyOutError=EFAULT;want=EFAULT;}
  if(fault==4){realState.control_ready=0;want=ENETDOWN;}
  auto before=m;assert(current->init.ioctl(current,SIOCGIFMEDIA64,&m)==want && !memcmp(&m,&before,sizeof(m)));
  copyOutError=0;realState.control_ready=1;
 }
 assert(current->init.ioctl(current,SIOCGIFMEDIA64,nullptr)==EINVAL);
 ifreq selection={};strcpy(selection.ifr_name,"en7");selection.ifr_media=IFM_IEEE80211|IFM_AUTO;
 assert(!current->init.ioctl(current,SIOCSIFMEDIA,&selection));
 assert(!nativeCalls && !setCalls && !adminCalls && !txCalls && !(current->flags&IFF_RUNNING));
 for(int mode:{IFM_IEEE80211|IFM_NONE,IFM_IEEE80211|0x100,0x20}){
  selection.ifr_media=mode;assert(current->init.ioctl(current,SIOCSIFMEDIA,&selection)==EOPNOTSUPP);
 }
 selection.ifr_media=IFM_IEEE80211|IFM_AUTO;
 selection.ifr_name[2]='8';assert(current->init.ioctl(current,SIOCSIFMEDIA,&selection)==ENXIO);
 memset(selection.ifr_name,'x',16);assert(current->init.ioctl(current,SIOCSIFMEDIA,&selection)!=0);
 strcpy(selection.ifr_name,"en7");realState.control_ready=0;
 assert(current->init.ioctl(current,SIOCSIFMEDIA,&selection)==ENETDOWN);realState.control_ready=1;
 assert(current->init.ioctl(current,SIOCSIFMEDIA,nullptr)==EINVAL);
 assert(!a04_native_trace_count); // standard discovery never consumes native32 trace quota
 cleanup(bridge,service);
}
static void traceCases(){
 reset();a04_network_backend b={};b.state=state;b.native_get=get;b.set_admin=admin;b.transmit=tx;b.native_set=set;A04BR3BSDNetwork bridge;IOService service;
 ready();assert(!bridge.attach(&service,&b,7));
 for(unsigned i=0;i<64;i++){
  auto r=req();unsigned cmd=i%2?A04_APPLE80211_SET64:A04_APPLE80211_GET64;
  credentialRoot=i!=1;
  int result=current->init.ioctl(current,cmd,&r);assert(result==(i==1?EPERM:0));
  unsigned expected=i==0?1:(i<3?2:3);
  assert(traceRows.size()==expected && (unsigned)a04_native_trace_count==expected);
 }
 // Repeated power is one metadata tuple, not1000 consumed slots.
 for(unsigned i=0;i<1000;i++)a04_native_trace(A04_APPLE80211_GET64,19,0,24,0);
 assert(traceRows.size()==4 && a04_native_trace_count==4);
 a04_native_trace(A04_APPLE80211_SET64,10,0,5456,EOPNOTSUPP);
 assert(traceRows.size()==5 && traceRows.back().find("type=10 value=0 length=5456 errno=")!=std::string::npos);
 a04_native_trace(A04_APPLE80211_SET64,10,0,5456,0);
 assert(traceRows.size()==6 && a04_native_trace_count==6);
 a04_native_trace_busy=1;a04_native_trace(A04_APPLE80211_GET64,999,0,4,0);
 assert(a04_native_trace_count==6);a04_native_trace_unlock();credentialRoot=1;
 // Scalar GET96 has no user payload: the only output is req.value, while
 // production backend uses an explicit normalized four-byte scratch buffer.
 auto scalar=req();scalar.type=96;scalar.value=0;scalar.length=0;scalar.user_data=0;
 int ins=copyIns,outs=copyOuts;backendOutput=4;
 assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&scalar));
 assert(scalar.value==0x53535353 && scalar.length==0 && !scalar.user_data && copyIns==ins && copyOuts==outs);
 for(unsigned type:{5u,8u,13u,15u,18u,21u,57u,96u}){
  scalar.type=type;scalar.value=0;assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&scalar));
  assert(scalar.value==0x53535353 && !scalar.length && copyIns==ins && copyOuts==outs);
 }
 scalar.type=96;
 scalar.value=0;nativeError=EOPNOTSUPP;
 assert(current->init.ioctl(current,A04_APPLE80211_GET64,&scalar)==EOPNOTSUPP && !scalar.value);
 nativeError=0;backendOutput=3;
 assert(current->init.ioctl(current,A04_APPLE80211_GET64,&scalar)==EOVERFLOW && !scalar.value);
 backendOutput=4;
 a04_network_copy scalarCopy={nullptr,a04_network_copyin,a04_network_copyout};uint8_t small[4]={};
 int priorCalls=nativeCalls;
 assert(a04_network_get(&b,&realState,&scalar,"en7",small,3,&scalarCopy)==EMSGSIZE && nativeCalls==priorCalls);
 scalar.user_data=(uintptr_t)userBuffer;
 assert(current->init.ioctl(current,A04_APPLE80211_GET64,&scalar)==EINVAL);
 scalar.length=4;assert(current->init.ioctl(current,A04_APPLE80211_GET64,&scalar)==EINVAL);
 scalar=req();scalar.type=95;scalar.length=0;scalar.user_data=0;
 assert(current->init.ioctl(current,A04_APPLE80211_GET64,&scalar)==EINVAL);
 cleanup(bridge,service);
 // Fill the remaining distinct tuple budget; repeated/novel calls after32
 // cannot grow the log, nor can detach/reattach renew the boot budget.
 for(unsigned type=200;type<264;type++)a04_native_trace(A04_APPLE80211_GET64,type,0,4,0);
 assert(a04_native_trace_count==32 && traceRows.size()==32);
 // A detach/reattach does not renew the boot-wide quota.
 assert(!bridge.attach(&service,&b,7));auto r=req();assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&r));assert(traceRows.size()==32);
 cleanup(bridge,service);
}
int main(){
 mediaCases();
 traceCases();
 a04_network_backend b={};b.state=state;b.native_get=get;b.set_admin=admin;b.transmit=tx;b.native_set=set;
 // No invented identity or parent; no ifnet allocated for absent hardware.
 for(unsigned fault=0;fault<5;fault++){
  reset();A04BR3BSDNetwork n;IOService s;
  if(fault==0)s.parent=false;if(fault==1)realState.present=0;if(fault==2)realState.mac_valid=0;
  if(fault==3)memset(realState.mac,0,6);if(fault==4)realState.mac[0]=1;
  assert(n.attach(&s,&b,7)!=0 && !current && !lockCount && !registrations && s.refs==1);
 }
 for(unsigned fault=0;fault<4;fault++){
  reset();A04BR3BSDNetwork n;IOService s;
  if(fault==0)failAlloc=1;if(fault==1)failMtu=EIO;if(fault==2)failFlags=EIO;if(fault==3)failAttach=EIO;
  assert(n.attach(&s,&b,7)!=0 && !current && !lockCount && !registrations && s.refs==1);
 }
 reset();A04BR3BSDNetwork n;IOService s;assert(n.attach(&s,&b,7)==0 && current && registrations==1);
 assert(s.text["IOInterfaceName"]=="en7" && s.text["IO80211InterfaceRole"]=="Infrastructure");
 assert(s.text["IOBSDName"]=="en7" && s.number["IOInterfaceType"]==IFT_ETHER && s.text["IOMACAddress"].size()==6);
 assert(!s.number["WLANBackendReady"] && !s.number["WLANDataPathReady"] && !(current->flags&(IFF_UP|IFF_RUNNING)));
 assert(n.finish()==EBUSY);auto r=req();assert(current->init.ioctl(current,A04_APPLE80211_GET64,&r)==ENETDOWN && !copyIns && !nativeCalls);
 Packet p={false};assert(current->init.output(current,&p)==ENETDOWN && p.freed && packetFrees==1 && !txCalls);
 current->flags|=IFF_UP;assert(current->init.ioctl(current,SIOCSIFFLAGS,nullptr)==ENETDOWN && !(current->flags&(IFF_UP|IFF_RUNNING)));
 // Radio UP is permitted before association/datapath. Native control GET
 // reaches only the mock backend; no scan results exist in production code.
 realState.firmware_running=realState.control_ready=1;
 assert(!realState.associated && !realState.datapath_ready);
 current->flags|=IFF_UP;assert(!current->init.ioctl(current,SIOCSIFFLAGS,nullptr));
 assert(adminCalls==1 && lastAdmin==1 && (current->flags&IFF_UP) && !(current->flags&IFF_RUNNING));
 assert(s.number["WLANBackendReady"] && !s.number["WLANDataPathReady"]);
 r=req();assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&r) && r.length==4 && nativeCalls==1);
 // SET credentials are checked before copyin/dispatch; GET remains unprivileged.
 int beforeIn=copyIns,beforeOut=copyOuts;
 credentialRoot=0;r=req();assert(current->init.ioctl(current,A04_APPLE80211_SET64,&r)==EPERM && !setCalls && copyIns==beforeIn);
 assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&r));credentialRoot=1;
 beforeOut=copyOuts;r=req();r.type=0x12345678;r.value=0x9abcdef0;
 assert(!current->init.ioctl(current,A04_APPLE80211_SET64,&r) && setCalls==1);
 assert(lastSetType==0x12345678 && lastSetValue==0x9abcdef0 && lastSetBytes==8 && r.length==8 && copyOuts==beforeOut);
 assert(!(current->flags&IFF_RUNNING)); // SET success never promotes association.
 r=req();r.length=16384;assert(!current->init.ioctl(current,A04_APPLE80211_SET64,&r) && lastSetBytes==16384);
 setError=EIO;r=req();assert(current->init.ioctl(current,A04_APPLE80211_SET64,&r)==EIO);setError=0;
 setError=EOPNOTSUPP;r=req();assert(current->init.ioctl(current,A04_APPLE80211_SET64,&r)==EOPNOTSUPP && r.length==8 && copyOuts==beforeOut);setError=0;
 int beforeSet=setCalls;copyInError=EFAULT;r=req();assert(current->init.ioctl(current,A04_APPLE80211_SET64,&r)==EFAULT && setCalls==beforeSet);copyInError=0;
 for(unsigned fault=0;fault<5;fault++){
  r=req();if(fault==0)memset(r.name,'x',16);if(fault==1)strcpy(r.name,"en8");
  if(fault==2)r.length=16385;if(fault==3)r.user_data=0;if(fault==4)r.user_data=1;
  assert(current->init.ioctl(current,A04_APPLE80211_SET64,&r)!=0 && setCalls==beforeSet && copyOuts==beforeOut);
 }
 beforeIn=copyIns;r=req();r.length=0;r.user_data=0;
 assert(!current->init.ioctl(current,A04_APPLE80211_SET64,&r) && lastSetBytes==0 && r.length==0 && copyIns==beforeIn);
 realState.control_ready=0;r=req();beforeSet=setCalls;
 assert(current->init.ioctl(current,A04_APPLE80211_SET64,&r)==ENETDOWN && setCalls==beforeSet);realState.control_ready=1;
 failMemory=1;r=req();assert(current->init.ioctl(current,A04_APPLE80211_SET64,&r)==ENOMEM && setCalls==beforeSet);failMemory=0;
 // Pure helper also refuses explicit unprivileged use, independently of adapter.
 a04_network_copy c={nullptr,a04_network_copyin,a04_network_copyout};uint8_t scratch[8]={};r=req();
 assert(a04_network_set(&b,&realState,&r,"en7",scratch,8,&c,0)==EPERM && setCalls==beforeSet);
 assert(current->init.ioctl(current,0x802069c8,nullptr)==EOPNOTSUPP);

 p={false};assert(current->init.output(current,&p)==ENETDOWN && p.freed && !txCalls);
 current->flags&=~IFF_UP;assert(!current->init.ioctl(current,SIOCSIFFLAGS,nullptr) && lastAdmin==0 && !(current->flags&IFF_RUNNING));
 adminError=EIO;current->flags|=IFF_UP;assert(current->init.ioctl(current,SIOCSIFFLAGS,nullptr)==EIO && !(current->flags&(IFF_UP|IFF_RUNNING)));adminError=0;
 ready();assert(n.refresh()==0 && s.number["WLANBackendReady"] && s.number["WLANDataPathReady"]);
 assert(!(current->flags&IFF_RUNNING));r=req();assert(current->init.ioctl(current,A04_APPLE80211_GET64,&r)==0 && r.length==4 && userBuffer[0]==0x53);
 current->flags|=IFF_UP;assert(!current->init.ioctl(current,SIOCSIFFLAGS,nullptr));assert(!n.refresh() && (current->flags&IFF_RUNNING));
 p={false};assert(!current->init.output(current,&p) && p.freed && txCalls==1);
 Packet rx={false};assert(!n.input(&rx) && rx.freed && inputCalls==1 && current->refs==1);
 inputError=EIO;rx={false};assert(n.input(&rx)==EIO && rx.freed && inputCalls==2 && current->refs==1);inputError=0;
 transmitError=EIO;p={false};assert(current->init.output(current,&p)==EIO && p.freed && txCalls==2);
 int outputs=copyOuts,calls=nativeCalls;
 for(unsigned fault=0;fault<6;fault++){
  r=req();if(fault==0)memset(r.name,'x',16);if(fault==1)strcpy(r.name,"en8");
  if(fault==2)r.length=0;if(fault==3)r.length=16385;if(fault==4)r.user_data=0;if(fault==5)r.user_data=1;
  assert(current->init.ioctl(current,A04_APPLE80211_GET64,&r)!=0 && copyOuts==outputs);
 }
 assert(nativeCalls==calls);
 nativeError=EOPNOTSUPP;r=req();assert(current->init.ioctl(current,A04_APPLE80211_GET64,&r)==EOPNOTSUPP && r.length==8 && copyOuts==outputs);nativeError=0;
 backendOutput=9;r=req();assert(current->init.ioctl(current,A04_APPLE80211_GET64,&r)==EOVERFLOW && copyOuts==outputs);backendOutput=4;
 copyOutError=EFAULT;r=req();assert(current->init.ioctl(current,A04_APPLE80211_GET64,&r)==EFAULT && r.length==8);copyOutError=0;
 assert(current->init.ioctl(current,A04_APPLE80211_SET64,nullptr)==EINVAL);assert(current->init.ioctl(current,0xc02069c9,nullptr)==EOPNOTSUPP);
 realState.datapath_ready=0;assert(!n.refresh() && !(current->flags&IFF_RUNNING) && !s.number["WLANDataPathReady"]);
 realState.firmware_running=0;assert(!n.refresh() && !s.number["WLANBackendReady"]);
 realState.mac[5]++;assert(n.refresh()==ENXIO && !s.number["WLANBackendReady"]);realState.mac[5]--;
 assert(n.detach()==0 && !n.isDetached() && n.finish()==EBUSY && !s.text.count("IOInterfaceName"));
 r=req();assert(current->init.ioctl(current,A04_APPLE80211_GET64,&r)==ENXIO);
 r=req();assert(current->init.ioctl(current,A04_APPLE80211_SET64,&r)==ENXIO);
 completeDetach();assert(n.isDetached() && n.finish()==0 && releaseCount==1 && s.refs==1);
 reset();A04BR3BSDNetwork fast;IOService t;assert(!fast.attach(&t,&b,7));inlineDetach=true;cleanup(fast,t);
 reset();A04BR3BSDNetwork retry;IOService u;assert(!retry.attach(&u,&b,7));ready();assert(!retry.refresh());current->flags|=IFF_UP;assert(!current->init.ioctl(current,SIOCSIFFLAGS,nullptr));
 failDetach=EIO;assert(retry.detach()==EIO && retry.finish()==EBUSY);
 assert(u.text["IOInterfaceName"]=="en7" && u.text["IO80211InterfaceRole"]=="Infrastructure" && u.number["IOInterfaceUnit"]==7);
 assert(u.number["WLANBackendReady"] && !(current->flags&(IFF_UP|IFF_RUNNING)));
 r=req();assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&r));
 current->flags|=IFF_UP;assert(!current->init.ioctl(current,SIOCSIFFLAGS,nullptr) && (current->flags&IFF_RUNNING));
 failDetach=0;cleanup(retry,u);
 reset();a04_network_backend missing=b;missing.native_get=nullptr;A04BR3BSDNetwork noControl;IOService v;ready();assert(!noControl.attach(&v,&missing,7) && !v.number["WLANBackendReady"] && !v.number["WLANDataPathReady"]);cleanup(noControl,v);
 reset();a04_network_backend noTx=b;noTx.transmit=nullptr;A04BR3BSDNetwork controlOnly;IOService w;
 realState.firmware_running=realState.control_ready=1;
 assert(!controlOnly.attach(&w,&noTx,7));current->flags|=IFF_UP;
 assert(!current->init.ioctl(current,SIOCSIFFLAGS,nullptr) && lastAdmin==1 && !(current->flags&IFF_RUNNING));
 r=req();assert(!current->init.ioctl(current,A04_APPLE80211_GET64,&r));cleanup(controlOnly,w);
 reset();a04_network_backend noSet=b;noSet.native_set=nullptr;A04BR3BSDNetwork getOnly;IOService z;
 realState.firmware_running=realState.control_ready=1;assert(!getOnly.attach(&z,&noSet,7));r=req();
 assert(current->init.ioctl(current,A04_APPLE80211_SET64,&r)==EOPNOTSUPP && !copyIns && !setCalls);
 cleanup(getOnly,z);
 // Provider-child construction/cleanup and failed construction unwind.
 for(unsigned fault=0;fault<3;fault++){
  reset();A04BR3BSDNetwork failedChild;IOService provider;
  if(fault==0)failChildInit=1;if(fault==1)failChildAttach=1;if(fault==2)realState.present=0;
  assert(failedChild.attachProvider(&provider,&b,7)!=0 && !childCount && !current && !lockCount);
 }
 reset();A04BR3BSDNetwork owned;IOService provider;
 assert(!owned.attachProvider(&provider,&b,7) && childCount==1 && owned.registryEntry()->getProvider()==&provider);
 assert(owned.registryEntry()->text["IOBSDName"]=="en7");
 Packet rxDown={false};assert(owned.input(&rxDown)==ENETDOWN && rxDown.freed && !inputCalls);
 assert(!owned.detach() && owned.finish()==EBUSY && childCount==1);
 Packet rxStopping={false};assert(owned.input(&rxStopping)==ENXIO && rxStopping.freed && !inputCalls);
 assert(!owned.registryEntry()->text.count("IOBSDName") && !owned.registryEntry()->text.count("IOMACAddress"));
 completeDetach();
 assert(!owned.finish() && !childCount && !lockCount);
 Packet rxClosed={false};assert(owned.input(&rxClosed)==ENXIO && rxClosed.freed);
 assert(owned.input(nullptr)==EINVAL);
 reset();A04BR3BSDNetwork wait;IOService parent;
 assert(!wait.attachProvider(&parent,&b,7));int oldSleeps=sleeps;
 assert(!wait.detachAndWait() && sleeps==oldSleeps+1 && !childCount && !lockCount);


}
'''

class BSDNetwork(unittest.TestCase):
    def test_actual_bridge(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'stub.h').write_text(STUB)
            for name in ['IOKit/IOLib.h','IOKit/IOService.h','net/kpi_interface.h','sys/systm.h','sys/sockio.h','sys/kpi_mbuf.h','sys/kauth.h','sys/kern_event.h','libkern/OSAtomic.h','kern/thread.h','net/if.h','net/net_kev.h','net/if_dl.h','net/if_types.h','net/if_ether.h','net/ethernet.h','net/if_media.h']:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('#include "stub.h"\n')
            (root/'driver.cpp').write_text((SOURCE/'A04BR3BSDNetwork.cpp').read_text())
            (root/'case.cpp').write_text(CASE);exe=root/'case'
            for words in (0,1):
                with self.subTest(event_header_bytes=24+4*words):
                    subprocess.run(['c++','-std=c++14','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-fsanitize=address,undefined','-fno-sanitize-recover=all','-DA04_TEST_EVENT_WORDS='+str(words),'-I',str(root),'-I',str(SOURCE),str(root/'case.cpp'),'-o',str(exe)],check=True)
                    subprocess.run([str(exe)],check=True)

if __name__=='__main__':unittest.main()
