# SPDX-License-Identifier: MIT
"""Execute the real read-only runtime parser with an instrumented partition.
No extracted production calibration is generated or required.
"""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
BLOCK=0x20000
ROOT=Path(__file__).resolve().parents[2]

def image():
    p=bytearray(BLOCK*8);record=bytearray(514)
    struct.pack_into('<HH',record,0,0x106,0);record[4:10]=bytes.fromhex('021122334455');record[196]=1
    struct.pack_into('<HH',record,256,1,0);check=0
    for i,v in enumerate(record[:512]):check=((check+v) if i%2==0 else check^v)&255
    record[512:]=bytes([0xaa,check]);p[BLOCK:BLOCK+514]=record
    struct.pack_into('<III',p,0,192,len(record),0xffffffff)
    checksum=0
    for i in range(0,512,4):
        w=struct.unpack_from('<I',record,i)[0];checksum=checksum^w if i%8==0 else (checksum+w)&0xffffffff
    checksum=(checksum+int.from_bytes(record[512:],'little'))&0xffffffff
    struct.pack_into('<I',p,12,checksum^len(record));struct.pack_into('<III',p,64,7,0,514)
    name=b'/data/nvram/APCFG/APRDEB/WIFI\0';p[76:76+len(name)]=name
    p[BLOCK*4:BLOCK*4+15]=bytes(15);struct.pack_into('<I',p,BLOCK*4+15,0xaabbccdd)
    return p

class RuntimeNVRAM(unittest.TestCase):

    def test_actual_parser_reads_checksums_and_failures(self):
        c=r'''
#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include "a04br3_wlan_nvram.h"
struct input {unsigned char *p;unsigned size,calls,fail;};
static int readpart(void *arg,uint32_t off,void *out,unsigned n){
 struct input *i=arg;assert(off<=i->size && n<=i->size-off);
 i->calls++;if(i->calls==i->fail)return EIO;
 memcpy(out,i->p+off,n);return 0;
}
int main(int argc,char **argv){
 assert(argc==2);FILE *f=fopen(argv[1],"rb");assert(f);fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);
 struct input i={calloc(1,n),(unsigned)n,0,0};assert(i.p);assert(fread(i.p,1,n,f)==(size_t)n);fclose(f);
 struct a04_nvram_work *w=calloc(1,sizeof(*w));assert(w);uint8_t out[512]={0};
 struct a04_nvram_reader r={&i,i.size,readpart};assert(a04_nvram_wifi(&r,w,out)==0);assert(out[196]==1);
 unsigned calls=i.calls;
 for(unsigned at=1;at<=calls;at++){
  memset(out,0,sizeof(out));memset(w,0,sizeof(*w));i.calls=0;i.fail=at;
  assert(a04_nvram_wifi(&r,w,out)==EIO);for(unsigned j=0;j<512;j++)assert(out[j]==0);
 }
 i.fail=0;
 /* Corrupt map size, bounds, pathname, data checksum, calibration/trailer,
  * and the three-copy bitmap. Every case must reject without publishing. */
 unsigned faults[]={0,4,8,12,68,72,76,0x20000,0x20000+513,0x80000+5,0x80000+15};
 for(unsigned k=0;k<sizeof(faults)/sizeof(*faults);k++){
  unsigned at=faults[k];i.p[at]^=0x40;memset(out,0,512);i.calls=0;
  assert(a04_nvram_wifi(&r,w,out)!=0);for(unsigned j=0;j<512;j++)assert(out[j]==0);i.p[at]^=0x40;
 }
 /* Same logical file relocated past a bitmap-marked bad block. */
 memcpy(i.p+0x40000,i.p+0x20000,514);memset(i.p+0x20000,0xff,514);
 for(unsigned k=0;k<3;k++)i.p[0x80000+k*5+1]=0xff;
 memset(out,0,512);assert(a04_nvram_wifi(&r,w,out)==0 && out[196]==1);
 r.bytes--;assert(a04_nvram_wifi(&r,w,out)==EINVAL);
 free(w);free(i.p);return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);(d/'input').write_bytes(image());(d/'test.c').write_text(c)
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(ROOT/'package/wifi'),str(d/'test.c'),'-o',str(d/'test')],check=True)
            subprocess.run([str(d/'test'),str(d/'input')],check=True)

if __name__=='__main__':unittest.main()
