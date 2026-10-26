/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Pure MT6630 codecs/planner. Includer supplies uint8_t/uint16_t/uint32_t.
 * No I/O, allocation, firmware contents, or SDIO dependency.
 * Sources: common_main/core/{stp_core.c,wmt_ic_6630.c}, linux/{osal.c,
 * stp_sdio.c}, core/include/wmt_core.h from the board kernel.
 * All buffers must be disjoint; returned views borrow the caller's input.
 */
#ifndef A04BR3_WMT_PROTOCOL_H
#define A04BR3_WMT_PROTOCOL_H
enum { A04_WMT_OK=0, A04_WMT_MORE=1, A04_WMT_ARGUMENT=2,
 A04_WMT_LENGTH=3, A04_WMT_FORMAT=4, A04_WMT_CHECKSUM=5,
 A04_WMT_CRC=6, A04_WMT_MISMATCH=7, A04_WMT_STATUS=8,
 A04_WMT_BUSY=9, A04_WMT_STATE=10, A04_WMT_DONE=11 };
enum { A04_STP_SDIO=0, A04_STP_FULL=1, A04_STP_WMT=4,
 A04_STP_MAX_PAYLOAD=1999, A04_WMT_PATCH_FRAGMENT=1000,
 A04_WMT_PATCH_HEADER=28, A04_WMT_PATCH_MAX=16*1024*1024 };
struct a04_wmt_view {const uint8_t *data;unsigned length;};
static inline unsigned a04_wmt_le16(const uint8_t *p){return p[0]|((unsigned)p[1]<<8);}
static inline void a04_wmt_put16(uint8_t *p,unsigned v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static inline void a04_wmt_put32(uint8_t *p,uint32_t v){
 for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i));
}
/* osal_crc16: reflected polynomial 0xa001, initial 0, no final XOR.
 * SDIO mandatory mode does NOT use this CRC; full UART/BTIF mode does. */
static inline uint16_t a04_stp_crc16(const uint8_t *p,unsigned length){
 uint16_t crc=0;
 for(unsigned i=0;i<length;i++){
  crc^=p[i];for(unsigned b=0;b<8;b++)crc=(uint16_t)((crc>>1)^((crc&1)?0xa001:0));
 }
 return crc;
}
struct a04_stp_frame {
 struct a04_wmt_view payload;
 unsigned task,seq,ack,nak,consumed;
};
/* Full-mode zero-length packets are header-only ACKs. SDIO requires payload.
 * Codec deliberately caps both modes to the SDIO parser's <2000 limit.
 * Full mode here is framing only, not an ARQ/retransmission implementation. */
static inline int a04_stp_encode(unsigned mode,unsigned task,unsigned seq,unsigned ack,
 unsigned nak,const uint8_t *payload,unsigned length,uint8_t *out,unsigned capacity,unsigned *written){
 if(!written)return A04_WMT_ARGUMENT;
 *written=0;
 if(mode>1 || task>7 || seq>7 || ack>7 || nak>1 || !out || (!payload&&length))return A04_WMT_ARGUMENT;
 if(length>A04_STP_MAX_PAYLOAD || (!length && mode==A04_STP_SDIO))return A04_WMT_LENGTH;
 if(mode==A04_STP_SDIO && (seq||ack||nak))return A04_WMT_ARGUMENT;
 unsigned total=length?length+6:4;if(capacity<total)return A04_WMT_LENGTH;
 out[0]=(uint8_t)(0x80|(seq<<3)|ack);
 out[1]=(uint8_t)((nak<<7)|(task<<4)|(length>>8));out[2]=(uint8_t)length;
 out[3]=mode==A04_STP_SDIO?0:(uint8_t)(out[0]+out[1]+out[2]);
 for(unsigned i=0;i<length;i++)out[4+i]=payload[i];
 if(length){unsigned crc=mode==A04_STP_SDIO?0:a04_stp_crc16(payload,length);a04_wmt_put16(out+4+length,crc);}
 *written=total;return 0;
}
static inline int a04_stp_parse(unsigned mode,const uint8_t *p,unsigned available,struct a04_stp_frame *f){
 if(mode>1 || !p || !f)return A04_WMT_ARGUMENT;
 if(available<4)return A04_WMT_MORE;
 if((p[0]&0xc0)!=0x80 || (mode==A04_STP_SDIO && (p[0]!=0x80 || (p[1]&0x80))))return A04_WMT_FORMAT;
 unsigned length=((p[1]&15)<<8)|p[2];
 if(length>A04_STP_MAX_PAYLOAD || (!length && mode==A04_STP_SDIO))return A04_WMT_LENGTH;
 /* Strict SDIO validation: board RX only warns on nonzero reserved CRC.
  * We reject it to avoid advancing patch state with a malformed response. */
 if(p[3]!=(mode==A04_STP_SDIO?0:(uint8_t)(p[0]+p[1]+p[2])))return A04_WMT_CHECKSUM;
 unsigned total=length?length+6:4;if(available<total)return A04_WMT_MORE;
 if(length && a04_wmt_le16(p+4+length)!=(mode==A04_STP_SDIO?0:a04_stp_crc16(p+4,length)))return A04_WMT_CRC;
 f->payload.data=p+4;f->payload.length=length;f->task=(p[1]>>4)&7;
 f->seq=(p[0]>>3)&7;f->ack=p[0]&7;f->nak=p[1]>>7;f->consumed=total;return 0;
}
/* WMT length is LE16 excluding its four-byte header. It has no independent
 * checksum/CRC. Event status is the first payload byte; success is zero. */
