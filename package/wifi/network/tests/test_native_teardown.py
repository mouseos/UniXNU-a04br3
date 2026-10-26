# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Real open deauth/TX_DONE + firmware teardown, no synthesized completions."""
from pathlib import Path
import subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
KERNEL=(ROOT.parent/'host-fixture')
sys.path.insert(0,str(KERNEL/'build-support/tests'))
from test_wlan_backend import SETUP
CASE=r'''
#include "A04BR3WLANRuntime.h"
#include "A04BR3WLANJoinAdmission.h"
#include "A04BR3WLANCurrentNetwork.h"
#include <strings.h>
static unsigned used;static uint8_t packets[128][2048];static unsigned sizes[128];
static int capture(void *p,uint32_t arg,void *buf,unsigned n){
 if((arg>>31) && ((arg>>9)&0x1ffff)==0x34){
  unsigned size=a04_wmt_le16((uint8_t *)buf);assert(size<=2048 && used<128);
  memcpy(packets[used],buf,size);sizes[used++]=size;
  // Independently emulate actual returned command pages; TX success alone
  // is not a firmware ACK, deauth completion is injected separately below.
  auto *c=(card *)p;c->cpu+=(size+127)/128;c->ffa+=(size+127)/128;
 }
 return transfer(p,arg,buf,n);
}
static bool locked;static unsigned scheduled;
static void IOLockLock(void *){assert(!locked);locked=true;}
static void IOLockUnlock(void *){assert(locked);locked=false;}
class A04BR3SDIO {
public:
 void *transportLock=nullptr;bool liveStopping=false,networkInitialized=true;
 a04_wlan_host *wlanHost=nullptr;
 struct Live {a04_wlan_runtime runtime;struct {unsigned bytes;uint8_t data[1514];} queue[8];unsigned head,count;};
 Live storage={};Live *live=&storage;
 static uint64_t liveNow(){assert(locked);return 2000000;}
 void scheduleLive(){assert(locked);scheduled++;}
PRODUCTION
};
static void prepare(A04BR3SDIO *s,card *c,a04_sdio_io *io,a04_sdio_transport *t,a04_wlan_host *h,unsigned phase){
 setup(c,io,t);t->transfer=capture;assert(!a04_wlan_host_open(h,t));h->ready=1;assert(!a04_wlan_runtime_begin(h));
 s->storage={};s->live=&s->storage;s->wlanHost=h;s->networkInitialized=true;s->liveStopping=false;
 auto &r=s->live->runtime;r.begun=r.configured=r.admin=1;r.scan_generation=7;r.scan_count=1;
 a04_wlan_local l;local_profile(&l);assert(!a04_wlan_station_setup(&r.station,h,&l));
 a04_wlan_ap a;ap_profile(&a);r.station.scan.complete=1;
 assert(!a04_wlan_station_select(&r.station,h,&a,0,44,1000,1000,1500000));
 r.station.join.phase=phase==5?4:phase;r.station.sta_active=phase==4;r.station.sta_pending=phase==5;
 if(phase>=3){r.station.channel_pending=0;r.station.grant_ms=1000;}
 if(phase==4){r.station.join.aid=42;r.station.deadline_us=0;}
 for(auto &q:s->live->queue){q.bytes=64;memset(q.data,0x5a,sizeof(q.data));}
 s->live->head=3;s->live->count=2;used=scheduled=0;
}
static void erased(A04BR3SDIO *s){
 assert(!s->live->count && !s->live->head);
 for(auto &q:s->live->queue){assert(!q.bytes);for(auto byte:q.data)assert(!byte);}
}
static void command_at(unsigned at,unsigned cid,unsigned n){
 assert(at<used && packets[at][4]==cid && packets[at][6]==1 && sizes[at]==8+n);
}
static void done(a04_wlan_runtime *r,a04_wlan_host *h,uint64_t now,unsigned status=0,unsigned wrong=0){
 uint8_t p[16]={};p[0]=(uint8_t)(r->teardown.pid+wrong);p[1]=(uint8_t)status;p[4]=(uint8_t)r->station.local.wlan;
 a04_wlan_event v={0x0f,0,16,p};int e=a04_wlan_runtime_event(r,h,&v,now);
 if(status || wrong || now>=2100000)assert(e && r->failed && h->failed);
 else assert(!e);
}
int main(){
 auto *s=new A04BR3SDIO;card c;a04_sdio_io io;a04_sdio_transport t;a04_wlan_host h;
 auto &r=s->live->runtime;
 // Pending channel: actual abort, PM abort, disconnected BSS, deactivate,
 // remove only this STA, fresh setup. No deauth/scan/join success fabricated.
 prepare(s,&c,&io,&t,&h,0);auto old=r.station.local;
 assert(!s->requestDisassociate() && !locked && scheduled==1);erased(s);
 assert(used==8 && !r.station.selected && r.station.configured && !r.teardown.pending && r.teardown.completed==1);
 command_at(0,0x1c,16);assert(packets[0][8]==old.bss && packets[0][9]==44 && packets[0][10]==1);
 for(unsigned i=11;i<24;i++)assert(!packets[0][i]);
 command_at(1,0x17,4);assert(packets[1][8]==old.bss);
 command_at(2,0x12,88);assert(packets[2][8]==old.bss && packets[2][9]==1);
 command_at(3,0x11,12);assert(packets[3][8]==old.bss && !packets[3][9]);
 for(unsigned i=12;i<18;i++)assert(!packets[3][i]);
 command_at(4,0x14,4);assert(!packets[4][8] && packets[4][9]==old.sta && packets[4][10]==old.bss && !packets[4][11]);
 assert(r.station.local.bss!=old.bss && r.station.local.sta!=old.sta && r.station.local.wlan!=old.wlan);
 assert(r.slots.bss&(1u<<old.bss));assert(!r.station.scan.complete && !r.scan_count && r.scan_generation==7);
 unsigned before=used;assert(s->requestDisassociate()==ENOTCONN && used==before);
 // Actual async old events cannot activate/grant replacement context.
 uint8_t eventBytes[16]={};eventBytes[0]=(uint8_t)old.bss;
 a04_wlan_event event={0x10,0,16,eventBytes};assert(!a04_wlan_runtime_event(&r,&h,&event,2000010));
 event.id=0x0c;event.length=8;eventBytes[7]=(uint8_t)old.bss;
 assert(!a04_wlan_runtime_event(&r,&h,&event,2000011) && !r.station.sta_active);
 // Associated peer: only deauth submission, preserve actual association until
 // TX_DONE. Both AP and local addresses/reason/PID/TX-status request are real.
 prepare(s,&c,&io,&t,&h,4);old=r.station.local;auto ap=r.station.ap;
 assert(!s->requestDisassociate() && used==1 && r.teardown.pending && r.station.sta_active);erased(s);
 assert(sizes[0]==54 && packets[0][5]==0xcc && packets[0][21]==2 && packets[0][20]==r.teardown.pid);
 assert(packets[0][28]==0xc0 && !memcmp(packets[0]+32,ap.mac,6) && !memcmp(packets[0]+38,old.mac,6));
 assert(!memcmp(packets[0]+44,ap.mac,6) && a04_wmt_le16(packets[0]+52)==3);
 before=used;assert(s->requestDisassociate()==EBUSY && used==before);
 uint8_t ethernet[14]={};IOLockLock(s->transportLock);
 assert(s->queuePacket(ethernet,sizeof(ethernet))==ENETDOWN && !s->live->count);
 IOLockUnlock(s->transportLock);
 auto *current=new a04_wlan_scan_record;memset(current,0xa5,sizeof(*current));
 assert(s->currentRecord(current)==EAGAIN && ((uint8_t *)current)[0]==0xa5);delete current;
 done(&r,&h,2000100);assert(!r.teardown.pending && r.teardown.completed==1 && !r.station.sta_active && !r.station.selected);
 assert(used==8 && r.station.local.bss!=old.bss);command_at(1,0x17,4);command_at(4,0x14,4);
 // Association response/STA activation still pending also waits for actual
 // deauth completion; late activation cannot advance the canceled state.
 for(unsigned phase=3;phase<=5;phase+=2){
  prepare(s,&c,&io,&t,&h,phase);assert(!s->requestDisassociate() && r.teardown.pending);
  event.id=0x0c;event.length=8;memcpy(eventBytes,r.station.ap.mac,6);
  eventBytes[6]=(uint8_t)r.station.local.sta;eventBytes[7]=(uint8_t)r.station.local.bss;
  assert(!a04_wlan_runtime_event(&r,&h,&event,2000010) && !r.station.sta_active);
  done(&r,&h,2000100);assert(!r.station.selected && !r.teardown.pending && used==9);
  command_at(1,0x1c,16); // release still-owned join channel after deauth
 }
 // Nonzero TX status, wrong PID, missing completion/timeout never reconnect.
 for(unsigned fault=1;fault<=3;fault++){
  prepare(s,&c,&io,&t,&h,4);old=r.station.local;assert(!s->requestDisassociate());
  done(&r,&h,fault==3?2100000:2000100,fault==1,fault==2);
  assert(r.station.local.bss==old.bss && r.teardown.pending && used==1);
 }
 // A missing event hits the actual runtime step deadline, not fake success.
 prepare(s,&c,&io,&t,&h,4);assert(!s->requestDisassociate());
 assert(a04_wlan_runtime_step(&r,&h,2100000,nullptr,nullptr)==A04_WLAN_TIMEOUT && r.failed && used==1);
 // A real successful deauth does not hide later command errors.
 for(unsigned at=1;at<=7;at++){
  prepare(s,&c,&io,&t,&h,4);assert(!s->requestDisassociate());c.fail_tx=c.txs+at;
  uint8_t p[16]={};p[0]=(uint8_t)r.teardown.pid;p[4]=(uint8_t)r.station.local.wlan;
  a04_wlan_event v={0x0f,0,16,p};
  assert(a04_wlan_runtime_event(&r,&h,&v,2000100) && r.failed && h.failed && !r.teardown.completed);
 }
 // Every firmware teardown/replacement send fault poisons transport and does
 // NOT claim completion. No repeated commands accepted after partial failure.
 for(unsigned at=1;at<=8;at++){
  prepare(s,&c,&io,&t,&h,0);c.fail_tx=c.txs+at;
  assert(s->requestDisassociate()==EIO && r.failed && h.failed && !r.teardown.completed);
  before=used;assert(s->requestDisassociate()==ENETDOWN && used==before);
 }
 // Secure/key queues are rejected intact: no key erasure or undocumented
 // firmware delete, no side effects. In-flight data and scan require retry.
 for(unsigned mode=0;mode<8;mode++){
  prepare(s,&c,&io,&t,&h,4);
  if(mode==0)r.station.secured=1;
  if(mode==1)r.security.enabled=1;
  if(mode==2)r.security.ptk_pending=1;
  if(mode==3)r.security.gtk_accepted=1;
  if(mode==4)r.security.pending_tx=2;
  if(mode==5)r.security.ptk_confirmed=1;
  if(mode==6)r.security.authorized=1;
  if(mode==7)r.security.m4_confirmed=1;
  assert(s->requestDisassociate()==EOPNOTSUPP && !used && !scheduled && s->live->count==2 && s->live->queue[0].data[0]==0x5a);
 }
 prepare(s,&c,&io,&t,&h,4);r.data.pending=1;assert(s->requestDisassociate()==EBUSY && !used);
 prepare(s,&c,&io,&t,&h,4);r.station.scan.pending=1;assert(s->requestDisassociate()==EBUSY && !used);
 prepare(s,&c,&io,&t,&h,0);r.slots.bss=15;assert(s->requestDisassociate()==ENOSPC && !used && r.station.selected);
 // A next explicit scan and generation-bound join use fresh resources.
 prepare(s,&c,&io,&t,&h,0);assert(!s->requestDisassociate());
 r.calibration_valid=r.calibration_sent=r.mac_valid=r.cap_received=1;
 assert(!a04_wlan_runtime_scan_mode(&r,&h,2000100,0) && r.station.scan.pending && r.scan_generation==8);
 assert(!r.station.selected && !r.station.sta_active);
 uint8_t scanned[4]={(uint8_t)r.station.scan.sequence,0,0,0};a04_wlan_event scanDone={0x0d,0,4,scanned};
 assert(!a04_wlan_runtime_event(&r,&h,&scanDone,2000300));
 ap_profile(&r.scan[0]);r.scan_count=1;r.scan_extra[0].generation=8;r.scan_extra[0].observed_us=2000200;
 a04_wlan_join_target target={};target.generation=8;target.index=0;target.ssid_length=4;
 target.channel=6;target.band=1;memcpy(target.bssid,r.scan[0].mac,6);memcpy(target.ssid,"test",4);
 assert(!a04_wlan_join_target_check(&r,&target,2000400));
 auto fresh=r.station.local;assert(!a04_wlan_station_select(&r.station,&h,&r.scan[0],0,45,1000,2000400,3000400));
 assert(r.station.channel_pending && !r.station.sta_active && h.tx[8]==fresh.bss);
 // Repeated teardown cannot silently recycle old BSS/WTBL/STA indices.
 // Conservative bounded lifetime exhausts explicitly instead of late-event reuse.
 for(unsigned i=0;i<2;i++){
  assert(!s->requestDisassociate());r.station.scan.complete=1;
  assert(!a04_wlan_station_select(&r.station,&h,&r.scan[0],0,46+i,1000,2000400,3000400));
 }
 before=used;assert(s->requestDisassociate()==ENOSPC && used==before && r.station.selected);
 delete s;
}
'''
class NativeTeardown(unittest.TestCase):
 def test_production_command_cancel_deauth_completion_and_failures(self):
  source=(ROOT.parent/'sdio/A04BR3SDIO.cpp').read_text()
  a=source.index(' int requestDisassociate(');b=source.index(' int installKey(',a)
  methods=source[a:b]
  for begin,end in [(' int queuePacket(',' static int backendTransmit('),(' int currentRecord(',' int requestJoinTarget(')]:
   a=source.index(begin);b=source.index(end,a);methods+=source[a:b]
  code='#include <stdint.h>\n#include <assert.h>\n#include <string.h>\n#include "A04BR3WMTProtocol.h"\n'+SETUP+CASE.replace('PRODUCTION',methods)
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory);(p/'case.cpp').write_text(code)
   subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(ROOT),'-I',str(ROOT.parent/'sdio'),str(p/'case.cpp'),'-o',str(p/'case')],check=True)
   subprocess.run([str(p/'case')],check=True)
if __name__=='__main__':unittest.main()
