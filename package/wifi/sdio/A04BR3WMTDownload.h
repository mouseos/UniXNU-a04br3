/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * MT6630 SDIO ROM-patch startup, caller serialized, input verified/owned.
 * wmt_ic_6630.c: each patch address/fragment ACKs -> init_table_3 reset;
 * after loop another init_table_3 reset -> coex -> clock -> merge -> FM strap.
 * UART init_table_4/5/full-mode switch is explicitly NOT on the SDIO path.
 * No automatic retries, host reset, poison clearing or synthesized credit.
 */
#ifndef A04BR3_WMT_DOWNLOAD_H
#define A04BR3_WMT_DOWNLOAD_H
#include "A04BR3WMTFirmware.h"
enum { A04_WMT_DOWNLOAD_STATE=70,A04_WMT_DOWNLOAD_ACK=71,
 A04_WMT_DOWNLOAD_IDENTITY=72,A04_WMT_DOWNLOAD_CONFIG=73 };
enum { A04_WMT_DL_IDLE=0,A04_WMT_DL_ADDRESS=1,A04_WMT_DL_FRAGMENT=2,
 A04_WMT_DL_PATCH_RESET=3,A04_WMT_DL_FINAL_RESET=4,A04_WMT_DL_COEX=5,
 A04_WMT_DL_CLOCK=6,A04_WMT_DL_MERGE=7,A04_WMT_DL_STRAP=8,
 A04_WMT_DL_VERIFY=9,A04_WMT_DL_FUNCTION1=10,A04_WMT_DL_READY=11 };
