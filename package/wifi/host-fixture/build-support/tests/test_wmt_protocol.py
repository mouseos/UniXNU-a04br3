# SPDX-License-Identifier: MIT
"""Execute pure MT6630 wire codecs; fixtures are board wmt_ic_6630.c vectors."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

INCLUDE = Path(__file__).resolve().parents[2] / 'iokit/Kernel/arm/sdio'
BOARD = Path(__file__).resolve().parents[5] / 'port/gsi/kernel-4.14/modules/connectivity/common/common_main'


class WMTProtocol(unittest.TestCase):
    def run_c(self, body):
        source = '#include <stdint.h>\n#include <assert.h>\n#include <string.h>\n#include "A04BR3WMTProtocol.h"\n' + body
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'test.c').write_text(source)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-I', str(INCLUDE),
                            str(path / 'test.c'), '-o', str(path / 'test')], check=True)
            subprocess.run([str(path / 'test')], check=True)
            subprocess.run(['c++', '-std=c++14', '-Wall', '-Wextra', '-Werror',
                            '-I', str(INCLUDE), '-x', 'c++', '-fsyntax-only',
                            str(path / 'test.c')], check=True)

    def test_stp_modes_corruption_and_every_stream_split(self):
        self.run_c(r'''
int main(void){
 const uint8_t cmd[]={1,7,1,0,4};
 const uint8_t mand[]={0x80,0x40,5,0,1,7,1,0,4,0,0};
 uint8_t out[2010],data[1999];unsigned n=0;struct a04_stp_frame f;
 assert(a04_stp_crc16((const uint8_t *)"123456789",9)==0xbb3d);
 assert(!a04_stp_encode(0,4,0,0,0,cmd,5,out,sizeof(out),&n));
 assert(n==sizeof(mand) && !memcmp(out,mand,n));
 for(unsigned mode=0;mode<2;mode++){
  for(unsigned len=1;len<=1999;len++){
   for(unsigned i=0;i<len;i++)data[i]=(uint8_t)(i*37+len);
   assert(!a04_stp_encode(mode,4,mode?7:0,mode?3:0,mode?1:0,data,len,out,sizeof(out),&n));
   assert(n==len+6 && !a04_stp_parse(mode,out,n,&f));
   assert(f.task==4 && f.payload.length==len && !memcmp(f.payload.data,data,len));
   assert(f.seq==(mode?7u:0u) && f.ack==(mode?3u:0u) && f.nak==mode);
  }
  assert(!a04_stp_encode(mode,4,0,0,0,cmd,5,out,sizeof(out),&n));
  for(unsigned split=0;split<=n;split++){
   struct a04_stp_stream s;unsigned used=999,used2=999;
   a04_stp_stream_reset(&s);
   int e=a04_stp_stream_feed(&s,mode,out,split,&used,&f);
   assert(used==split && e==(split==n?0:A04_WMT_MORE));
   if(split<n){assert(!a04_stp_stream_feed(&s,mode,out+split,n-split,&used2,&f));assert(used2==n-split);}
   assert(f.payload.length==5 && !memcmp(f.payload.data,cmd,5));
  }
  /* Single-byte slices and a read that contains two frames. */
  struct a04_stp_stream s;a04_stp_stream_reset(&s);
  for(unsigned i=0;i<n;i++){unsigned used=0;int e=a04_stp_stream_feed(&s,mode,out+i,1,&used,&f);assert(used==1 && e==(i==n-1?0:A04_WMT_MORE));}
  memcpy(out+n,out,n);a04_stp_stream_reset(&s);unsigned used=0;
  assert(!a04_stp_stream_feed(&s,mode,out,n*2,&used,&f) && used==n);
  out[3]^=1;assert(a04_stp_parse(mode,out,n,&f)==A04_WMT_CHECKSUM);out[3]^=1;
  out[n-1]^=1;assert(a04_stp_parse(mode,out,n,&f)==A04_WMT_CRC);out[n-1]^=1;
  for(unsigned shortlen=0;shortlen<n;shortlen++)assert(a04_stp_parse(mode,out,shortlen,&f)==A04_WMT_MORE);
 }
 assert(!a04_stp_encode(1,0,0,5,0,0,0,out,sizeof(out),&n) && n==4);
 assert(!a04_stp_parse(1,out,n,&f) && !f.payload.length && f.ack==5);
 assert(a04_stp_encode(0,4,0,0,0,data,2000,out,sizeof(out),&n)==A04_WMT_LENGTH);
 assert(a04_stp_encode(0,4,1,0,0,cmd,5,out,sizeof(out),&n)==A04_WMT_ARGUMENT);
 assert(a04_stp_encode(0,4,0,0,0,cmd,5,out,10,&n)==A04_WMT_LENGTH && !n);
 assert(a04_stp_encode(0,4,0,0,0,0,0,out,sizeof(out),&n)==A04_WMT_LENGTH);
 uint8_t invalid[]={0x80,0x47,0xd0,0};
 assert(a04_stp_parse(0,invalid,4,&f)==A04_WMT_LENGTH);
 invalid[0]=0xc0;assert(a04_stp_parse(0,invalid,4,&f)==A04_WMT_FORMAT);
 struct a04_stp_stream s;a04_stp_stream_reset(&s);unsigned used=0;
 assert(a04_stp_stream_feed(&s,0,invalid,4,&used,&f)==A04_WMT_FORMAT);
 assert(a04_stp_stream_feed(&s,0,mand,sizeof(mand),&used,&f)==A04_WMT_FORMAT && !used);
 return 0;
}
''')

    def test_wmt_event_matching_and_sdio_aggregation(self):
        self.run_c(r'''
int main(void){
 const uint8_t cmd[]={1,7,1,0,4},evt[]={2,7,1,0,0};
 uint8_t out[2560],stp[64],bad[32];unsigned n=0;struct a04_wmt_view reply;
 assert(!a04_wmt_command(7,cmd+4,1,out,sizeof(out),&n) && n==5 && !memcmp(out,cmd,5));
 assert(!a04_wmt_event(cmd,5,evt,5,&reply) && !reply.length);
 for(unsigned k=0;k<5;k++)assert(a04_wmt_event(cmd,5,evt,k,&reply)==A04_WMT_MORE);
 memcpy(bad,evt,5);bad[0]=1;assert(a04_wmt_event(cmd,5,bad,5,&reply)==A04_WMT_FORMAT);
 memcpy(bad,evt,5);bad[1]=8;assert(a04_wmt_event(cmd,5,bad,5,&reply)==A04_WMT_MISMATCH);
 memcpy(bad,evt,5);bad[4]=1;assert(a04_wmt_event(cmd,5,bad,5,&reply)==A04_WMT_STATUS);
 memcpy(bad,evt,5);assert(a04_wmt_event(cmd,5,bad,6,&reply)==A04_WMT_LENGTH);
 bad[2]=0;assert(a04_wmt_event(cmd,5,bad,4,&reply)==A04_WMT_LENGTH);
 struct a04_wmt_sdio_lengths sizes;
 assert(!a04_wmt_sdio_encode(cmd,5,out,sizeof(out),&sizes));
 const uint8_t golden[]={15,0,0,0,0x80,0x40,5,0,1,7,1,0,4,0,0,0};
 assert(sizes.logical==15 && sizes.fifo==16 && sizes.bus==16 && !memcmp(out,golden,16));
 /* RX has two aligned STP events (task0 is visible, task4 matches WMT). */
 memset(out,0xa5,sizeof(out));out[0]=28;out[1]=out[2]=out[3]=0;
 assert(!a04_stp_encode(0,0,0,0,0,evt,5,stp,sizeof(stp),&n));memcpy(out+4,stp,n);
 assert(!a04_stp_encode(0,4,0,0,0,evt,5,stp,sizeof(stp),&n));memcpy(out+16,stp,n);
 struct a04_wmt_sdio_cursor c;struct a04_stp_frame f;
 assert(!a04_wmt_sdio_open(out,512,28,&c));
 assert(!a04_wmt_sdio_next(&c,&f) && f.task==0 && c.offset==12);
 assert(!a04_wmt_sdio_next(&c,&f) && f.task==4);
 assert(!a04_wmt_event(cmd,5,f.payload.data,f.payload.length,&reply));
 assert(a04_wmt_sdio_next(&c,&f)==A04_WMT_DONE);
 assert(a04_wmt_sdio_open(out,27,28,&c)==A04_WMT_MORE);
 assert(a04_wmt_sdio_open(out,512,27,&c)==A04_WMT_LENGTH);
 out[2]=1;assert(a04_wmt_sdio_open(out,512,28,&c)==A04_WMT_FORMAT);out[2]=0;
 /* Truncated second frame never becomes an event. */
 out[0]=26;assert(!a04_wmt_sdio_open(out,26,26,&c));
 assert(!a04_wmt_sdio_next(&c,&f));assert(a04_wmt_sdio_next(&c,&f)==A04_WMT_MORE);
 uint8_t params[1995];memset(params,0xee,sizeof(params));uint8_t command[1999];
 for(unsigned len=0;len<=1995;len++){
  assert(!a04_wmt_command(1,params,len,command,sizeof(command),&n));
  memset(out,0xa5,sizeof(out));assert(!a04_wmt_sdio_encode(command,n,out,sizeof(out),&sizes));
  assert(sizes.logical==len+14 && sizes.fifo==((len+17)&~3u));
  assert(sizes.bus==(sizes.fifo>512?((sizes.fifo+511)&~511u):sizes.fifo));
  for(unsigned k=sizes.logical;k<sizes.bus;k++)assert(!out[k]);
  assert(a04_wmt_sdio_encode(command,n,out,sizes.bus-1,&sizes)==A04_WMT_LENGTH);
 }
 return 0;
}
''')

    def test_patch_header_planning_and_failure_latch(self):
        self.run_c(r'''
static const uint8_t address_evt[]={2,8,4,0,0,0,0,1};
static const uint8_t patch_evt[]={2,1,1,0,0};
static const uint8_t base_cmd[]={1,8,16,0,1,1,0,1,0xd4,3,9,2,0,0,0,0,255,255,255,255};
static const uint8_t part_cmd[]={1,8,16,0,1,1,0,1,0xfc,8,9,2,0,0,8,0,255,255,255,255};
int main(void){
 uint8_t blob[28+3001],out[1005],bad[9];unsigned n=0;
 for(unsigned i=0;i<sizeof(blob);i++)blob[i]=(uint8_t)(i*37);
 memcpy(blob,"20261021abcdefgh",16);memcpy(blob+16,"TEST",4);
 blob[20]=0x12;blob[21]=0x34;blob[22]=0x56;blob[23]=0x78;
 struct a04_wmt_patch_info info;
 for(unsigned k=0;k<=28;k++)assert(a04_wmt_patch_parse(blob,k,&info)==A04_WMT_LENGTH);
 assert(!a04_wmt_patch_parse(blob,sizeof(blob),&info));
 assert(info.hardware==0x1234 && info.software==0x5678 && info.body.data==blob+28 && info.body.length==3001);
 assert(!memcmp(info.date,blob,16) && !memcmp(info.platform,blob+16,4) && !memcmp(info.patch_version,blob+24,4));
 const unsigned lengths[]={1,999,1000,1001,2000,2001,3001};
 for(unsigned k=0;k<7;k++){
  unsigned length=lengths[k];struct a04_wmt_patch_plan s;
  assert(!a04_wmt_patch_begin(&s,blob+28,length,0x80000));
  assert(a04_wmt_patch_accept(&s,patch_evt,5)==A04_WMT_STATE);
  assert(a04_wmt_patch_next(&s,out,19,&n)==A04_WMT_LENGTH && !n && !s.pending);
  assert(!a04_wmt_patch_next(&s,out,sizeof(out),&n) && n==20 && !memcmp(out,base_cmd,20));
  assert(a04_wmt_patch_next(&s,out,sizeof(out),&n)==A04_WMT_BUSY);
  assert(!a04_wmt_patch_accept(&s,address_evt,8));
  assert(!a04_wmt_patch_next(&s,out,sizeof(out),&n) && n==20 && !memcmp(out,part_cmd,20));
  assert(!a04_wmt_patch_accept(&s,address_evt,8));
  unsigned offset=0,count=0;
  while(offset<length){
   unsigned frag=length-offset>1000?1000:length-offset;
   assert(!a04_wmt_patch_next(&s,out,sizeof(out),&n) && n==frag+5);
   assert(out[0]==1 && out[1]==1 && a04_wmt_le16(out+2)==frag+1);
   unsigned flag=offset+frag==length?3:(offset?2:1);assert(out[4]==flag);
   assert(!memcmp(out+5,blob+28+offset,frag));
   assert(s.offset==offset);assert(!a04_wmt_patch_accept(&s,patch_evt,5));
   offset+=frag;count++;assert(s.offset==offset);
  }
  assert(count==(length+999)/1000 && s.phase==3);
  assert(a04_wmt_patch_next(&s,out,sizeof(out),&n)==A04_WMT_DONE && !n);
  assert(a04_wmt_patch_accept(&s,patch_evt,5)==A04_WMT_STATE);
 }
 for(unsigned phase=0;phase<3;phase++)for(unsigned fault=0;fault<6;fault++){
  struct a04_wmt_patch_plan s;assert(!a04_wmt_patch_begin(&s,blob+28,1001,0x80000));
  for(unsigned i=0;i<phase;i++){assert(!a04_wmt_patch_next(&s,out,sizeof(out),&n));assert(!a04_wmt_patch_accept(&s,address_evt,8));}
  assert(!a04_wmt_patch_next(&s,out,sizeof(out),&n));
  unsigned len=phase<2?8:5;memcpy(bad,phase<2?address_evt:patch_evt,len);
  if(fault==0)bad[0]=1;
  if(fault==1)bad[1]=0xff;
  if(fault==2)bad[4]=1;
  if(fault==3)len--;
  if(fault==4)len++;
  if(fault==5){bad[2]++;len++;}
  assert(a04_wmt_patch_accept(&s,bad,len)!=0 && s.failed && !s.offset);
  int e=s.failed;assert(a04_wmt_patch_next(&s,out,sizeof(out),&n)==e);
  assert(a04_wmt_patch_accept(&s,phase<2?address_evt:patch_evt,phase<2?8:5)==e);
 }
 struct a04_wmt_patch_plan s;
 assert(a04_wmt_patch_begin(&s,blob,0,0)==A04_WMT_LENGTH);
 assert(a04_wmt_patch_begin(&s,blob,2,0xffffffffu)==A04_WMT_LENGTH);
 assert(a04_wmt_patch_begin(&s,blob,A04_WMT_PATCH_MAX+1,0)==A04_WMT_LENGTH);
 return 0;
}
''')

    def test_mutated_and_truncated_untrusted_input(self):
        self.run_c(r'''
static uint32_t state=0x6630;
static uint8_t random_byte(void){state=state*1664525u+1013904223u;return (uint8_t)(state>>24);}
int main(void){
 const uint8_t cmd[]={1,1,2,0,3,0},good[]={2,1,1,0,0};
 uint8_t p[2560];struct a04_stp_frame f;struct a04_wmt_view v;
 struct a04_wmt_sdio_cursor c;struct a04_wmt_patch_info info;
 for(unsigned iteration=0;iteration<12000;iteration++){
  unsigned len=iteration%sizeof(p);
  for(unsigned i=0;i<len;i++)p[i]=random_byte();
  for(unsigned mode=0;mode<2;mode++){
   (void)a04_stp_parse(mode,p,len,&f);
   struct a04_stp_stream stream;a04_stp_stream_reset(&stream);
   unsigned used=0;(void)a04_stp_stream_feed(&stream,mode,p,len,&used,&f);
   assert(used<=len && stream.used<=sizeof(stream.bytes));
  }
  (void)a04_wmt_event(cmd,sizeof(cmd),p,len,&v);
  (void)a04_wmt_event(p,len,good,sizeof(good),&v);
  (void)a04_wmt_patch_parse(p,len,&info);
  if(!a04_wmt_sdio_open(p,len,len,&c)){
   unsigned packets=0;
   while(!a04_wmt_sdio_next(&c,&f))assert(++packets<=256);
   assert(c.offset<=c.length);
  }
 }
 /* Exact small allocations expose lookahead beyond a supplied slice. */
 for(unsigned len=0;len<32;len++){
  uint8_t *q=(uint8_t *)malloc(len?len:1);assert(q);memset(q,0,len);
  (void)a04_stp_parse(0,q,len,&f);(void)a04_wmt_event(cmd,sizeof(cmd),q,len,&v);
  (void)a04_wmt_patch_parse(q,len,&info);(void)a04_wmt_sdio_open(q,len,len,&c);free(q);
 }
 struct a04_stp_stream stream;a04_stp_stream_reset(&stream);unsigned used=0;
 uint8_t first=0x80;
 assert(a04_stp_stream_feed(&stream,0,&first,1,&used,&f)==A04_WMT_MORE);
 assert(a04_stp_stream_feed(&stream,1,&first,1,&used,&f)==A04_WMT_ARGUMENT && !used);
 return 0;
}
'''.replace('static uint32_t state=', '#include <stdlib.h>\nstatic uint32_t state='))

    @unittest.skipUnless((BOARD / 'core/wmt_ic_6630.c').exists(), 'board source not present')
    def test_against_board_source_command_vectors_and_crc_table(self):
        text = (BOARD / 'core/wmt_ic_6630.c').read_text()
        def vector(name):
            match = re.search(r'\b' + name + r'\[\]\s*=\s*\{([^}]+)\}', text)
            self.assertIsNotNone(match)
            return [int(v, 16) for v in re.findall(r'0x[0-9a-fA-F]+', match.group(1))]
        vectors = {name: vector(name) for name in [
            'WMT_PATCH_ADDRESS_CMD', 'WMT_PATCH_P_ADDRESS_CMD',
            'WMT_PATCH_ADDRESS_EVT', 'WMT_PATCH_EVT', 'WMT_RESET_CMD', 'WMT_RESET_EVT']}
        fixtures = '\n'.join('static const uint8_t ' + name + '[]={' + ','.join(map(str, values)) + '};'
                             for name, values in vectors.items())
        crc_source = (BOARD / 'linux/osal.c').read_text()
        table = re.search(r'crc16_table\[256\]\s*=\s*\{([^}]+)\}', crc_source).group(1)
        fixtures += '\nstatic const uint16_t board_crc[]={' + table + '};\n'
        self.run_c(fixtures + r'''
int main(void){
 uint8_t body[1]={0},out[32];unsigned n=0;struct a04_wmt_patch_plan s;
 assert(!a04_wmt_patch_begin(&s,body,1,0x80000));
 assert(!a04_wmt_patch_next(&s,out,sizeof(out),&n));
 assert(n==sizeof(WMT_PATCH_ADDRESS_CMD) && !memcmp(out,WMT_PATCH_ADDRESS_CMD,n));
 assert(!a04_wmt_patch_accept(&s,WMT_PATCH_ADDRESS_EVT,sizeof(WMT_PATCH_ADDRESS_EVT)));
 assert(!a04_wmt_patch_next(&s,out,sizeof(out),&n));
 assert(n==sizeof(WMT_PATCH_P_ADDRESS_CMD) && !memcmp(out,WMT_PATCH_P_ADDRESS_CMD,n));
 assert(!a04_wmt_patch_accept(&s,WMT_PATCH_ADDRESS_EVT,sizeof(WMT_PATCH_ADDRESS_EVT)));
 assert(!a04_wmt_patch_next(&s,out,sizeof(out),&n));
 assert(!a04_wmt_patch_accept(&s,WMT_PATCH_EVT,sizeof(WMT_PATCH_EVT)));
 struct a04_wmt_view reply;
 assert(!a04_wmt_event(WMT_RESET_CMD,sizeof(WMT_RESET_CMD),WMT_RESET_EVT,sizeof(WMT_RESET_EVT),&reply));
 for(unsigned i=0;i<256;i++){uint8_t b=(uint8_t)i;assert(a04_stp_crc16(&b,1)==board_crc[i]);}
 return 0;
}
''')