static inline int a04_wmt_command(unsigned opcode,const uint8_t *parameters,unsigned length,
 uint8_t *out,unsigned capacity,unsigned *written){
 if(!written)return A04_WMT_ARGUMENT;
 *written=0;
 if(opcode>255 || !out || (!parameters&&length))return A04_WMT_ARGUMENT;
 if(length>A04_STP_MAX_PAYLOAD-4 || capacity<length+4)return A04_WMT_LENGTH;
 out[0]=1;out[1]=(uint8_t)opcode;a04_wmt_put16(out+2,length);
 for(unsigned i=0;i<length;i++)out[4+i]=parameters[i];
 *written=length+4;return 0;
}
static inline int a04_wmt_event(const uint8_t *command,unsigned command_length,
 const uint8_t *event,unsigned event_length,struct a04_wmt_view *reply){
 if(!command || !event || !reply)return A04_WMT_ARGUMENT;
 if(command_length<4 || event_length<4)return A04_WMT_MORE;
 if(command[0]!=1 || event[0]!=2)return A04_WMT_FORMAT;
 if(command_length>A04_STP_MAX_PAYLOAD || event_length>A04_STP_MAX_PAYLOAD ||
    a04_wmt_le16(command+2)!=command_length-4)return A04_WMT_LENGTH;
 unsigned len=a04_wmt_le16(event+2);
 if(len> A04_STP_MAX_PAYLOAD-4)return A04_WMT_LENGTH;
 if(len+4>event_length)return A04_WMT_MORE;
 if(len+4!=event_length || !len)return A04_WMT_LENGTH;
 if(event[1]!=command[1])return A04_WMT_MISMATCH;
 if(event[4])return A04_WMT_STATUS;
 reply->data=event+5;reply->length=len-1;return 0;
}
/* TX one WMT command -> STP -> SDIO hardware header, then zero padding.
 * Header counts logical bytes (including SDIO header), FIFO credits count
 * round4(logical), CMD53 bus count round512(FIFO) only when FIFO>512.
 * No padding bytes are included in WMT/STP length fields. */
struct a04_wmt_sdio_lengths {unsigned logical,fifo,bus;};
static inline int a04_wmt_sdio_encode(const uint8_t *command,unsigned length,
 uint8_t *out,unsigned capacity,struct a04_wmt_sdio_lengths *sizes){
 if(!command || !out || !sizes)return A04_WMT_ARGUMENT;
 if(length<4 || length>A04_STP_MAX_PAYLOAD || command[0]!=1 ||
    a04_wmt_le16(command+2)!=length-4)return A04_WMT_LENGTH;
 unsigned logical=length+10,fifo=(logical+3)&~3u;
 unsigned bus=fifo>512?(fifo+511)&~511u:fifo;
 if(capacity<bus)return A04_WMT_LENGTH;
 for(unsigned i=0;i<bus;i++)out[i]=0;
 unsigned written=0;int e=a04_stp_encode(A04_STP_SDIO,A04_STP_WMT,0,0,0,command,length,out+4,capacity-4,&written);
 if(e)return e;
 a04_wmt_put16(out,logical);
 sizes->logical=logical;sizes->fifo=fifo;sizes->bus=bus;return 0;
}
/* RX validates CHISR RX length against the SDIO header and walks every STP
 * frame, including four-byte inter-frame alignment. Bus padding outside the
 * logical packet and inter-frame pad values are ignored, as by board RX.
 * Unknown tasks remain visible; callers must select task4 before WMT matching.
 */
