/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * CPU-only MT6630 external firmware selection/staging contract.
 * Source: CT3 combo_tool/src/stp_uart_launcher.c (CUST_MULTI_PATCH=1):
 * mt6630_patch prefix, SW BE16 at22 compared to FVR using xor&0xff,
 * count/sequence in byte24, destination LE32 bytes24..27 with byte24 zero.
 * Reference source provenance is NOT proof of stock launcher binary identity.
 * No I/O, no embedded firmware, no native PA interpretation or device send.
 */
#ifndef A04BR3_WMT_FIRMWARE_H
#define A04BR3_WMT_FIRMWARE_H
#include "A04BR3WMTRoundtrip.h"
enum { A04_WMT_FW_ARGUMENT=60,A04_WMT_FW_REVISION=61,A04_WMT_FW_ROM=62,
 A04_WMT_FW_METADATA=63,A04_WMT_FW_CONTAINER=64,A04_WMT_FW_RANGE=65,
 A04_WMT_FW_CRC=66,A04_WMT_FW_SET=67,A04_WMT_FW_MEMORY=68,
 A04_WMT_FW_STATE=69 };
enum { A04_WMT_FW_MAX_COUNT=15,A04_WMT_FW_MAX_BUNDLE=32*1024*1024 };
struct a04_wmt_identity {uint32_t chip,hardware,rom;};
struct a04_wmt_revision {unsigned exact,table_hardware,eco,patch_family,major,minor;};
static inline int a04_wmt_revision_decode(const struct a04_wmt_identity *id,struct a04_wmt_revision *r){
 if(!id || !r)return A04_WMT_FW_ARGUMENT;
 if(id->chip!=0x6630 || id->hardware>0xffff || id->rom>0xffff)return A04_WMT_FW_REVISION;
 const unsigned table[4]={0x8a00,0x8a10,0x8a11,0x8b11};
 int found=-1;unsigned exact=0;
 for(int i=3;i>=0;i--)if(id->hardware==table[i]){found=i;exact=1;break;}
 if(found<0)for(int i=3;i>=0;i--)if((id->hardware&0xf0)==(table[i]&0xf0)){found=i;break;}
 if(found<0)return A04_WMT_FW_REVISION;
 r->exact=exact;r->table_hardware=table[found];r->eco=(unsigned)found+1;
 r->patch_family=found?2:1;r->major=id->hardware&0xf0;r->minor=id->hardware&15;return 0;
}
struct a04_wmt_firmware_file {
 struct a04_wmt_patch_info header;
 unsigned count,sequence;uint32_t destination;
};
static inline int a04_wmt_firmware_name(const uint8_t *name,unsigned capacity){
 const char prefix[]="mt6630_patch";
 if(!name || capacity<sizeof(prefix))return 0;
 unsigned n=0;
 for(;n<capacity && name[n];n++){
  unsigned c=name[n];
  if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_'||c=='-'||c=='.'))return 0;
 }
 if(n==capacity || n<sizeof(prefix)-1)return 0;
 for(unsigned i=0;i<sizeof(prefix)-1;i++)if(name[i]!=(uint8_t)prefix[i])return 0;
 for(unsigned i=n+1;i<capacity;i++)if(name[i])return 0;
 return 1;
}
static inline int a04_wmt_firmware_inspect(const struct a04_wmt_identity *id,
 const uint8_t *name,unsigned name_bytes,const uint8_t *blob,unsigned length,
 struct a04_wmt_firmware_file *file){
 if(!id || !blob || !file)return A04_WMT_FW_ARGUMENT;
 struct a04_wmt_revision revision;int e=a04_wmt_revision_decode(id,&revision);if(e)return e;
 if(!a04_wmt_firmware_name(name,name_bytes))return A04_WMT_FW_METADATA;
 if(a04_wmt_patch_parse(blob,length,&file->header))return A04_WMT_FW_RANGE;
 if(((file->header.software^id->rom)&255)!=0)return A04_WMT_FW_ROM;
 /* Extra conservative major-HW guard. Actual launcher filters FVR only, not
  * exact HW equality: measured8a12 is not a reason to reject header8a11. */
 if((file->header.hardware&0xf0)!=revision.major)return A04_WMT_FW_REVISION;
 file->count=blob[24]>>4;file->sequence=blob[24]&15;
 if(!file->count || !file->sequence || file->sequence>file->count)return A04_WMT_FW_METADATA;
 file->destination=(uint32_t)blob[25]<<8|(uint32_t)blob[26]<<16|(uint32_t)blob[27]<<24;
 if(file->header.body.length-1>0xffffffffu-file->destination)return A04_WMT_FW_RANGE;
 return 0;
}
/* CRC identifies bytes against the explicitly supplied descriptor, not trusted
 * signing/authenticity. Parent pins SHA256/provenance when assembling assets. */
static inline uint32_t a04_wmt_firmware_crc32(const uint8_t *p,unsigned n){
 uint32_t crc=0xffffffffu;
 for(unsigned i=0;i<n;i++){
  crc^=p[i];for(unsigned k=0;k<8;k++)crc=(crc>>1)^((crc&1)?0xedb88320u:0);
 }
 return crc^0xffffffffu;
}
struct a04_wmt_firmware_set {
 unsigned count;struct a04_wmt_firmware_file files[A04_WMT_FW_MAX_COUNT];
};
/* A04WMT1 header32 and descriptor64 described in firmware-interface.txt handoff.
 * Input must be a stable CPU-readable snapshot. Views borrow it. Files are
 * sorted by one-based sequence, never by caller order or filename suffix.
 */
