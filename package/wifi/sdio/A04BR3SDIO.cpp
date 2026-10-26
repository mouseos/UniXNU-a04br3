/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * MT8167 MSDC2/MT6630 bring-up, based on board register definitions.
 * Deliberately no IO80211 service or connected state until data path exists.
 */
#include <IOKit/IOLib.h>
#include <IOKit/IOService.h>
#include <pexpert/pexpert.h>
#include "A04BR3SDIOPIO.h"
#include "A04BR3SDIOPads.h"
#include "A04BR3WMTDownload.h"
#include "A04BR3WMTBootFirmware.h"
#include "A04BR3WLANFirmware.h"
#include "A04BR3WLANBootFirmware.h"
#include "A04BR3WLANRuntime.h"
#include "A04BR3WLANPersonalRuntime.h"
#include "A04BR3WLANPSKBinding.h"
#include "A04BR3WLANBackendAPI.h"
#include "A04BR3WLANJoinAdmission.h"
#include "A04BR3WLANCurrentNetwork.h"
#include "A04BR3WLANNetworkProfile.h"
#include <kern/thread_call.h>
#include <kern/clock.h>
extern "C" {
#include <net/init.h>
#include <sys/kpi_mbuf.h>
#include <sys/errno.h>
int a04br3_wlan_read_calibration(void *,unsigned);
}
#include <libkern/crypto/sha2.h>
#include <libkern/c++/OSData.h>
#include "../network/A04BR3NativeRendezvous.h"
#include "../network/A04BR3StationPhase.h"
extern "C" vm_offset_t ml_io_map(vm_offset_t,vm_size_t);
class A04BR3SDIO : public IOService {
 OSDeclareDefaultStructors(A04BR3SDIO);
 vm_offset_t host,top,gpio;
 IOLock *transportLock=nullptr;
 struct a04_sdio_io io;
 struct a04_sdio_transport transport;
 bool transportReady=false;
 struct a04_wmt_channel channel;
 struct a04_wmt_identity identity={};
 bool identityReady=false;
 void *firmwareSnapshot=nullptr;
 unsigned firmwareBytes=0;
 void *wlanSnapshot=nullptr;
 unsigned wlanBytes=0;
 bool wlanAttempted=false;
 struct a04_wlan_host *wlanHost=nullptr;
 struct a04_wlan_firmware wlanFirmware={};
 struct a04_wlan_download_state wlanDownload={};
 struct a04_wmt_firmware_set firmwareSet={};
 struct a04_wmt_download_state downloadState={};
 struct a04_wmt_startup_options startupOptions={0,0,0,1,2,1};
 struct Live {
  struct a04_wlan_runtime runtime;
  struct a04_psk4way personalWork;uint8_t personalRX[1514];
  struct {unsigned bytes;uint8_t data[1514];} queue[8];
  unsigned head,count,attempted,readyNotified,notifiedEvents,notifiedScan;
 } *live=nullptr;
 thread_call_t liveCall=nullptr;
 bool liveStopping=false,networkInitialized=false;
 struct a04_wlan_native_client nativeClient={};
 uint64_t nativeEpoch=1;
 static A04BR3SDIO *netOwner;
 static uint64_t liveNow(){clock_sec_t s;clock_usec_t u;clock_get_system_microtime(&s,&u);return (uint64_t)s*1000000+u;}
 void scheduleLive(unsigned ms=5){
  if(liveCall && !liveStopping){uint64_t deadline;clock_interval_to_deadline(ms,1000000,&deadline);thread_call_enter_delayed(liveCall,deadline);}
 }
 static void networkInit(){
  A04BR3SDIO *s=__atomic_exchange_n(&netOwner,(A04BR3SDIO *)nullptr,__ATOMIC_ACQ_REL);
  if(!s)return;
  IOLockLock(s->transportLock);
  if(!s->liveStopping){s->networkInitialized=true;s->scheduleLive();}
  IOLockUnlock(s->transportLock);s->release();
 }
 void deferLive(){
  if(!wlanHost || !wlanDownload.ready || liveCall)return;
  live=(Live *)IOMalloc(sizeof(*live));if(!live){setProperty("WLANRuntimeError",ENOMEM,32);return;}
  bzero(live,sizeof(*live));
  liveCall=thread_call_allocate_with_options(liveThunk,this,THREAD_CALL_PRIORITY_KERNEL,THREAD_CALL_OPTIONS_ONCE);
  if(!liveCall){IOFree(live,sizeof(*live));live=nullptr;setProperty("WLANRuntimeError",ENOMEM,32);return;}
  /* The pending callback owns a retain; no context-free dangling singleton.
   * Only one physical SDIO owner may register. stop clears pending ownership. */
  retain();A04BR3SDIO *empty=nullptr;
  if(!__atomic_compare_exchange_n(&netOwner,&empty,this,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE)){
   release();setProperty("WLANRuntimeError",EBUSY,32);return;
  }
  int e=net_init_add(networkInit);
  if(e==EALREADY)networkInit();
  else if(e){A04BR3SDIO *expected=this;if(__atomic_compare_exchange_n(&netOwner,&expected,(A04BR3SDIO *)nullptr,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE))release();setProperty("WLANRuntimeError",e,32);}
  else IOLog("A04BR3 WLAN: runtime deferred until net_init; no early ifnet attach\n");
 }
 void stopLive(){
  if(!transportLock)return;
  IOLockLock(transportLock);++nativeEpoch;liveStopping=true;networkInitialized=false;
  if(live){live->runtime.admin=0;live->runtime.security.authorized=0;live->runtime.station.sta_active=0;live->count=0;
   if(live->runtime.personal_admitted)a04_psk_fail(&live->runtime.personal,A04_WLAN_STATE);
   a04_psk_prepared_clear(&live->runtime.credential);}
  IOLockUnlock(transportLock);
  A04BR3SDIO *expected=this;
  if(__atomic_compare_exchange_n(&netOwner,&expected,(A04BR3SDIO *)nullptr,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE))release();
  if(liveCall){thread_call_cancel_wait(liveCall);thread_call_free(liveCall);liveCall=nullptr;}
  /* Rendezvous owns controller/DLIL teardown. A failed unbind preserves its
   * owner and acquired provider context; never free a live BSD backend. */
  if(nativeOwner){int e=a04br3_network_unbind(this,&nativeOwner);setProperty("WLANNativeUnbindError",(unsigned)e,32);}
  else unbindNative();
 }
 static void liveThunk(thread_call_param_t p,thread_call_param_t){((A04BR3SDIO *)p)->pumpLive();}
 void pumpLive(){
  IOLockLock(transportLock);
  if(liveStopping || !networkInitialized || !live || !wlanHost){IOLockUnlock(transportLock);return;}
  struct a04_wlan_runtime *r=&live->runtime;int e=0;
  if(!live->attempted){
   live->attempted=1;
   /* root_device/MSDC probe precedes net_init. This is an automatic per-device
    * runtime read, never a staged backup asset or a generated fallback MAC. */
   IOLockUnlock(transportLock);
   e=a04br3_wlan_read_calibration(r->calibration,512);
   IOLockLock(transportLock);
   if(liveStopping){IOLockUnlock(transportLock);return;}
   setProperty("WLANCalibrationReadError",(unsigned)e,32);
   if(!e)e=a04_wlan_runtime_calibration(r,r->calibration,512);
   if(!e)e=a04_wlan_live_begin(r,wlanHost,liveNow());
   if(e)a04_wlan_runtime_fail(r,wlanHost,e);
   IOLog("A04BR3 WLAN: runtime board read/query=%d after net_init (no automatic retry)\n",e);
  }
  struct a04_wlan_native_client client=nativeClient;if(client.owner)client.owner->retain();
  unsigned changed=0,ready=0;uint64_t authenticatedGeneration=0;
  for(unsigned step=0;!r->failed && step<8;step++){
   uint64_t now=liveNow();e=a04_wlan_runtime_step(r,wlanHost,now,r->personal_admitted?a04_psk_verify:client.verify,r->personal_admitted?(void *)&r->personal:client.context);
   bool empty=(e==A04_WLAN_PENDING);if(empty)e=0;
   if(!e && !r->teardown.pending && r->station.selected && r->station.grant_ms && !r->station.channel_pending && r->station.join.phase==0)
    e=a04_wlan_station_auth(&r->station,wlanHost,now);
   if(!e && !r->teardown.pending && r->station.join.phase==2){unsigned bytes=0;const uint8_t *rsn=r->station.secured?a04_wlan_rsn_selected(&bytes):nullptr;
    e=a04_wlan_station_assoc(&r->station,wlanHost,r->station.ap.capability,10,rsn,bytes,now);}
   if(!e && !r->teardown.pending && r->station.sta_active && r->station.secured && !r->security.enabled){
    if(r->personal_admitted){auto ops=a04_psk_native_crypto();e=a04_wlan_personal_start(r,&ops,now);}
    else e=a04_wlan_security_begin(&r->security,&r->station,now,now+10000000);
   }
   if(!e && r->personal_admitted && r->delivery_kind==A04_WLAN_DELIVERY_EAPOL){
    uint64_t epoch=r->association_generation,consumer=nativeEpoch;unsigned bytes=r->delivery_length;
    if(bytes<14 || bytes>sizeof(live->personalRX))e=A04_WLAN_FORMAT;
    else{
     bcopy(r->delivery,live->personalRX,bytes);live->personalWork=r->personal;r->personal_busy=1;
     IOLockUnlock(transportLock);
     // CSPRNG and all actual handshake crypto run outside SDIO/spin locks.
     int crypto=a04_psk_rx(&live->personalWork,live->personalRX+14,bytes-14,now);
     IOLockLock(transportLock);r->personal_busy=0;now=liveNow();
     if(!liveStopping && a04_wlan_personal_commit_allowed(r,epoch,consumer,nativeEpoch,nativeClient.owner && nativeClient.owner==client.owner)){
      if(crypto!=A04_WLAN_PENDING){
       if(!crypto)crypto=a04_psk_live(&live->personalWork,now);
       e=crypto?crypto:a04_wlan_personal_observed(r,&live->personalWork);
      }
     }
     a04_wlan_secret_clear(&live->personalWork,sizeof(live->personalWork));
     a04_wlan_secret_clear(live->personalRX,sizeof(live->personalRX));
    }
    r->delivery_kind=r->delivery_length=0;r->delivery=nullptr;
    if(liveStopping)break;
   }
   if(!e && !r->teardown.pending)e=a04_wlan_personal_drive(r,wlanHost,now);
   if(!e && r->personal_admitted && a04_psk_authenticated(&r->personal) && !r->security.authorized)
    e=a04_wlan_security_authorize(&r->security,0,now);
   if(!e && r->security.authorized && r->personal_admitted && !r->personal.completion_reported){
    authenticatedGeneration=r->association_generation;
   }
   if(!e && !r->teardown.pending && live->count && !r->data.pending){
    unsigned at=live->head;uint64_t until=0;
    unsigned eapol=a04_wlan_be16(live->queue[at].data+12)==0x888e;
    e=a04_wlan_data_deadline(&r->security,eapol,now,&until);
    if(!e)e=a04_wlan_ethernet_send(wlanHost,&r->station,&r->security,&r->data,live->queue[at].data,live->queue[at].bytes,r->personal_admitted?a04_psk_verify:client.verify,r->personal_admitted?(void *)&r->personal:client.context,now,until);
    a04_wlan_secret_clear(live->queue[at].data,live->queue[at].bytes);live->head=(at+1)%8;live->count--;
   }
   if(e)a04_wlan_runtime_fail(r,wlanHost,e);
   if(r->delivery_kind && !r->failed && client.packet){
    unsigned kind=r->delivery_kind,bytes=r->delivery_length;const void *data=r->delivery;
    /* ONCE prevents overlapping execution while packet consumer copies RX. */
    IOLockUnlock(transportLock);client.packet(client.context,kind,data,bytes);IOLockLock(transportLock);
    if(liveStopping)break;
   }
   if(empty)break; /* still advance one pending TX/security operation */
  }
  if(r->configured && !live->readyNotified && client.ready && !r->failed){live->readyNotified=1;ready=1;}
  if(live->notifiedEvents!=r->events || live->notifiedScan!=r->scan_count || r->failed){changed=1;live->notifiedEvents=r->events;live->notifiedScan=r->scan_count;}
  if(r->configured && !getRuntimeAnnounced){getRuntimeAnnounced=true;
   IOLog("A04BR3 WLAN: runtime MAC valid=%u source=%u NIC=0x%x FW=0x%x boardQueued=%u passive scan seq=%u RFfail=%u BBfail=%u\n",r->mac_valid,r->mac_source,r->firmware_product,r->firmware_version,r->calibration_sent,r->scan_sequence,r->rf_cal_fail,r->bb_cal_fail);}
  if(r->station.scan.complete && !scanAnnounced){scanAnnounced=true;IOLog("A04BR3 WLAN: passive scan complete results=%u dropped=%u events=%u\n",r->scan_count,r->scan_dropped,r->events);}
  setProperty("WLANRuntimeError",(unsigned)r->failed,32);
  setProperty("WLANControlReady",(r->configured&&!r->failed)?kOSBooleanTrue:kOSBooleanFalse);
  setProperty("WLANScanComplete",r->station.scan.complete?kOSBooleanTrue:kOSBooleanFalse);
  setProperty("WLANScanResults",r->scan_count,32);
  if(r->failed){live->count=0;IOLog("A04BR3 WLAN: runtime stopped error=%d phase=%u events=%u cap=%u event=0x%x/%u/%u lastCMD=0x%x type=0x%x set=%u seq=%u bytes=%u WHISR=0x%x WASR=0x%x wasr-error=%d; no retry\n",r->failed,r->phase,r->events,r->cap_received,r->last_event_id,r->last_event_sequence,r->last_event_bytes,wlanHost->last_cid,wlanHost->last_cmd_type,wlanHost->last_cmd_set,wlanHost->last_cmd_sequence,wlanHost->last_cmd_bytes,wlanHost->status,wlanHost->wasr,wlanHost->wasr_error);}
  if(r->failed)for(unsigned i=0;i<r->event_trace_count;i++)IOLog("A04BR3 WLAN: event-trace[%u] id=0x%x seq=%u bytes=%u\n",i,r->event_trace[i].id,r->event_trace[i].sequence,r->event_trace[i].bytes);
  else if(!liveStopping)scheduleLive((r->cap_pending || r->station.scan.pending || r->station.selected || r->data.pending || live->count)?20:50);
  uint64_t completionConsumer=nativeEpoch;
  IOLockUnlock(transportLock);
  if(ready)client.ready(client.context,this);
  if(changed && client.changed)client.changed(client.context);
  if(authenticatedGeneration && client.authenticated && !client.authenticated(client.context,authenticatedGeneration)){
   IOLockLock(transportLock);
   if(!liveStopping && live && nativeEpoch==completionConsumer && nativeClient.owner==client.owner &&
      live->runtime.association_generation==authenticatedGeneration && live->runtime.security.authorized &&
      !live->runtime.personal.completion_reported)
    (void)a04_psk_completion_take(&live->runtime.personal);
   IOLockUnlock(transportLock);
  }
  if(client.owner)client.owner->release();
 }
 IOService *nativeOwner=nullptr;
 bool getRuntimeAnnounced=false,scanAnnounced=false;
 static int backendState(void *p,struct a04_network_state *out){
  if(!out)return EINVAL;
  A04BR3SDIO *s=(A04BR3SDIO *)p;IOLockLock(s->transportLock);bzero(out,sizeof(*out));
  if(s->live){const struct a04_wlan_runtime *r=&s->live->runtime;
   out->present=s->identityReady;out->firmware_running=s->wlanDownload.ready && s->wlanHost && !s->wlanHost->failed;
   out->control_ready=s->networkInitialized && !s->liveStopping && r->configured && !r->failed;
   out->radio_enabled=r->admin && out->control_ready;
   out->mac_valid=r->mac_valid;bcopy(r->mac,out->mac,6);
   a04_wlan_network_profile(r,out,s->nativeClient.packet!=nullptr);
  }IOLockUnlock(s->transportLock);return 0;
 }
 static int backendGet(void *p,uint32_t type,uint32_t value,void *buf,uint32_t *n){return ((A04BR3SDIO *)p)->nativeDispatch(false,type,value,buf,n);}
 static int backendSet(void *p,uint32_t type,uint32_t value,const void *buf,uint32_t n){return ((A04BR3SDIO *)p)->nativeDispatch(true,type,value,(void *)buf,&n);}
 int nativeDispatch(bool set,uint32_t type,uint32_t value,void *buf,uint32_t *n){
  IOLockLock(transportLock);struct a04_wlan_native_client c=nativeClient;if(c.owner)c.owner->retain();bool stopped=liveStopping;IOLockUnlock(transportLock);
  int e=stopped?ENXIO:(set?(c.set?c.set(c.context,type,value,buf,*n):EOPNOTSUPP):(c.get?c.get(c.context,type,value,buf,n):EOPNOTSUPP));
  if(c.owner)c.owner->release();
  return e;
 }
 static int backendAdmin(void *p,unsigned enabled){
  A04BR3SDIO *s=(A04BR3SDIO *)p;IOLockLock(s->transportLock);int e=0;
  if(!s->live || s->liveStopping || s->live->runtime.failed || !s->live->runtime.configured)e=ENXIO;
  else if(enabled!=s->live->runtime.admin)e=EOPNOTSUPP; /* no fabricated power transition */
  IOLockUnlock(s->transportLock);return e;
 }
 int queuePacket(const void *bytes,unsigned n){
  if(!live || liveStopping || live->runtime.failed || !live->runtime.admin || n<14 || n>1514)return ENXIO;
  if(live->count==8)return ENOBUFS;
  unsigned eapol=a04_wlan_be16((const uint8_t *)bytes+12)==0x888e;
  if(live->runtime.teardown.pending || !a04_wlan_port_allowed(&live->runtime.station,&live->runtime.security,eapol))return ENETDOWN;
  unsigned at=(live->head+live->count)%8;bcopy(bytes,live->queue[at].data,n);live->queue[at].bytes=n;live->count++;scheduleLive();return 0;
 }
 static int backendTransmit(void *p,void *packet){
  if(!packet)return EINVAL;
  A04BR3SDIO *s=(A04BR3SDIO *)p;mbuf_t m=(mbuf_t)packet;size_t n=mbuf_pkthdr_len(m);
  if(n<14 || n>1514)return EMSGSIZE;
  IOLockLock(s->transportLock);int e=ENXIO;
  if(s->live && !s->liveStopping && !s->live->runtime.failed && s->live->runtime.admin && !s->live->runtime.teardown.pending && s->live->count<8 && a04_wlan_port_allowed(&s->live->runtime.station,&s->live->runtime.security,0)){
   unsigned at=(s->live->head+s->live->count)%8;e=mbuf_copydata(m,0,n,s->live->queue[at].data);
   if(!e){s->live->queue[at].bytes=(unsigned)n;s->live->count++;s->scheduleLive();}
  }else if(s->live && s->live->count==8)e=ENOBUFS;
  IOLockUnlock(s->transportLock);if(!e)mbuf_freem(m);return e;
 }
 static int backendEAPOLTransmit(void *p,void *packet){
  if(!packet)return EINVAL;
  auto *s=(A04BR3SDIO *)p;mbuf_t m=(mbuf_t)packet;size_t n=mbuf_pkthdr_len(m);
  if(n<14 || n>1514)return EMSGSIZE;
  uint8_t eth[1514];int e=mbuf_copydata(m,0,n,eth);if(e)return e;
  IOLockLock(s->transportLock);
  if(!s->live || s->liveStopping)e=ENXIO;
  else if(s->live->runtime.personal_admitted)e=EOPNOTSUPP; // driver owns Personal4way; no competing native writer
  else if(a04_wlan_be16(eth+12)!=0x888e || !a04_wlan_equal(eth,s->live->runtime.station.ap.mac,6) ||
   !a04_wlan_equal(eth+6,s->live->runtime.station.local.mac,6))e=EINVAL;
  else e=s->queuePacket(eth,(unsigned)n);
  IOLockUnlock(s->transportLock);a04_wlan_secret_clear(eth,sizeof(eth));if(!e)mbuf_freem(m);return e;
 }
public:
 int acquireBackend(struct a04_network_backend *out){
  if(!out)return EINVAL;
  struct a04_network_state state;backendState(this,&state);if(!state.control_ready || !state.mac_valid)return ENXIO;
  retain();*out={this,backendState,backendGet,backendAdmin,backendTransmit,backendSet,backendEAPOLTransmit};return 0;
 }
 int bindNative(const struct a04_wlan_native_client *client){
  if(!client || !client->owner)return EINVAL;
  IOLockLock(transportLock);int e=0;
  if(liveStopping)e=ENXIO;else if(nativeClient.owner)e=EBUSY;else{++nativeEpoch;nativeClient=*client;client->owner->retain();if(live)live->readyNotified=0;scheduleLive();}
  IOLockUnlock(transportLock);return e;
 }
 int unbindNative(){
  IOLockLock(transportLock);++nativeEpoch;struct a04_wlan_native_client c=nativeClient;bzero(&nativeClient,sizeof(nativeClient));
  /* Removing a supplicant/consumer closes the host controlled port. */
  if(live){live->runtime.security.authorized=0;live->count=0;
   if(live->runtime.personal_admitted)a04_psk_fail(&live->runtime.personal,A04_WLAN_STATE);
   a04_psk_prepared_clear(&live->runtime.credential);}
  IOLockUnlock(transportLock);if(c.owner)c.owner->release();return 0;
 }
 int requestScanMode(unsigned mode,unsigned *acceptedGeneration){
  if(!acceptedGeneration)return EINVAL;
  *acceptedGeneration=0;
  if(mode>A04_WLAN_SCAN_ACTIVE)return EINVAL;
  IOLockLock(transportLock);int e=0;
  if(!live || liveStopping || live->runtime.failed)e=ENXIO;
  else if(live->runtime.teardown.pending || live->runtime.station.scan.pending || live->runtime.station.selected)e=EBUSY;
  else{int wire=a04_wlan_runtime_scan_mode(&live->runtime,wlanHost,liveNow(),mode);
   if(wire && wlanHost->failed)a04_wlan_runtime_fail(&live->runtime,wlanHost,wire);
   e=wire?(wire==A04_WLAN_UNSUPPORTED?EOPNOTSUPP:(wire==A04_WLAN_ARGUMENT?EINVAL:EIO)):0;
   if(!e){*acceptedGeneration=live->runtime.scan_generation;scanAnnounced=false;scheduleLive();}}
  IOLockUnlock(transportLock);return e;
 }
 int requestScan(){unsigned generation=0;return requestScanMode(A04_WLAN_SCAN_PASSIVE,&generation);}
 int scanCount(unsigned *count,unsigned *complete){if(!count || !complete)return EINVAL;IOLockLock(transportLock);int e=live?0:ENXIO;if(!e){*count=live->runtime.scan_count;*complete=live->runtime.station.scan.complete;}IOLockUnlock(transportLock);return e;}
 int scanCopy(unsigned i,struct a04_wlan_ap *out){if(!out)return EINVAL;IOLockLock(transportLock);int e=(!live || i>=live->runtime.scan_count)?ENOENT:0;if(!e)*out=live->runtime.scan[i];IOLockUnlock(transportLock);return e;}
 int scanSnapshot(struct a04_wlan_scan_info *out){
  if(!out)return EINVAL;
  IOLockLock(transportLock);int e=(!live || liveStopping)?ENXIO:0;
  if(!e){const struct a04_wlan_runtime *r=&live->runtime;bzero(out,sizeof(*out));
   out->generation=r->scan_generation;out->count=r->scan_count;out->complete=r->station.scan.complete;out->dropped=r->scan_dropped;
   out->started_us=r->scan_started_us;out->completed_us=r->scan_completed_us;out->error=r->failed;out->mode=r->scan_mode;}
  IOLockUnlock(transportLock);return e;
 }
 int scanCopyRecord(unsigned generation,unsigned index,struct a04_wlan_scan_record *out){
  if(!out || !generation)return EINVAL;
  IOLockLock(transportLock);int e=0;
  if(!live || liveStopping || live->runtime.failed)e=ENXIO;
  else{const struct a04_wlan_runtime *r=&live->runtime;
   if(generation!=r->scan_generation || !r->station.scan.complete || r->station.scan.pending)e=EAGAIN;
   else if(index>=r->scan_count)e=ENOENT;
   else{const struct a04_wlan_scan_extra *x=&r->scan_extra[index];
    if(x->generation!=generation || x->ie_bytes>A04_WLAN_SCAN_MAX_IE)e=EIO;
    else{bzero(out,sizeof(*out));out->ap=r->scan[index];out->observation.observed_us=x->observed_us;
     out->observation.generation=x->generation;out->observation.from_probe=x->from_probe;out->observation.ie_bytes=x->ie_bytes;
     bcopy(x->ies,out->observation.ies,x->ie_bytes);}
   }
  }IOLockUnlock(transportLock);return e;
 }
 int requestJoin(unsigned index,unsigned secure){
  IOLockLock(transportLock);int e=EINVAL;
  if(live && !liveStopping && index<live->runtime.scan_count && secure<=1 && (!secure || (nativeClient.verify && nativeClient.packet))){
   struct a04_wlan_runtime *r=&live->runtime;
   /* Explicit native request, source null-country domain only: channels1..13. */
   uint64_t now=liveNow();const struct a04_wlan_scan_extra *observed=&r->scan_extra[index];
   if(observed->generation!=r->scan_generation || now<observed->observed_us || now-observed->observed_us>=10000000){IOLockUnlock(transportLock);return EAGAIN;}
   r->station.local.active_channels=r->station.local.permitted_channels;
   e=a04_wlan_station_select(&r->station,wlanHost,&r->scan[index],secure,1,1000,now,now+1500000);
   if(!e)scheduleLive();
  }IOLockUnlock(transportLock);return e?EIO:0;
 }
 int currentRecord(struct a04_wlan_scan_record *out){
  if(!out)return EINVAL;
  IOLockLock(transportLock);int e=(!live || liveStopping)?ENXIO:(live->runtime.teardown.pending?EAGAIN:a04_wlan_current_record(&live->runtime,out));
  IOLockUnlock(transportLock);return e;
 }
 int requestJoinTarget(const struct a04_wlan_join_target *target){
  if(!target)return EINVAL;
  IOLockLock(transportLock);int e=ENXIO;
  if(live && !liveStopping && networkInitialized && wlanHost){
   struct a04_wlan_runtime *r=&live->runtime;uint64_t now=liveNow();
   e=a04_wlan_join_target_check(r,target,now);
   if(!e){
    r->station.local.active_channels=r->station.local.permitted_channels;
    int command=a04_wlan_station_select(&r->station,wlanHost,&r->scan[target->index],0,1,1000,now,now+1500000);
    e=command?EIO:0;if(!e){
     r->auth_lower=1;r->auth_upper=r->auth_upper_ext=0;
     r->association_generation=++r->association_counter;scheduleLive();}
   }
  }IOLockUnlock(transportLock);return e;
 }
 int requestPersonalJoin(const struct a04_wlan_join_target *target,struct a04_psk_prepared *prepared){
  if(!target || !prepared){a04_psk_prepared_clear(prepared);return EINVAL;}
  IOLockLock(transportLock);int e=ENXIO;
  if(live && !liveStopping && networkInitialized && wlanHost){
   auto *r=&live->runtime;e=a04_wlan_personal_target_check(r,target,liveNow());
   if(!e && (!prepared->ready || prepared->ssid_bytes!=target->ssid_length || !a04_psk_equal(prepared->ssid,target->ssid,target->ssid_length)))e=EINVAL;
   if(!e && r->association_counter>=0xffffffffffffULL)e=EOVERFLOW;
   if(!e){r->station.local.active_channels=r->station.local.permitted_channels;uint64_t now=liveNow();
    int wire=a04_wlan_station_select(&r->station,wlanHost,&r->scan[target->index],1,1,1000,now,now+1500000);
    if(wire)e=EIO;
    else{r->credential=*prepared;r->personal_admitted=1;r->auth_lower=1;r->auth_upper=8;r->auth_upper_ext=0;
     r->association_generation=++r->association_counter;scheduleLive();}
   }
  }
  a04_psk_prepared_clear(prepared);IOLockUnlock(transportLock);return e;
 }
 int sendEAPOL(const void *p,unsigned n){if(!p || n<14 || a04_wlan_be16((const uint8_t *)p+12)!=0x888e)return EINVAL;IOLockLock(transportLock);int e=queuePacket(p,n);IOLockUnlock(transportLock);return e;}
 int requestDisassociate(){
  IOLockLock(transportLock);int e=ENXIO;
  if(live && !liveStopping && networkInitialized && wlanHost){
   auto *r=&live->runtime;
   if(!r->configured || !r->admin || r->failed)e=ENETDOWN;
   else e=a04_wlan_teardown_begin(&r->teardown,&r->station,&r->security,&r->data,&r->slots,wlanHost,liveNow());
   if(!e || wlanHost->failed){
    /* Only admitted cancellation (or poisoned transport) owns queue disposal.
     * Never leave plaintext/security frames reachable by the next session. */
    for(unsigned i=0;i<8;i++){a04_wlan_secret_clear(live->queue[i].data,sizeof(live->queue[i].data));live->queue[i].bytes=0;}
    live->head=live->count=0;
   }
   if(wlanHost->failed)a04_wlan_runtime_fail(r,wlanHost,wlanHost->failed);
   if(!e){if(!r->teardown.pending){r->scan_count=0;r->scan_completed_us=0;}scheduleLive();}
  }IOLockUnlock(transportLock);return e;
 }
 int installKey(unsigned pairwise,unsigned id,const uint8_t *key,const uint8_t *rsc){IOLockLock(transportLock);int e=(!live || liveStopping)?ENXIO:(live->runtime.personal_admitted?EOPNOTSUPP:a04_wlan_security_install(&live->runtime.security,&live->runtime.station,wlanHost,pairwise,id,key,rsc,liveNow()));if(!e)scheduleLive();IOLockUnlock(transportLock);return e?EIO:0;}
 int authorize(int result){IOLockLock(transportLock);int e=(!live || liveStopping)?ENXIO:(live->runtime.personal_admitted?EOPNOTSUPP:a04_wlan_security_authorize(&live->runtime.security,result,liveNow()));if(!e)scheduleLive();IOLockUnlock(transportLock);return e?EIO:0;}
private:

 static uint32_t dataRead(void *p,unsigned off,unsigned width){
  A04BR3SDIO *s=(A04BR3SDIO *)p;
  if(width==4)return rd(s->host,off);
  uint32_t v=*(volatile uint8_t *)(s->host+off);__asm__ volatile("dmb oshld":::"memory");return v;
 }
 static void dataWrite(void *p,unsigned off,uint32_t v,unsigned width){
  A04BR3SDIO *s=(A04BR3SDIO *)p;
  if(width==4){wr(s->host,off,v);return;}
  *(volatile uint8_t *)(s->host+off)=(uint8_t)v;__asm__ volatile("dsb oshst":::"memory");
 }
 static void dataDelay(void *,unsigned us){IODelay(us);}
 static int transfer(void *p,uint32_t arg,void *buffer,unsigned length){
  struct a04_sdio_regs regs={p,dataRead,dataWrite,dataDelay};
  int e=a04_sdio_pio(&regs,arg,buffer,length);
  if(e)IOLog("A04BR3 SDIO: CMD53 error=%d arg=0x%x len=%u status=0x%x fifo=0x%x (transport stopped)\n",e,arg,length,rd(((A04BR3SDIO *)p)->host,0x0c),rd(((A04BR3SDIO *)p)->host,0x14));
  return e;
 }
 static uint32_t rd(vm_offset_t b,unsigned o){uint32_t v=*(volatile uint32_t *)(b+o);__asm__ volatile("dmb oshld":::"memory");return v;}
 static void wr(vm_offset_t b,unsigned o,uint32_t v){*(volatile uint32_t *)(b+o)=v;__asm__ volatile("dsb oshst":::"memory");}
 static bool wait(vm_offset_t b,unsigned o,uint32_t mask,uint32_t value){for(unsigned n=0;n<10000;n++){if((rd(b,o)&mask)==value)return true;IODelay(10);}return false;}
 void pin(unsigned p,unsigned mode){unsigned o=0x300+(p/5)*16,s=(p%5)*3;wr(gpio,o,(rd(gpio,o)&~(7u<<s))|(mode<<s));}
 void output(unsigned p,bool high){pin(p,0);wr(gpio,(high?0x104:0x108)+(p/32)*16,1u<<(p%32));wr(gpio,4+(p/32)*16,1u<<(p%32));}
 static void padWrite(void *p,unsigned off,uint32_t v){wr(((A04BR3SDIO *)p)->gpio,off,v);}
 static int command(void *p,unsigned op,uint32_t arg,unsigned response,uint32_t *out){
  A04BR3SDIO *s=(A04BR3SDIO *)p;
  if(!wait(s->host,0x3c,1,0))return 1;
  wr(s->host,0x0c,rd(s->host,0x0c));wr(s->host,0x38,arg);wr(s->host,0x34,op|(response<<7));
  for(unsigned n=0;n<10000;n++){
   uint32_t st=rd(s->host,0x0c)&0x700;
   if(st){wr(s->host,0x0c,st);if(st&0x600){IOLog("A04BR3 SDIO: CMD%u arg=0x%x interrupt=0x%x pins=0x%x cfg=0x%x\n",op,arg,st,rd(s->host,8),rd(s->host,0));return 2;}*out=rd(s->host,0x40);if(response==7&&!wait(s->host,0x3c,1,0))return 3;return 0;}
   IODelay(10);
  }
  return 4;
 }
 static void delay(void *,unsigned us){IOSleep((us+999)/1000);}
public:
 bool start(IOService *provider) APPLE_KEXT_OVERRIDE;
 void stop(IOService *provider) APPLE_KEXT_OVERRIDE {
  stopLive();
  if(transportLock){IOLockLock(transportLock);identityReady=false;transportReady=false;IOLockUnlock(transportLock);}
  setProperty("WMTTransportReady",kOSBooleanFalse);
  setProperty("WLANFirmwareReady",kOSBooleanFalse);
  IOService::stop(provider);
 }
 void free(void) APPLE_KEXT_OVERRIDE {
  if(live){a04_wlan_secret_clear(live,sizeof(*live));IOFree(live,sizeof(*live));}
  if(firmwareSnapshot)IOFree(firmwareSnapshot,firmwareBytes);
  if(wlanSnapshot)IOFree(wlanSnapshot,wlanBytes);
  if(wlanHost)IOFree(wlanHost,sizeof(*wlanHost));
  if(transportLock)IOLockFree(transportLock);
  IOService::free();
 }
 /* Explicit CPU-view staging interface. Parent owns delivery/reservation;
  * caller retains input until return. No PA translation, GPU asset aliasing,
  * firmware transmission, resets or automatic retries occur here. */
 int stageWMTFirmware(const void *bytes,unsigned length){
  if(!bytes || length<32 || length>A04_WMT_FW_MAX_BUNDLE)return A04_WMT_FW_ARGUMENT;
  if(!transportLock)return A04_WMT_FW_STATE;
  IOLockLock(transportLock);
  int e=0;
  if(!identityReady || !transportReady || transport.failed || firmwareSnapshot)e=A04_WMT_FW_STATE;
  void *snapshot=nullptr;
  if(!e){snapshot=IOMalloc(length);if(!snapshot)e=A04_WMT_FW_MEMORY;}
  if(!e){
   bcopy(bytes,snapshot,length);
   e=a04_wmt_firmware_bundle(&identity,snapshot,length,&firmwareSet);
  }
  if(!e){firmwareSnapshot=snapshot;firmwareBytes=length;}
  else if(snapshot){firmwareSet.count=0;IOFree(snapshot,length);}
  setProperty("WMTFirmwareStageError",(unsigned)e,32);
  setProperty("WMTFirmwareStaged",firmwareSnapshot?kOSBooleanTrue:kOSBooleanFalse);
  setProperty("WMTFirmwareRunning",kOSBooleanFalse);
  IOLog("A04BR3 WMT: firmware CPU stage=%d bundle=%u parts=%u HW=0x%x ROM=0x%x; no firmware sent\n",e,length,firmwareSet.count,identity.hardware,identity.rom);
  if(!e)for(unsigned i=0;i<firmwareSet.count;i++){
   const struct a04_wmt_firmware_file *f=&firmwareSet.files[i];
   IOLog("A04BR3 WMT: staged part=%u/%u body=%u destination=0x%x headerHW=0x%x SW=0x%x\n",f->sequence,f->count,f->header.body.length,f->destination,f->header.hardware,f->header.software);
  }
  IOLockUnlock(transportLock);return e;
 }
 static void downloadProgress(void *p,const struct a04_wmt_download_state *d){
  A04BR3SDIO *s=(A04BR3SDIO *)p;
  if(d->phase==A04_WMT_DL_FRAGMENT && (d->fragment&15))return;
  IOLog("A04BR3 WMT: download phase=%u part=%u/%u fragment=%u offset=%u commands=%u ACKs=%u resets=%u credit=%u\n",d->phase,d->sequence,s->firmwareSet.count,d->fragment,d->offset,d->commands,d->acked,d->reset_acks,s->channel.free_bytes);
 }
 int startWMTFirmware(){
  if(!transportLock)return A04_WMT_DOWNLOAD_STATE;
  IOLockLock(transportLock);
  if(downloadState.attempted){IOLockUnlock(transportLock);return A04_WMT_DOWNLOAD_STATE;}
  int e=(!identityReady || !transportReady || !firmwareSnapshot)?A04_WMT_DOWNLOAD_STATE:
   a04_wmt_download_run(&transport,&channel,&firmwareSet,&identity,&startupOptions,&downloadState,downloadProgress,this);
  if(transport.failed)transportReady=false;
  setProperty("WMTFirmwareDownloadError",(unsigned)e,32);
  setProperty("WMTFirmwareDownloadComplete",(!e && downloadState.phase==A04_WMT_DL_READY)?kOSBooleanTrue:kOSBooleanFalse);
  setProperty("WMTSTPReady",(!e && downloadState.stp_ready)?kOSBooleanTrue:kOSBooleanFalse);
  setProperty("WMTWiFiFunctionReady",(!e && downloadState.function1_ready)?kOSBooleanTrue:kOSBooleanFalse);
  setProperty("WMTTransportReady",transportReady?kOSBooleanTrue:kOSBooleanFalse);
  /* fn1 enabled is a WLAN probe boundary, not evidence of WLAN FW execution. */
  setProperty("WMTFirmwareRunning",kOSBooleanFalse);
  IOLog("A04BR3 WMT: download result=%d phase=%u part=%u fragment=%u offset=%u ACKs=%u/%u resets=%u parts=%u STP=%u fn1=%u CHISR=0x%x credit=%u pending=%u\n",e,downloadState.phase,downloadState.sequence,downloadState.fragment,downloadState.offset,downloadState.acked,downloadState.commands,downloadState.reset_acks,downloadState.parts_acked,downloadState.stp_ready,downloadState.function1_ready,channel.status,channel.free_bytes,channel.pending_cost);
  if(e && channel.event_length){
   IOLog("A04BR3 WMT: failing event");
   for(unsigned i=0;i<channel.event_length && i<32;i++)IOLog(" %02x",channel.event[i]);
   IOLog("\n");
  }
  IOLockUnlock(transportLock);return e;
 }
 /* Explicit immutable CPU copy; no firmware file probing or raw PA aliasing.
  * SHA256 pins the supplied stock asset, CRC validates its divided layout.
  * WMT ID8a12/FVR8a11 + completed patch/STP/fn1 are required independently. */
 int stageWLANFirmware(const void *bytes,unsigned length){
  if(!transportLock || !bytes || length!=237008)return A04_WLAN_ARGUMENT;
  IOLockLock(transportLock);
  int e=(!identityReady || !transportReady || transport.failed || wlanSnapshot || wlanAttempted ||
   downloadState.phase!=A04_WMT_DL_READY || !downloadState.function1_ready)?A04_WLAN_STATE:0;
  void *snapshot=nullptr;uint8_t digest[SHA256_DIGEST_LENGTH]={};
  if(!e){snapshot=IOMalloc(length);if(!snapshot)e=A04_WMT_FW_MEMORY;}
  if(!e){
   bcopy(bytes,snapshot,length);SHA256_CTX hash;
   SHA256_Init(&hash);SHA256_Update(&hash,snapshot,length);SHA256_Final(digest,&hash);
   e=a04_wlan_firmware_select(&identity,snapshot,length,digest,&wlanFirmware);
  }
  if(!e){wlanSnapshot=snapshot;wlanBytes=length;}
  else if(snapshot){wlanFirmware.count=0;IOFree(snapshot,length);}
  setProperty("WLANFirmwareStageError",(unsigned)e,32);
  setProperty("WLANFirmwareStaged",wlanSnapshot?kOSBooleanTrue:kOSBooleanFalse);
  IOLog("A04BR3 WLAN: CPU stage=%d bytes=%u sections=%u candidate=%u HW=0x%x ROM=0x%x\n",e,length,wlanFirmware.count,wlanFirmware.candidate,identity.hardware,identity.rom);
  if(!e)for(unsigned i=0;i<wlanFirmware.count;i++)
   IOLog("A04BR3 WLAN: section=%u address=0x%x bytes=%u\n",i+1,wlanFirmware.section[i].address,wlanFirmware.section[i].length);
  IOLockUnlock(transportLock);return e;
 }
 int startWLANFirmware(){
  if(!transportLock)return A04_WLAN_STATE;
  IOLockLock(transportLock);
  if(wlanAttempted || !wlanSnapshot || !transportReady || transport.failed ||
     downloadState.phase!=A04_WMT_DL_READY || !downloadState.function1_ready){IOLockUnlock(transportLock);return A04_WLAN_STATE;}
  wlanHost=(struct a04_wlan_host *)IOMalloc(sizeof(*wlanHost));
  if(!wlanHost){
   setProperty("WLANFirmwareDownloadError",A04_WMT_FW_MEMORY,32);
   IOLog("A04BR3 WLAN: host buffer allocation failed; no fn1 transaction attempted\n");
   IOLockUnlock(transportLock);return A04_WMT_FW_MEMORY;
  }
  bzero(wlanHost,sizeof(*wlanHost));
  wlanAttempted=true;
  int e=a04_wlan_host_open(wlanHost,&transport);
  IOLog("A04BR3 WLAN: fn1 prepare=%d WCIR=0x%x WHLPCR=0x%x own-polls=%u enabled=0x%x\n",e,wlanHost->wcir,wlanHost->chlpcr,wlanHost->own_polls,transport.enabled);
  if(!e)e=a04_wlan_download(wlanHost,&wlanFirmware,&wlanDownload);
  if(transport.failed)transportReady=false;
  bool ready=!e && wlanDownload.ready && wlanHost->ready;
  setProperty("WLANFirmwareDownloadError",(unsigned)e,32);
  setProperty("WLANFirmwareReady",ready?kOSBooleanTrue:kOSBooleanFalse);
  setProperty("WLANWCIR",wlanHost->wcir,32);
  setProperty("WMTTransportReady",transportReady?kOSBooleanTrue:kOSBooleanFalse);
  setProperty("NetworkDataPathAvailable",kOSBooleanFalse);
  IOLog("A04BR3 WLAN: download result=%d attempted=%u section=%u/%u offset=%u configs=%u packets=%u start=%u ready=%u WCIR=0x%x rxlen=%u CPU-free=%u used=%u transport-error=%d\n",e,wlanDownload.attempted,wlanDownload.section,wlanFirmware.count,wlanDownload.offset,wlanDownload.configured,wlanDownload.packets,wlanDownload.started,ready?1u:0u,wlanHost->wcir,wlanHost->rx_length,wlanHost->cpu_free,wlanHost->cpu_used,transport.failed);
  if(e && wlanHost->rx_length){
   IOLog("A04BR3 WLAN: failing init event");
   for(unsigned i=0;i<wlanHost->rx_length && i<32;i++)IOLog(" %02x",wlanHost->rx[i]);
   IOLog("\n");
  }
  IOLockUnlock(transportLock);return e;
 }
 void loadBootWLANFirmware(IOService *provider){
  setProperty("WLANFirmwareReady",kOSBooleanFalse);
  const void *view=nullptr;unsigned bytes=0;
  int boot=a04_wlan_boot_firmware_view(&view,&bytes),stage=A04_WLAN_STATE;
  setProperty("WLANBootFirmwareError",(unsigned)boot,32);
  IOLog("A04BR3 WLAN: boot view=%d bytes=%u\n",boot,bytes);
  if(!boot)stage=stageWLANFirmware(view,bytes);
  else if(boot==1){
   OSObject *object=provider->copyProperty("WLANFirmware-A04BR3");
   OSData *asset=OSDynamicCast(OSData,object);
   if(asset)stage=stageWLANFirmware(asset->getBytesNoCopy(),asset->getLength());
   else if(object)IOLog("A04BR3 WLAN: rejected non-OSData CPU firmware\n");
   else IOLog("A04BR3 WLAN: explicit asset absent; WLAN download deferred\n");
   if(object)object->release();
  }else IOLog("A04BR3 WLAN: invalid DT range/mapping; no fallback\n");
  if(!stage)startWLANFirmware();
 }
 int copyWMTFirmware(unsigned sequence,unsigned offset,void *bytes,unsigned length){
  if(!transportLock)return A04_WMT_FW_STATE;
  IOLockLock(transportLock);
  int e=identityReady && firmwareSnapshot?
   a04_wmt_firmware_read(&firmwareSet,sequence,offset,bytes,length):A04_WMT_FW_STATE;
  IOLockUnlock(transportLock);return e;
 }
 /* Synchronous kernel transport entry points. Never call from interrupt context.
  * Function1 is enabled on demand; WMT preparation only touches function2. */
 int enableFunction(unsigned fn){
  if(!transportLock)return A04_SDIO_POISONED;
  IOLockLock(transportLock);
  int e=transportReady?a04_sdio_enable(&transport,fn):A04_SDIO_POISONED;
  IOLockUnlock(transportLock);return e;
 }
 int transferFunction(unsigned fn,unsigned address,int write,int increment,void *buffer,unsigned length){
  if(!transportLock)return A04_SDIO_POISONED;
  IOLockLock(transportLock);
  /* fn2 belongs to the serialized WMT channel: raw CHISR/FIFO accesses
   * would consume status/data behind its credit and response bookkeeping. */
  int e=!transportReady?A04_SDIO_POISONED:
   ((fn==2 && channel.initialized) || (fn==1 && wlanHost && wlanHost->initialized))?A04_SDIO_ARGUMENT:
   a04_sdio_transfer(&transport,fn,address,write,increment,buffer,length);
  if(transport.failed){transportReady=false;setProperty("WMTTransportReady",kOSBooleanFalse);}
  IOLockUnlock(transportLock);return e;
 }
};
OSDefineMetaClassAndStructors(A04BR3SDIO,IOService);
bool A04BR3SDIO::start(IOService *provider)
{
 unsigned enabled=0;
 if(!PE_parse_boot_argn("a04br3-wifi",&enabled,sizeof(enabled))||enabled!=1||!IOService::start(provider))return false;
 static bool attempted=false;
 if(__atomic_exchange_n(&attempted,true,__ATOMIC_RELAXED))return false;
 top=ml_io_map(0x10000000,0x1000);gpio=ml_io_map(0x10005000,0x1000);if(!top||!gpio)return false;
 /* Select 26MHz with source gated. TOP2 bit30 is an inverted gate. */
 wr(top,0xa0,2);wr(top,0x40,rd(top,0x40)&~(7u<<3));wr(top,0x9c,1);wr(top,0x6c,1u<<30);wr(top,0xb0,2);
 host=ml_io_map(0x11170000,0x1000);if(!host)return false;
 IOLog("A04BR3 SDIO: MSDC2 inherited cfg=0x%x GPIO=0x%x/0x%x\n",rd(host,0),rd(gpio,0x100),rd(gpio,0x200));
 /* External combo PMU GPIO4 and reset GPIO7; retain board RTC source.
  * Do not touch UART or the separate eMMC controller. */
 output(4,false);output(7,false);IOSleep(10);output(4,true);IOSleep(30);output(7,true);IOSleep(30);
 for(unsigned p=68;p<=73;p++)pin(p,1);
 a04_sdio_pad_pulls(this,padWrite);
 wr(gpio,0x934,0x7e); /* MSDC2 input enables; board IES offsets are +4 set. */
 wr(host,0x10,0);wr(host,0,rd(host,0)|4);
 if(!wait(host,0,4,0)){IOLog("A04BR3 SDIO: reset timeout\n");return false;}
 wr(host,0,(rd(host,0)&~((3u<<20)|(0xfffu<<8)|2u))|9u|(17u<<8));
 if(!wait(host,0,0x80,0x80)){IOLog("A04BR3 SDIO: clock timeout cfg=0x%x\n",rd(host,0));return false;}
 wr(host,0x30,(rd(host,0x30)&~(3u<<16))|(1u<<19)|(0xffu<<24));
 IOSleep(10);
 io={this,command,delay};struct a04_sdio_card card={};int error=a04_sdio_identify(&io,&card);
 IOLog("A04BR3 SDIO: identify=%d OCR=0x%x RCA=0x%x functions=%u CCCR=0x%x vendor=0x%x device=0x%x\n",error,card.ocr,card.rca,card.functions,card.revision,card.vendor,card.device);
 if(error)return false;
 if(card.vendor!=0x037a || card.device!=0x6630 || card.functions<2)return false;
 transportLock=IOLockAlloc();if(!transportLock)return false;
 transport={&io,transfer,card.functions,0,0};
 struct a04_sdio_wmt_state wmt={};
 error=a04_sdio_wmt_prepare(&transport,&wmt);
 IOLog("A04BR3 SDIO: WMT fn2 prepare=%d CCIR=0x%x CHLPCR=0x%x->0x%x polls=%u enabled=0x%x\n",error,wmt.ccir,wmt.chlpcr_before,wmt.chlpcr_after,wmt.polls,transport.enabled);
 if(error)return false;
 /* Keep all WMT commands and destructive CHISR/CRDR reads under one lock.
  * Cold preparation above is the only point at which FIFO credits initialize. */
 IOLockLock(transportLock);
 error=a04_wmt_channel_init(&transport,&channel);
 IOLog("A04BR3 WMT: channel init=%d CHCR=0x%x (polled)\n",error,channel.chcr);
 const uint32_t addresses[2]={0x80000000u,0x80000004u};
 uint32_t versions[2]={0,0};
 for(unsigned i=0;!error && i<2;i++){
  error=a04_wmt_read_version(&transport,&channel,addresses[i],&versions[i]);
  IOLog("A04BR3 WMT: version addr=0x%x error=%d value=0x%x CHISR=0x%x polls=%u rxlen=%u packets=%u event=%u credit=%u pending=%u\n",addresses[i],error,versions[i],channel.status,channel.polls,channel.rx_length,channel.rx_packets,channel.event_length,channel.free_bytes,channel.pending_cost);
  if(channel.event_length){
   IOLog("A04BR3 WMT: event");
   for(unsigned k=0;k<channel.event_length && k<32;k++)IOLog(" %02x",channel.event[k]);
   IOLog("\n");
  }
 }
 transportReady=!error;
 if(!error){identity={0x6630,versions[0]&0xffff,versions[1]&0xffff};identityReady=true;}
 IOLockUnlock(transportLock);
 setProperty("WMTVersionQueryError",(unsigned)error,32);
 if(!error){
  setProperty("WMTHardwareVersion",versions[0],32);setProperty("WMTFirmwareROMVersion",versions[1],32);
  IOLog("A04BR3 WMT: identity HW=0x%x FW/ROM=0x%x; awaiting explicit firmware bundle\n",versions[0],versions[1]);
  struct a04_wmt_revision revision={};
  int re=a04_wmt_revision_decode(&identity,&revision);
  IOLog("A04BR3 WMT: revision decode=%d exact=%u table=0x%x tableECO=%u patch-family=%u (selection uses ROM-low8)\n",re,revision.exact,revision.table_hardware,revision.eco,revision.patch_family);
  setProperty("WMTFirmwareStaged",kOSBooleanFalse);
  setProperty("WMTFirmwareRunning",kOSBooleanFalse);
  /* Explicit options reflect vendor cfgExist/clock/build/FM/HIF choices.
   * Absent config: native cfgExist0 + co-clock disabled, MT6630 non-MT2601
   * merge build enabled, launcher FM_COMM2, fn1 host probe boundary. */
  OSObject *configObject=provider->copyProperty("WMTStartup-A04BR3");
  OSData *config=OSDynamicCast(OSData,configObject);
  bool configValid=!configObject;
  if(config && config->getLength()==24){
   const uint8_t *p=(const uint8_t *)config->getBytesNoCopy();
   startupOptions={a04_wmt_get32(p),a04_wmt_get32(p+4),a04_wmt_get32(p+8),a04_wmt_get32(p+12),a04_wmt_get32(p+16),a04_wmt_get32(p+20)};
   configValid=startupOptions.coex_present<=1 && startupOptions.antenna_mode<=255 && startupOptions.co_clock<=1 && startupOptions.merge_interface<=1 && (startupOptions.fm_strap==1 || startupOptions.fm_strap==2) && startupOptions.function1<=1;
  }
  if(configObject)configObject->release();
  const void *bootView=nullptr;unsigned bootBytes=0;
  int bootError=a04_wmt_boot_firmware_view(&bootView,&bootBytes);
  IOLog("A04BR3 WMT: boot bundle view=%d bytes=%u config-valid=%u\n",bootError,bootBytes,configValid?1u:0u);
  int stageError=A04_WMT_FW_STATE;
  if(!configValid){
   setProperty("WMTFirmwareStageError",A04_WMT_DOWNLOAD_CONFIG,32);
   IOLog("A04BR3 WMT: invalid startup options; firmware send blocked\n");
  }else if(bootError==A04_WMT_BOOT_OK){
   stageError=stageWMTFirmware(bootView,bootBytes);
  }else if(bootError==A04_WMT_BOOT_ABSENT){
   /* Preserve explicit CPU-stage API/property; never reinterpret a DT PA pair
    * as CPU bytes and never fall back after an invalid DT reservation. */
   OSObject *assetObject=provider->copyProperty("WMTFirmware-A04BR3");
   OSData *asset=OSDynamicCast(OSData,assetObject);
   if(asset)stageError=stageWMTFirmware(asset->getBytesNoCopy(),asset->getLength());
   else if(assetObject){
    setProperty("WMTFirmwareStageError",A04_WMT_FW_ARGUMENT,32);
    IOLog("A04BR3 WMT: rejected non-OSData CPU bundle\n");
   }else IOLog("A04BR3 WMT: firmware bundle absent; download deferred\n");
   if(assetObject)assetObject->release();
  }else{
   setProperty("WMTFirmwareStageError",A04_WMT_FW_RANGE,32);
   IOLog("A04BR3 WMT: boot bundle reservation/mapping rejected; no fallback\n");
  }
  if(!stageError){
   IOLog("A04BR3 WMT: SDIO mandatory STP, cfgExist=%u antenna=%u co-clock=%u merge=%u FM=%u; starting verified patch set\n",startupOptions.coex_present,startupOptions.antenna_mode,startupOptions.co_clock,startupOptions.merge_interface,startupOptions.fm_strap);
   if(!startWMTFirmware())loadBootWLANFirmware(provider);
  }
 }
 /* Readiness stays separate: WCIR ready alone does not publish a network. */
 setProperty("WMTTransportReady",transportReady?kOSBooleanTrue:kOSBooleanFalse);
 setProperty("WMTCCIR",wmt.ccir,32);setProperty("WMTCHLPCR",wmt.chlpcr_after,32);
 setProperty("SDIOVendor",card.vendor,32);setProperty("SDIODevice",card.device,32);setProperty("NetworkDataPathAvailable",kOSBooleanFalse);
 int nativeError=a04br3_network_bind(this,&nativeOwner);
 setProperty("WLANNativeBindError",(unsigned)nativeError,32);
 IOLog("A04BR3 WLAN: native rendezvous bind=%d; runtime ready remains deferred\n",nativeError);
 deferLive();registerService();return true;
}

