# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Execute nonempty native records and request/iteration using real codec.
Backend fixture is explicitly a host mock, not observed RF/network success.
"""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class NativeScanResults(unittest.TestCase):
 def test_actual_codec_and_backend_dispatch(self):
  case=r'''
#include "A04BR3NativeScanDispatch.h"
#include <assert.h>
struct Mock {
 a04_scan_snapshot meta; a04_scan_observation records[2];
 int failure,begin_failure;unsigned begins,copies,active;
};
static int begin(void *c,unsigned active,uint64_t *generation){
 auto *m=(Mock *)c;m->begins++;m->active=active;
 if(m->begin_failure)return m->begin_failure;
 m->meta.generation++;m->meta.complete=0;m->meta.count=0;*generation=m->meta.generation;return 0;
}
static int snapshot(void *c,uint64_t expected,unsigned index,a04_scan_snapshot *meta,a04_scan_observation *record){
 auto *m=(Mock *)c;if(m->failure)return m->failure;
 if(expected && expected!=m->meta.generation)return ESTALE;
 *meta=m->meta;
 if(record){m->copies++;if(index>=m->meta.count)return ENOENT;*record=m->records[index];}
 return 0;
}
static void initialize(a04_scan_observation *r,uint64_t gen,unsigned index){
 *r={};r->generation=gen;r->seen_us=1000000;r->channel=6;r->band=1;r->beacon=100;
 r->capability=0x431;r->rcpi_valid=1;r->rcpi=100;r->from_probe=index;
 r->bssid[0]=2;r->bssid[5]=(uint8_t)(1+index);r->ssid_length=3;
 memcpy(r->ssid,"abc",3);r->rate_count=3;r->rates[0]=0x82;r->rates[1]=0x84;r->rates[2]=11;
 // Whole fixture IEs (not a selected RSN reconstruction): SSID, rates, vendor.
 uint8_t ies[]={0,3,'a','b','c',1,3,0x82,0x84,11,221,4,1,2,3,4};
 r->ie_length=sizeof(ies);memcpy(r->ies,ies,sizeof(ies));
}
static void outputInit(uint8_t *out){memset(out,0xaa,1166);out[1]=out[2]=out[3]=0;out[0]=1;out[0x8a]=0;out[0x8b]=4;}
int main(){
 Mock m={};m.meta={40,2234567,2,1};initialize(&m.records[0],40,0);initialize(&m.records[1],40,1);
 a04_scan_backend b={&m,begin,snapshot};a04_scan_result_session s={};a04_scan_completion_state event={};
 uint8_t p[5456]={},out[1166];p[0]=1;p[4]=3;p[0x34]=3;p[0x38]=1;
 assert(!a04_scan_dispatch_set(&s,&b,10,0,p,sizeof(p),&event) && !m.begins && s.generation==40);
 for(unsigned i=0;i<2;i++){
  outputInit(out);uint32_t bytes=1164;
  assert(!a04_scan_dispatch_get(&s,&b,11,0,out,&bytes) && s.cursor==i+1 && bytes==1164);
  assert(out[1164]==0xaa && out[1165]==0xaa);
  assert(a04_scan_result_u32(out)==1 && a04_scan_result_u32(out+4)==1);
  assert(a04_scan_result_u32(out+8)==6 && a04_scan_result_u32(out+12)==8);
  assert(out[0x10]==0 && out[0x11]==0 && out[0x12]==0 && out[0x14]==0); // no invented noise
  assert((int16_t)((unsigned)out[0x16]|((unsigned)out[0x17]<<8))==-60);
  assert(out[0x18]==100 && out[0x1a]==0x31 && out[0x1b]==4);
  assert(!memcmp(out+0x1c,m.records[i].bssid,6) && out[0x22]==0);
  assert(a04_scan_result_u32(out+0x24)==0 && a04_scan_result_u32(out+0x28)==0 && a04_scan_result_u32(out+0x2c)==0);
  assert(out[0x60]==3 && !memcmp(out+0x61,"abc",3));
  assert(a04_scan_result_u32(out+0x84)==1234 && out[0x88]==i);
  assert(out[0x8a]==m.records[i].ie_length && out[0x8b]==0);
  assert(!memcmp(out+0x8c,m.records[i].ies,m.records[i].ie_length));
 }
 for(unsigned i=0;i<4;i++){outputInit(out);uint8_t old[1166];memcpy(old,out,sizeof(out));uint32_t bytes=1164;
  assert(a04_scan_dispatch_get(&s,&b,11,0,out,&bytes)==EIO && s.cursor==2 && !memcmp(old,out,sizeof(out)));}
 assert(m.copies==2); // terminal GET never copies AP0 or walks past bounds
 p[0x34]=1;m.begin_failure=EOPNOTSUPP;
 assert(a04_scan_dispatch_set(&s,&b,10,0,p,sizeof(p),&event)==EOPNOTSUPP && s.generation==40 && m.active==1);
 m.begin_failure=0;assert(!a04_scan_dispatch_set(&s,&b,10,0,p,sizeof(p),&event) && s.generation==41 && !s.cursor);
 outputInit(out);uint32_t bytes=1164;
 assert(a04_scan_dispatch_get(&s,&b,11,0,out,&bytes)==EAGAIN && !s.cursor);
 m.meta.count=2;m.meta.complete=1;initialize(&m.records[0],41,0);initialize(&m.records[1],41,1);
 assert(!a04_scan_dispatch_get(&s,&b,11,0,out,&bytes) && s.cursor==1);
 m.meta.generation++;outputInit(out);
 assert(a04_scan_dispatch_get(&s,&b,11,0,out,&bytes)==ESTALE && s.cursor==1);
 p[0x34]=2;assert(!a04_scan_dispatch_set(&s,&b,10,0,p,sizeof(p),&event) && !m.active);
 unsigned calls=m.begins;p[0x34]=3;
 assert(a04_scan_dispatch_set(&s,&b,10,0,p,sizeof(p),&event)==EAGAIN && m.begins==calls);
 m.meta.complete=1;m.meta.count=0;assert(!a04_scan_dispatch_set(&s,&b,10,0,p,sizeof(p),&event));
 assert(a04_scan_dispatch_get(&s,&b,11,0,out,&bytes)==EIO);
 m.failure=ENETDOWN;assert(a04_scan_dispatch_get(&s,&b,11,0,out,&bytes)==ENETDOWN);m.failure=0;
 // Direct record validation is transactional: preserve all user bytes on error.
 a04_scan_snapshot meta={99,2234567,1,1};a04_scan_observation good={};initialize(&good,99,0);
 for(unsigned fault=0;fault<18;fault++){
  auto r=good;outputInit(out);uint8_t old[1166];memcpy(old,out,sizeof(out));bytes=1164;
  switch(fault){
   case 0:r.generation++;break;case 1:r.ie_length=1025;break;case 2:r.ie_truncated=1;break;
   case 3:r.ie_length--;break;case 4:r.ssid_length=33;break;case 5:r.rate_count=16;break;
   case 6:r.channel=0;break;case 7:r.band=2;break;case 8:r.rcpi_valid=0;break;
   case 9:r.rcpi=255;break;case 10:r.seen_us=meta.now_us+1;break;case 11:r.bssid[0]=1;break;
   case 12:r.from_probe=2;break;case 13:r.ie_length=0;break;case 14:r.capability=65536;break;
   case 15:r.ssid[0]='z';break;case 16:r.rates[0]=99;break;case 17:r.rate_count=2;break;
  }
  assert(a04_scan_result_encode(&meta,&r,out,&bytes)!=0 && !memcmp(out,old,sizeof(out)) && bytes==1164);
 }
 good.ie_length=1024; // Whole original SSID/rates plus 4 bounded vendor TLVs.
 for(unsigned i=16;i<1024;i+=252){good.ies[i]=221;good.ies[i+1]=250;memset(good.ies+i+2,0,250);}
 meta.now_us=UINT64_MAX;good.seen_us=0;outputInit(out);
 assert(!a04_scan_result_encode(&meta,&good,out,&bytes));assert(a04_scan_result_u32(out+0x84)==UINT32_MAX && out[1164]==0xaa);
 meta.complete=0;assert(!a04_scan_completion_due(&event,&meta));
 meta.complete=1;assert(a04_scan_completion_due(&event,&meta));assert(a04_scan_completion_reserve(&event,&meta));
 unsigned starts=m.begins;p[0x34]=1;
 assert(a04_scan_dispatch_set(&s,&b,10,0,p,sizeof(p),&event)==EBUSY && m.begins==starts);
 assert(!a04_scan_completion_reserve(&event,&meta));a04_scan_completion_posted(&event,meta.generation,EIO);
 assert(a04_scan_completion_due(&event,&meta));assert(a04_scan_completion_reserve(&event,&meta));a04_scan_completion_posted(&event,meta.generation,0);
 assert(!a04_scan_completion_due(&event,&meta));meta.generation++;assert(a04_scan_completion_due(&event,&meta));
}
'''
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'case.cpp').write_text(case)
   subprocess.run(['c++','-std=c++14','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(ROOT),str(p/'case.cpp'),'-o',str(p/'case')],check=True)
   subprocess.run([str(p/'case')],check=True)
 def test_donor_dictionary_defaults_and_strict_policy_before_backend(self):
  # REA ev_371521...: NULL options and non-NULL dictionary use different
  # default wire carriers. This is a semantic fixture, not native raw bytes.
  case=r'''
#include "A04BR3NativeScanDispatch.h"
#include <assert.h>
#include <stdio.h>
struct Backend {unsigned begins,snapshots,active;int error;uint64_t generation;};
static int begin(void *c,unsigned active,uint64_t *generation){
 auto *b=(Backend *)c;b->begins++;b->active=active;if(b->error)return b->error;
 *generation=++b->generation;return 0;
}
static int snapshot(void *c,uint64_t,unsigned,a04_scan_snapshot *out,a04_scan_observation *){
 auto *b=(Backend *)c;b->snapshots++;if(b->error)return b->error;
 *out={b->generation,1000,0,1};return 0;
}
static void request(uint8_t *p,unsigned type,uint32_t def){
 memset(p,0,5456);a04_scan_result_put32(p,1);a04_scan_result_put32(p+4,3);
 a04_scan_result_put32(p+0x34,type);a04_scan_result_put32(p+0x38,1);
 a04_scan_result_put32(p+0x1310,def);
}
int main(){
 uint8_t p[5456],old[5456];unsigned cases=0;
 for(unsigned type=1;type<=3;type++)for(unsigned dictionary=0;dictionary<2;dictionary++){
  request(p,type,dictionary?0x80000000u:0);memcpy(old,p,sizeof(p));unsigned mode=99;
  assert(!a04_scan_result_policy(0,p,sizeof(p),&mode)&&mode==type);
  assert(!memcmp(p,old,sizeof(p)));cases++;
  // Every bit outside dwell/rest hints, channel entries and scheduling flags
  // remains rejected; both original carriers retained.
  for(unsigned i=4;i<sizeof(p);i++)for(unsigned bit=0;bit<8;bit++){
   if(i>=0x34&&i<0x38)continue;
   if((i>=0x3c&&i<0x3e)||(i>=0x40&&i<0x44))continue;
   if(i>=0x48&&i<0x1308)continue; // channel entries: covered below
   if(i==0x1308&&(bit==1||bit==5||bit==6||bit==7))continue;
   if(i==0x44)continue; // nonzero count with zero entries: covered below
   p[i]^=1u<<bit;mode=99;int error=a04_scan_result_policy(0,p,sizeof(p),&mode);
   if(i==0x1313&&bit==7)assert(!error&&mode==type);
   else assert(error==EOPNOTSUPP&&mode==99);
   p[i]^=1u<<bit;cases++;
  }
  Backend backend={0,0,0,0,50};a04_scan_backend b={&backend,begin,snapshot};
  a04_scan_result_session session={};a04_scan_completion_state event={};
  assert(!a04_scan_dispatch_set(&session,&b,10,0,p,sizeof(p),&event));
  assert(session.generation==(type==3?50:51));
  assert(backend.begins==(type==3?0u:1u));
  if(type!=3)assert(backend.active==(type==1?1u:0u));
  // Every valid request still reaches the real callback error contract.
  backend.error=ENETDOWN;auto before=session;unsigned calls=backend.begins+backend.snapshots;
  assert(a04_scan_dispatch_set(&session,&b,10,0,p,sizeof(p),&event)==ENETDOWN);
  assert(!memcmp(&session,&before,sizeof(session))&&backend.begins+backend.snapshots==calls+1);
  // Unknown policy cannot reach callbacks or mutate accepted generation.
  p[0x1308]=8;calls=backend.begins+backend.snapshots;
  assert(a04_scan_dispatch_set(&session,&b,10,0,p,sizeof(p),&event)==EOPNOTSUPP);
  assert(!memcmp(&session,&before,sizeof(session))&&backend.begins+backend.snapshots==calls);
 }
 request(p,1,0x80000000u);unsigned mode=99;
 for(unsigned length=0;length<5456;length++){
  assert(a04_scan_result_policy(0,p,length,&mode)==EINVAL&&mode==99);
 }
 assert(a04_scan_result_policy(1,p,sizeof(p),&mode)==EOPNOTSUPP&&mode==99);
 assert(a04_scan_result_policy(0,nullptr,sizeof(p),&mode)==EINVAL&&mode==99);
 assert(a04_scan_result_policy(0,p,sizeof(p),nullptr)==EINVAL);
 puts("PASS donor default scan admission: active/passive/cached real callback/generation/error contract, no fabricated completion");
}
'''
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'case.cpp').write_text(case)
   subprocess.run(['c++','-std=c++14','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(ROOT),str(p/'case.cpp'),'-o',str(p/'case')],check=True)
   subprocess.run([str(p/'case')],check=True)
 def test_observed_setup_requests_and_channel_filter(self):
  # Exact shapes logged on hardware by kernel 911d14d (scan reject diagnostics).
  case=r'''
#include "A04BR3NativeScanDispatch.h"
#include <assert.h>
struct Mock {a04_scan_snapshot meta;a04_scan_observation records[3];unsigned begins;};
static int begin(void *c,unsigned,uint64_t *generation){
 auto *m=(Mock *)c;m->begins++;*generation=m->meta.generation;return 0;}
static int snapshot(void *c,uint64_t expected,unsigned index,a04_scan_snapshot *meta,a04_scan_observation *record){
 auto *m=(Mock *)c;if(expected && expected!=m->meta.generation)return ESTALE;*meta=m->meta;
 if(record){if(index>=m->meta.count)return ENOENT;*record=m->records[index];}return 0;}
static void base(uint8_t *p){memset(p,0,5456);a04_scan_result_put32(p,1);a04_scan_result_put32(p+4,3);
 a04_scan_result_put32(p+0x34,1);a04_scan_result_put32(p+0x38,1);a04_scan_result_put32(p+0x1310,0x80000000u);}
static void channel(uint8_t *p,unsigned i,unsigned ch,unsigned flags){uint8_t *c=p+0x48+12*i;
 a04_scan_result_put32(c,1);a04_scan_result_put32(c+4,ch);a04_scan_result_put32(c+8,flags);}
static void record(a04_scan_observation *r,unsigned ch,unsigned i){*r={};r->generation=7;r->seen_us=1;r->channel=ch;r->band=1;
 r->beacon=100;r->capability=1;r->rcpi_valid=1;r->rcpi=100;r->bssid[0]=2;r->bssid[5]=(uint8_t)(i+1);
 r->ssid_length=1;r->ssid[0]='x';r->rate_count=1;r->rates[0]=0x82;
 uint8_t ies[]={0,1,'x',1,1,0x82};r->ie_length=sizeof(ies);memcpy(r->ies,ies,sizeof(ies));}
int main(){
 static uint8_t p[5456];uint8_t out[1166];unsigned mode=0;
 base(p);p[0x1308]=0x20;assert(!a04_scan_result_policy(0,p,sizeof(p),&mode)&&mode==1); // LOW_LATENCY
 base(p);a04_scan_result_put32(p+0x44,13);for(unsigned i=0;i<13;i++)channel(p,i,i+1,0x0a);
 a04_scan_request q={};assert(!a04_scan_result_request(0,p,sizeof(p),&q)&&q.filtered&&q.channels==0x3ffe);
 p[0x1308]=0x20|0x2|0x40|0x80;assert(!a04_scan_result_request(0,p,sizeof(p),&q));
 p[0x1308]=0x08;assert(a04_scan_result_request(0,p,sizeof(p),&q)==EOPNOTSUPP); // OFFCHANNEL shapes results
 p[0x1308]=0;p[0x1309]=1;assert(a04_scan_result_request(0,p,sizeof(p),&q)==EOPNOTSUPP); // 6GHz followup
 p[0x1309]=0;a04_scan_result_put32(p+0x1310,(uint32_t)-70);assert(a04_scan_result_request(0,p,sizeof(p),&q)==EOPNOTSUPP);
 a04_scan_result_put32(p+0x1310,0x80000000u);
 channel(p,13,0,0);a04_scan_result_put32(p+0x48+12*13,1);assert(a04_scan_result_request(0,p,sizeof(p),&q)==EOPNOTSUPP); // entry past count
 memset(p+0x48+12*13,0,12);channel(p,0,1,0x2);assert(a04_scan_result_request(0,p,sizeof(p),&q)==EOPNOTSUPP); // no band
 channel(p,0,1,0x18);assert(a04_scan_result_request(0,p,sizeof(p),&q)==EOPNOTSUPP); // both bands
 channel(p,0,1,0x200a);assert(a04_scan_result_request(0,p,sizeof(p),&q)==EOPNOTSUPP); // unknown flag
 channel(p,0,15,0x0a);assert(a04_scan_result_request(0,p,sizeof(p),&q)==EOPNOTSUPP);
 channel(p,0,1,0x0a);a04_scan_result_put32(p+0x44,401);assert(a04_scan_result_request(0,p,sizeof(p),&q)==EOPNOTSUPP);
 // Request channels 1 and 6 only (plus 5GHz 36): results on 11 are skipped.
 base(p);a04_scan_result_put32(p+0x44,3);channel(p,0,1,0x0a);channel(p,1,6,0x0a);channel(p,2,36,0x12);
 Mock m={};m.meta={7,1000,3,1};record(&m.records[0],11,0);record(&m.records[1],6,1);record(&m.records[2],1,2);
 a04_scan_backend b={&m,begin,snapshot};a04_scan_result_session s={};a04_scan_completion_state ev={};
 assert(!a04_scan_dispatch_set(&s,&b,10,0,p,sizeof(p),&ev)&&s.filtered&&s.channels==((1u<<1)|(1u<<6)));
 unsigned seen[2];for(unsigned i=0;i<2;i++){memset(out,0,sizeof(out));out[0]=1;out[0x8b]=4;uint32_t bytes=1164;
  assert(!a04_scan_dispatch_get(&s,&b,11,0,out,&bytes));seen[i]=a04_scan_result_u32(out+8);}
 assert(seen[0]==6&&seen[1]==1&&s.cursor==3);
 memset(out,0,sizeof(out));out[0]=1;out[0x8b]=4;uint32_t bytes=1164;assert(a04_scan_dispatch_get(&s,&b,11,0,out,&bytes)==EIO);
 // Unfiltered request returns all three.
 base(p);s={};assert(!a04_scan_dispatch_set(&s,&b,10,0,p,sizeof(p),&ev)&&!s.filtered);
 // Hardware CoreWiFi caller header: version0, ie_len1024; output written as version1.
 for(unsigned i=0;i<3;i++){memset(out,0,sizeof(out));out[0x8b]=4;bytes=1164;
  assert(!a04_scan_dispatch_get(&s,&b,11,0,out,&bytes)&&a04_scan_result_u32(out)==1);}
 // A record whose IEs disagree with its metadata is skipped, others returned.
 m.records[1].rate_count=2;base(p);s={};assert(!a04_scan_dispatch_set(&s,&b,10,0,p,sizeof(p),&ev));
 unsigned got=0;for(;;){memset(out,0,sizeof(out));out[0]=1;out[0x8b]=4;bytes=1164;
  int e=a04_scan_dispatch_get(&s,&b,11,0,out,&bytes);if(e){assert(e==EIO);break;}got++;}
 assert(got==2&&s.skipped==1&&s.skip_why==8);
 // Header mismatch is still a caller error, not a skip.
 memset(out,0,sizeof(out));out[0]=2;out[0x8b]=4;bytes=1164;s={};assert(!a04_scan_dispatch_set(&s,&b,10,0,p,sizeof(p),&ev));
 assert(a04_scan_dispatch_get(&s,&b,11,0,out,&bytes)==EINVAL&&!s.skipped);
 return 0;
}
'''
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'case.cpp').write_text(case)
   subprocess.run(['c++','-std=c++14','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(ROOT),str(p/'case.cpp'),'-o',str(p/'case')],check=True)
   subprocess.run([str(p/'case')],check=True)
if __name__=='__main__':unittest.main()