struct a04_wmt_startup_options {
 unsigned coex_present,antenna_mode,co_clock,merge_interface,fm_strap,function1;
};
struct a04_wmt_download_state {
 unsigned attempted,phase,sequence,fragment,offset,commands,acked,reset_acks,parts_acked;
 unsigned stp_ready,function1_ready;uint32_t hardware_after,rom_after;int failed;
};
typedef void (*a04_wmt_download_progress)(void *,const struct a04_wmt_download_state *);
static inline int a04_wmt_download_fail(struct a04_sdio_transport *t,struct a04_wmt_channel *c,
 struct a04_wmt_download_state *s,int error){
 s->failed=error;return a04_wmt_channel_fail(t,c,error);
}
static inline int a04_wmt_startup_command(struct a04_sdio_transport *t,struct a04_wmt_channel *c,
 struct a04_wmt_download_state *s,const uint8_t *command,unsigned length,
 const uint8_t *expected,unsigned expected_length){
 s->commands++;
 int e=a04_wmt_channel_exchange(t,c,command,length);if(e)return a04_wmt_download_fail(t,c,s,e);
 if(c->event_length!=expected_length)return a04_wmt_download_fail(t,c,s,A04_WMT_DOWNLOAD_ACK);
 for(unsigned i=0;i<expected_length;i++)if(c->event[i]!=expected[i])return a04_wmt_download_fail(t,c,s,A04_WMT_DOWNLOAD_ACK);
 s->acked++;return 0;
}
static inline void a04_wmt_download_note(a04_wmt_download_progress f,void *ctx,const struct a04_wmt_download_state *s){if(f)f(ctx,s);}
static inline int a04_wmt_download_reset(struct a04_sdio_transport *t,struct a04_wmt_channel *c,
 struct a04_wmt_download_state *s){
 const uint8_t command[5]={1,7,1,0,4},event[5]={2,7,1,0,0};
 int e=a04_wmt_startup_command(t,c,s,command,5,event,5);if(e)return e;
 /* A WMT MCU reset is not a host/card reset. Native SDIO keeps mandatory
  * framing and HIF credits. Never call cold channel_init here. */
 if(c->pending_cost || c->free_bytes!=2080)return a04_wmt_download_fail(t,c,s,A04_WMT_CHANNEL_CREDIT);
 s->reset_acks++;return 0;
}
static inline int a04_wmt_download_run(struct a04_sdio_transport *t,struct a04_wmt_channel *c,
 const struct a04_wmt_firmware_set *set,const struct a04_wmt_identity *identity,
 const struct a04_wmt_startup_options *options,struct a04_wmt_download_state *s,
 a04_wmt_download_progress progress,void *ctx){
 if(!t || !c || !set || !identity || !options || !s)return A04_WMT_DOWNLOAD_STATE;
 if(s->attempted || s->failed || t->failed || c->failed)return A04_WMT_DOWNLOAD_STATE;
 if(!c->initialized || !set->count || set->count>A04_WMT_FW_MAX_COUNT)return A04_WMT_DOWNLOAD_STATE;
 if(options->coex_present>1 || options->antenna_mode>255 || options->co_clock>1 ||
    options->merge_interface>1 || (options->fm_strap!=1 && options->fm_strap!=2) || options->function1>1)return A04_WMT_DOWNLOAD_CONFIG;
 struct a04_wmt_revision revision;
 if(a04_wmt_revision_decode(identity,&revision))return A04_WMT_FW_REVISION;
 /* Input reader has already checked CRCs and release coherence. Still bound
  * all planner inputs before the first command to prevent a partial send. */
 for(unsigned i=0;i<set->count;i++){
  const struct a04_wmt_firmware_file *f=&set->files[i];
  if(f->count!=set->count || f->sequence!=i+1 || !f->header.body.data ||
     !f->header.body.length || f->header.body.length>A04_WMT_PATCH_MAX ||
     f->header.body.length-1>0xffffffffu-f->destination ||
     ((f->header.software^identity->rom)&255) || (f->header.hardware&0xf0)!=revision.major)return A04_WMT_FW_SET;
 }
 s->attempted=1;s->stp_ready=s->function1_ready=0;
 struct a04_wmt_patch_plan plan;uint8_t command[A04_WMT_PATCH_FRAGMENT+5];
 for(unsigned i=0;i<set->count;i++){
  s->sequence=i+1;s->fragment=s->offset=0;
  int e=a04_wmt_firmware_plan(set,i+1,&plan);if(e)return a04_wmt_download_fail(t,c,s,e);
  unsigned limit=(plan.length+999)/1000+2; /* two address commands + fragments */
  for(unsigned n=0;n<limit;n++){
   s->phase=plan.phase<2?A04_WMT_DL_ADDRESS:A04_WMT_DL_FRAGMENT;
   unsigned length=0;e=a04_wmt_patch_next(&plan,command,sizeof(command),&length);
   if(e)return a04_wmt_download_fail(t,c,s,A04_WMT_CHANNEL_CODEC+e);
   a04_wmt_download_note(progress,ctx,s);s->commands++;
   e=a04_wmt_channel_exchange(t,c,command,length);if(e)return a04_wmt_download_fail(t,c,s,e);
   e=a04_wmt_patch_accept(&plan,c->event,c->event_length);
   if(e)return a04_wmt_download_fail(t,c,s,A04_WMT_CHANNEL_CODEC+e);
   s->acked++;
   if(s->phase==A04_WMT_DL_FRAGMENT){s->fragment++;s->offset=plan.offset;}
  }
  if(plan.phase!=3 || plan.offset!=plan.length)return a04_wmt_download_fail(t,c,s,A04_WMT_DOWNLOAD_STATE);
  s->phase=A04_WMT_DL_PATCH_RESET;a04_wmt_download_note(progress,ctx,s);
  e=a04_wmt_download_reset(t,c,s);if(e)return e;
  s->parts_acked++;
 }
 /* Unconditional post-loop reset in mt6630_sw_init, in addition to per-part. */
 s->phase=A04_WMT_DL_FINAL_RESET;a04_wmt_download_note(progress,ctx,s);
 int e=a04_wmt_download_reset(t,c,s);if(e)return e;
 if(options->coex_present){
  s->phase=A04_WMT_DL_COEX;a04_wmt_download_note(progress,ctx,s);
  uint8_t coex[6]={1,0x10,2,0,1,(uint8_t)options->antenna_mode};
  const uint8_t event[5]={2,0x10,1,0,0};
  if((e=a04_wmt_startup_command(t,c,s,coex,6,event,5)))return e;
 }
 /* cfgExist==0 explicitly selects native firmware defaults; no made-up config.
  * co_clock0 is native initial state. Board-specific selection is caller input. */
 if(options->co_clock){
  s->phase=A04_WMT_DL_CLOCK;a04_wmt_download_note(progress,ctx,s);
  const uint8_t clock[6]={1,0x0a,2,0,8,3},event[5]={2,0x0a,1,0,0};
  if((e=a04_wmt_startup_command(t,c,s,clock,6,event,5)))return e;
 }
 if(options->merge_interface){
  s->phase=A04_WMT_DL_MERGE;a04_wmt_download_note(progress,ctx,s);
  const uint8_t merge[44]={1,8,0x28,0,1,1,0,3,
   0x6c,0x50,2,0x80,0,0,0x10,0x11,0,0,0xf0,0xff,
   0x70,0x50,2,0x80,1,0,0,0,0x0f,0,0,0,
   0,0x53,2,0x80,4,0,0,0,4,0,0,0};
  const uint8_t event[8]={2,8,4,0,0,0,0,3};
  if((e=a04_wmt_startup_command(t,c,s,merge,sizeof(merge),event,sizeof(event))))return e;
 }
 s->phase=A04_WMT_DL_STRAP;a04_wmt_download_note(progress,ctx,s);
 uint8_t strap[6]={1,5,2,0,2,(uint8_t)options->fm_strap};
 uint8_t strap_event[6]={2,5,2,0,0,(uint8_t)options->fm_strap};
 if((e=a04_wmt_startup_command(t,c,s,strap,6,strap_event,6)))return e;
 /* Extra read-only continuity checks after the native startup script. Success
  * confirms responding WMT+identity; it does not prove WLAN firmware execution. */
 s->phase=A04_WMT_DL_VERIFY;a04_wmt_download_note(progress,ctx,s);
 s->commands++;e=a04_wmt_read_version(t,c,0x80000000u,&s->hardware_after);if(e)return a04_wmt_download_fail(t,c,s,e);s->acked++;
 s->commands++;e=a04_wmt_read_version(t,c,0x80000004u,&s->rom_after);if(e)return a04_wmt_download_fail(t,c,s,e);s->acked++;
 if((s->hardware_after&0xffff)!=identity->hardware || (s->rom_after&0xffff)!=identity->rom)return a04_wmt_download_fail(t,c,s,A04_WMT_DOWNLOAD_IDENTITY);
 s->stp_ready=1; /* WMT_STP_CONF_RDY is a host flag, not a wire command. */
 if(options->function1){
  s->phase=A04_WMT_DL_FUNCTION1;a04_wmt_download_note(progress,ctx,s);
  /* Combo wmt_func_wifi_on -> HIF WIFI on -> client's WLAN probe, not generic
   * WMT FUNC_CTRL(type3). Here only SDIO fn1 enable/ready is implemented. */
  e=a04_sdio_enable(t,1);if(e)return a04_wmt_download_fail(t,c,s,e);
  s->function1_ready=1;
 }
 s->phase=A04_WMT_DL_READY;a04_wmt_download_note(progress,ctx,s);return 0;
}
#ifdef __cplusplus
extern "C" int a04br3_wmt_start_firmware(IOService *);
#endif
#endif