extern "C" int a04br3_wmt_stage_firmware(IOService *service,const void *bytes,unsigned length)
{
 A04BR3SDIO *sdio=OSDynamicCast(A04BR3SDIO,service);
 return sdio?sdio->stageWMTFirmware(bytes,length):A04_WMT_FW_STATE;
}
extern "C" int a04br3_wmt_copy_firmware(IOService *service,unsigned sequence,unsigned offset,void *bytes,unsigned length)
{
 A04BR3SDIO *sdio=OSDynamicCast(A04BR3SDIO,service);
 return sdio?sdio->copyWMTFirmware(sequence,offset,bytes,length):A04_WMT_FW_STATE;
}

extern "C" int a04br3_wmt_start_firmware(IOService *service)
{
 A04BR3SDIO *sdio=OSDynamicCast(A04BR3SDIO,service);
 return sdio?sdio->startWMTFirmware():A04_WMT_DOWNLOAD_STATE;
}

extern "C" int a04br3_wlan_stage_firmware(IOService *service,const void *bytes,unsigned length)
{
 A04BR3SDIO *sdio=OSDynamicCast(A04BR3SDIO,service);
 return sdio?sdio->stageWLANFirmware(bytes,length):A04_WLAN_STATE;
}
extern "C" int a04br3_wlan_start_firmware(IOService *service)
{
 A04BR3SDIO *sdio=OSDynamicCast(A04BR3SDIO,service);
 return sdio?sdio->startWLANFirmware():A04_WLAN_STATE;
}