static inline int a04_wmt_firmware_bundle(const struct a04_wmt_identity *id,
 const void *data,unsigned length,struct a04_wmt_firmware_set *set){
 if(!id || !data || !set)return A04_WMT_FW_ARGUMENT;
 set->count=0;
 const uint8_t *p=(const uint8_t *)data;const uint8_t magic[8]={'A','0','4','W','M','T','1',0};
 if(length<32 || length>A04_WMT_FW_MAX_BUNDLE)return A04_WMT_FW_RANGE;
 for(unsigned i=0;i<8;i++)if(p[i]!=magic[i])return A04_WMT_FW_CONTAINER;
 unsigned count=a04_wmt_get32(p+12);
 if(a04_wmt_get32(p+8)!=1 || !count || count>A04_WMT_FW_MAX_COUNT ||
    a04_wmt_get32(p+16)!=length || a04_wmt_get32(p+20)!=64 ||
    a04_wmt_get32(p+24)!=32 || a04_wmt_get32(p+28))return A04_WMT_FW_CONTAINER;
 unsigned table_end=32+count*64,seen=0;
 if(length<table_end)return A04_WMT_FW_RANGE;
 for(unsigned i=0;i<count;i++){
  const uint8_t *entry=p+32+i*64;
  unsigned off=a04_wmt_get32(entry+48),bytes=a04_wmt_get32(entry+52);
  if(a04_wmt_get32(entry+60))return A04_WMT_FW_CONTAINER;
  if(off<table_end || off>length || bytes<=28 || bytes>A04_WMT_PATCH_MAX+28 || bytes>length-off)return A04_WMT_FW_RANGE;
  /* O(15^2) bounded, including duplicate descriptors/names and aliased ranges. */
  for(unsigned j=0;j<i;j++){
   const uint8_t *prev=p+32+j*64;
   unsigned po=a04_wmt_get32(prev+48),pn=a04_wmt_get32(prev+52),equal=1;
   if(off<po+pn && po<off+bytes)return A04_WMT_FW_RANGE;
   for(unsigned k=0;k<48;k++)if(entry[k]!=prev[k])equal=0;
   if(equal)return A04_WMT_FW_SET;
  }
  if(a04_wmt_firmware_crc32(p+off,bytes)!=a04_wmt_get32(entry+56))return A04_WMT_FW_CRC;
  struct a04_wmt_firmware_file file;
  int e=a04_wmt_firmware_inspect(id,entry,48,p+off,bytes,&file);if(e)return e;
  if(file.count!=count || (seen&(1u<<(file.sequence-1))))return A04_WMT_FW_SET;
  /* A complete release must have identical build/platform/HW/SW across all
   * parts; reject directory mixtures even when FVR-low8 happens to match. */
  if(i){
   const struct a04_wmt_patch_info *h=&set->files[0].header;
   /* First file may have sequence>1, so locate the first occupied slot. */
   for(unsigned k=0;k<count;k++)if(seen&(1u<<k)){h=&set->files[k].header;break;}
   if(h->hardware!=file.header.hardware || h->software!=file.header.software)return A04_WMT_FW_SET;
   for(unsigned k=0;k<16;k++)if(h->date[k]!=file.header.date[k])return A04_WMT_FW_SET;
   for(unsigned k=0;k<4;k++)if(h->platform[k]!=file.header.platform[k])return A04_WMT_FW_SET;
  }
  set->files[file.sequence-1]=file;seen|=1u<<(file.sequence-1);
 }
 if(seen!=(1u<<count)-1)return A04_WMT_FW_SET;
 set->count=count;return 0;
}
static inline int a04_wmt_firmware_read(const struct a04_wmt_firmware_set *set,
 unsigned sequence,unsigned offset,void *output,unsigned bytes){
 if(!set || !output || !sequence || sequence>set->count || set->count>A04_WMT_FW_MAX_COUNT)return A04_WMT_FW_ARGUMENT;
 const struct a04_wmt_view *body=&set->files[sequence-1].header.body;
 if(!body->data || offset>body->length || bytes>body->length-offset)return A04_WMT_FW_RANGE;
 uint8_t *p=(uint8_t *)output;for(unsigned i=0;i<bytes;i++)p[i]=body->data[offset+i];return 0;
}
static inline int a04_wmt_firmware_plan(const struct a04_wmt_firmware_set *set,
 unsigned sequence,struct a04_wmt_patch_plan *plan){
 if(!set || !plan || !sequence || sequence>set->count || set->count>A04_WMT_FW_MAX_COUNT)return A04_WMT_FW_ARGUMENT;
 const struct a04_wmt_firmware_file *file=&set->files[sequence-1];
 return a04_wmt_patch_begin(plan,file->header.body.data,file->header.body.length,file->destination);
}
/* Parent integration ABI: service must be a retained IOService matching the
 * A04BR3SDIO instance. Buffers are synchronous CPU views, never physical PAs. */
#ifdef __cplusplus
class IOService;
extern "C" int a04br3_wmt_stage_firmware(IOService *,const void *,unsigned);
extern "C" int a04br3_wmt_copy_firmware(IOService *,unsigned,unsigned,void *,unsigned);
#endif
#endif