struct a04_wmt_sdio_cursor {const uint8_t *data;unsigned length,offset;};
static inline int a04_wmt_sdio_open(const uint8_t *p,unsigned bus_length,unsigned chisr_length,
 struct a04_wmt_sdio_cursor *c){
 if(!p || !c)return A04_WMT_ARGUMENT;
 if(bus_length<4)return A04_WMT_MORE;
 unsigned logical=a04_wmt_le16(p);
 if(logical<=4 || logical>2304 || logical!=chisr_length)return A04_WMT_LENGTH;
 if(bus_length<logical)return A04_WMT_MORE;
 if(bus_length>2560 || p[2] || p[3])return A04_WMT_FORMAT;
 c->data=p+4;c->length=logical-4;c->offset=0;return 0;
}
static inline int a04_wmt_sdio_next(struct a04_wmt_sdio_cursor *c,struct a04_stp_frame *f){
 if(!c || !f || !c->data || c->offset>c->length)return A04_WMT_ARGUMENT;
 unsigned remain=c->length-c->offset;
 if(!remain)return A04_WMT_DONE;
 int e=a04_stp_parse(A04_STP_SDIO,c->data+c->offset,remain,f);if(e)return e;
 unsigned next=c->offset+f->consumed,pad=(4-(f->consumed&3))&3;
 unsigned left=c->length-next;
 /* Last packet may have no alignment bytes or fewer than four pad bytes. */
 if(left<=pad)c->offset=c->length;
 else c->offset=next+pad;
 return 0;
}
/* Reassemble one STP frame from arbitrary host-read slices. Fixed 2005-byte
 * storage; never resynchronizes silently and never consumes a following frame.
 * After success or error reset explicitly. Caller handles SDIO header/alignment
 * separately and keeps the selected mode unchanged throughout a frame. */
struct a04_stp_stream {uint8_t bytes[A04_STP_MAX_PAYLOAD+6];unsigned used,target,mode;int failed;};
static inline void a04_stp_stream_reset(struct a04_stp_stream *s){s->used=0;s->target=4;s->mode=2;s->failed=0;}
static inline int a04_stp_stream_feed(struct a04_stp_stream *s,unsigned mode,const uint8_t *p,
 unsigned length,unsigned *consumed,struct a04_stp_frame *f){
 if(!s || !consumed || !f || (!p&&length) || mode>1)return A04_WMT_ARGUMENT;
 *consumed=0;
 if(s->failed)return s->failed;
 if(s->used && s->mode!=mode)return A04_WMT_ARGUMENT;
 s->mode=mode;
 if(s->target<4 || s->target>sizeof(s->bytes) || s->used>s->target)return A04_WMT_STATE;
 while(*consumed<length || s->used==s->target){
  if(s->used<s->target){s->bytes[s->used++]=p[(*consumed)++];continue;}
  int e=a04_stp_parse(mode,s->bytes,s->used,f);
  if(e==A04_WMT_MORE){
   unsigned n=((s->bytes[1]&15)<<8)|s->bytes[2];
   s->target=n?n+6:4;
   if(s->target<=s->used || s->target>sizeof(s->bytes)){s->failed=A04_WMT_LENGTH;return s->failed;}
  }else{if(e)s->failed=e;return e;}
 }
 return A04_WMT_MORE;
}
/* Legacy MT6630 WMT_PATCH (28 bytes), not the distinct wmt_rom_patch format.
 * Preserve opaque version bytes; hw/sw fields are BE16 (board prints swapped
 * native values). No magic, body checksum, authenticity, or compatibility
 * information is provided by this header. Policy/chip matching is caller work.
 */