A04BR3SDIO *A04BR3SDIO::netOwner=nullptr;
extern "C" int a04br3_wlan_acquire_backend(IOService *p,struct a04_network_backend *b){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->acquireBackend(b):ENXIO;}
extern "C" void a04br3_wlan_release_backend(IOService *p){if(p)p->release();}
extern "C" int a04br3_wlan_bind_native(IOService *p,const struct a04_wlan_native_client *c){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->bindNative(c):ENXIO;}
extern "C" int a04br3_wlan_unbind_native(IOService *p){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->unbindNative():ENXIO;}
extern "C" int a04br3_wlan_scan(IOService *p){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->requestScan():ENXIO;}
extern "C" int a04br3_wlan_scan_count(IOService *p,unsigned *c,unsigned *done){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->scanCount(c,done):ENXIO;}
extern "C" int a04br3_wlan_scan_copy(IOService *p,unsigned i,struct a04_wlan_ap *a){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->scanCopy(i,a):ENXIO;}
extern "C" int a04br3_wlan_join(IOService *p,unsigned i,unsigned secure){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->requestJoin(i,secure):ENXIO;}
extern "C" int a04br3_wlan_current_record(IOService *p,struct a04_wlan_scan_record *out){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->currentRecord(out):ENXIO;}
extern "C" int a04br3_wlan_join_target(IOService *p,const struct a04_wlan_join_target *t){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->requestJoinTarget(t):ENXIO;}
extern "C" int a04br3_wlan_eapol(IOService *p,const void *b,unsigned n){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->sendEAPOL(b,n):ENXIO;}
extern "C" int a04br3_wlan_install_key(IOService *p,unsigned pair,unsigned id,const uint8_t k[16],const uint8_t r[8]){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->installKey(pair,id,k,r):ENXIO;}
extern "C" int a04br3_wlan_authorize(IOService *p,int result){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->authorize(result):ENXIO;}