struct a04_wmt_patch_info {
 uint8_t date[16],platform[4],patch_version[4];
 unsigned hardware,software;
 struct a04_wmt_view body;
};
static inline int a04_wmt_patch_parse(const uint8_t *blob,unsigned length,struct a04_wmt_patch_info *info){
 if(!blob || !info)return A04_WMT_ARGUMENT;
 if(length<=A04_WMT_PATCH_HEADER || length-A04_WMT_PATCH_HEADER>A04_WMT_PATCH_MAX)return A04_WMT_LENGTH;
 for(unsigned i=0;i<16;i++)info->date[i]=blob[i];
 for(unsigned i=0;i<4;i++){info->platform[i]=blob[16+i];info->patch_version[i]=blob[24+i];}
 info->hardware=((unsigned)blob[20]<<8)|blob[21];info->software=((unsigned)blob[22]<<8)|blob[23];
 info->body.data=blob+28;info->body.length=length-28;return 0;
}
/* One outstanding command only. ACKs contain no fragment/transaction ID: an
 * identical delayed/duplicate ACK cannot be distinguished on the wire. Caller
 * must drain/reset the transport on ambiguity; never retransmit automatically.
 * Body is borrowed and immutable until completion. 16MiB is a software safety
 * cap, not an asserted hardware limit. No command is transmitted by this API.
 */
struct a04_wmt_patch_plan {
 const uint8_t *body;unsigned length,offset,phase,pending,fragment;
 uint32_t address;int failed;
 uint8_t command[A04_WMT_PATCH_FRAGMENT+5];unsigned command_length;
};
static inline int a04_wmt_patch_begin(struct a04_wmt_patch_plan *s,const uint8_t *body,unsigned length,uint32_t address){
 if(!s || !body)return A04_WMT_ARGUMENT;
 if(!length || length>A04_WMT_PATCH_MAX || length-1>0xffffffffu-address)return A04_WMT_LENGTH;
 s->body=body;s->length=length;s->offset=0;s->phase=0;s->pending=0;s->fragment=0;
 s->address=address;s->failed=0;s->command_length=0;return 0;
}
static inline int a04_wmt_patch_next(struct a04_wmt_patch_plan *s,uint8_t *out,unsigned capacity,unsigned *written){
 if(!s || !out || !written)return A04_WMT_ARGUMENT;
 *written=0;
 if(s->failed)return s->failed;
 if(s->pending)return A04_WMT_BUSY;
 if(s->phase==3)return A04_WMT_DONE;
 if(!s->body || !s->length || s->length>A04_WMT_PATCH_MAX || s->phase>3 || s->offset>=s->length)return A04_WMT_STATE;
 unsigned n=s->phase<2?20:((s->length-s->offset)>1000?1000:s->length-s->offset)+5;
 if(capacity<n)return A04_WMT_LENGTH;
 if(s->phase<2){
  const uint8_t prefix[8]={1,8,16,0,1,1,0,1};
  for(unsigned i=0;i<8;i++)out[i]=prefix[i];
  a04_wmt_put32(out+8,s->phase?0x020908fc:0x020903d4);
  a04_wmt_put32(out+12,s->phase?s->address:0);a04_wmt_put32(out+16,0xffffffffu);
  s->fragment=0;
 }else{
  unsigned frag=n-5;
  out[0]=1;out[1]=1;a04_wmt_put16(out+2,frag+1);
  out[4]=(s->offset+frag==s->length)?3:(s->offset?2:1);
  for(unsigned i=0;i<frag;i++)out[5+i]=s->body[s->offset+i];
  s->fragment=frag;
 }
 for(unsigned i=0;i<n;i++)s->command[i]=out[i];
 s->command_length=n;s->pending=1;*written=n;return 0;
}
static inline int a04_wmt_patch_accept(struct a04_wmt_patch_plan *s,const uint8_t *event,unsigned length){
 if(!s || !event)return A04_WMT_ARGUMENT;
 if(s->failed)return s->failed;
 if(!s->pending || s->phase>2)return A04_WMT_STATE;
 struct a04_wmt_view reply;
 int e=a04_wmt_event(s->command,s->command_length,event,length,&reply);
 if(!e){
  if(s->phase<2){if(reply.length!=3 || reply.data[0] || reply.data[1] || reply.data[2]!=1)e=A04_WMT_MISMATCH;}
  else if(reply.length)e=A04_WMT_MISMATCH;
 }
 if(e){s->failed=e;return e;}
 s->pending=0;
 if(s->phase<2)s->phase++;
 else{s->offset+=s->fragment;if(s->offset==s->length)s->phase=3;}
 return 0;
}
#endif