extern "C" int a04br3_wlan_scan_snapshot(IOService *p,struct a04_wlan_scan_info *out){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->scanSnapshot(out):ENXIO;}
extern "C" int a04br3_wlan_scan_copy_record(IOService *p,unsigned generation,unsigned index,struct a04_wlan_scan_record *out){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->scanCopyRecord(generation,index,out):ENXIO;}

extern "C" int a04br3_wlan_scan_mode(IOService *p,unsigned mode,unsigned *acceptedGeneration){
 if(!acceptedGeneration)return EINVAL;
 *acceptedGeneration=0;auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->requestScanMode(mode,acceptedGeneration):ENXIO;
}

extern "C" int a04br3_wlan_disassociate(IOService *p){auto s=OSDynamicCast(A04BR3SDIO,p);return s?s->requestDisassociate():ENXIO;}

extern "C" int a04br3_wlan_join_personal(IOService *p,const struct a04_wlan_join_target *t,struct a04_psk_prepared *prepared){auto s=OSDynamicCast(A04BR3SDIO,p);if(!s){a04_psk_prepared_clear(prepared);return ENXIO;}return s->requestPersonalJoin(t,prepared);}

// Board archive starts this source-owned service after its metaclasses.
extern "C" IOService *a04br3_wifi_allocate(void) { return new A04BR3SDIO; }
