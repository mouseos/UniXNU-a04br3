#!/usr/bin/env python3
"""Actual canonical IOKit log route with framed-character mocks; no device access."""
from pathlib import Path
import os
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
p=(ROOT/'patches/mesa/pvr-darwin-winsys.patch').read_text()
s='\n'.join(l[1:] for l in p.splitlines() if l.startswith('+') and not l.startswith('+++'))
a=s.index('void pvr_darwin_sipf1_log(const char *text)\n{');b=s.index('{',a);e=b+1;d=1
while d:
 d+=(s[e]=='{')-(s[e]=='}');e+=1
actual=s[a:e]
s_header=s
h=r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
typedef unsigned io_service_t,io_connect_t;typedef int kern_return_t;typedef void *CFMutableDictionaryRef;
#define KERN_SUCCESS 0
#define kIOMainPortDefault 0
#define NativePowerVRServiceName "NativePowerVRGpu"
#define NativePowerVRMetalMarkBase 0x41470000u
static unsigned matchingCalls,lookups,releases,opens,closes,lines,used,starts;static int mode;static char line[64],payload[4096];static unsigned payloadLen;
static unsigned mach_task_self(void){return 17;}
static CFMutableDictionaryRef IOServiceMatching(const char *n){assert(!strcmp(n,"IOAcceleratorES")||!strcmp(n,NativePowerVRServiceName));matchingCalls++;return mode==1?NULL:(void*)1;}
static io_service_t IOServiceGetMatchingService(unsigned p,CFMutableDictionaryRef m){assert(!p&&m);lookups++;return mode==2?0:3;}
static void IOObjectRelease(unsigned s){assert(s==3);releases++;}
static kern_return_t IOServiceOpen(unsigned s,unsigned task,uint32_t type,unsigned *conn){assert(s==3&&task==17&&*conn==0);assert((type&0xffff0000u)==NativePowerVRMetalMarkBase);opens++;
 unsigned v=type&0xffff;
 if(v==0x7fff){assert(!starts&&!used);starts=1;}
 else if(v==0x7ffd){assert(starts&&used<=63);line[used]=0;assert(!strncmp(line,"S1 ",3));unsigned id,part,parts;int n=0;assert(sscanf(line,"S1 %x.%u/%u %n",&id,&part,&parts,&n)==3);assert(part>=1&&part<=parts&&parts<=7&&n>0);assert(payloadLen+used-n<sizeof(payload));memcpy(payload+payloadLen,line+n,used-n);payloadLen+=used-n;payload[payloadLen]=0;lines++;starts=used=0;}
 else {assert(starts&&v>0x7000&&v<0x7080&&used<63);line[used++]=(char)(v&0x7f);}
 if(mode==3){*conn=9;return 0;}return -1; /* Unsupported after delivery. */
}
static void IOServiceClose(unsigned c){assert(c==9);closes++;}
'''+actual+r'''
static void clear(void){matchingCalls=lookups=releases=opens=closes=lines=used=starts=payloadLen=0;payload[0]=0;}
static void *worker(void *p){pvr_darwin_sipf1_log(p);return NULL;}
int main(void){
clear();unsetenv("PVR_DARWIN_SIPF1_DUMP");pvr_darwin_sipf1_log("off");
const char *bad[]={"0","2","01","true",""};for(unsigned i=0;i<5;i++){setenv("PVR_DARWIN_SIPF1_DUMP",bad[i],1);pvr_darwin_sipf1_log("off");}assert(!matchingCalls);
setenv("PVR_DARWIN_SIPF1_DUMP","1",1);pvr_darwin_sipf1_log(NULL);pvr_darwin_sipf1_log("");assert(!matchingCalls);
mode=1;pvr_darwin_sipf1_log("a");assert(matchingCalls==1&&!lookups&&!opens);
clear();mode=2;pvr_darwin_sipf1_log("a");assert(lookups==1&&!releases&&!opens);
clear();mode=0;setenv("PVR_DARWIN_SERVICE","IOAcceleratorES",1);pvr_darwin_sipf1_log("abc\n\001");assert(!strcmp(payload,"abc??")&&lines==1&&releases==1&&!closes);
clear();char longText[301];memset(longText,'x',300);longText[300]=0;pvr_darwin_sipf1_log(longText);assert(lines==7&&payloadLen==255&&releases==1&&!closes);
clear();mode=3;unsetenv("PVR_DARWIN_SERVICE");pvr_darwin_sipf1_log("ok");assert(!strcmp(payload,"ok")&&closes==opens&&releases==1);
clear();mode=0;pthread_t t[8];for(unsigned i=0;i<8;i++)assert(!pthread_create(&t[i],NULL,worker,"parallel-line"));for(unsigned i=0;i<8;i++)assert(!pthread_join(t[i],NULL));assert(lines==8&&releases==8&&!starts&&!used&&payloadLen==8*13);
puts("PASS actual IOKit route: exact gate, null/empty, lookup failures, expected Unsupported, close/release, bounded 63-char parts, 255-char cap, sanitization and concurrent framing");}
'''
with tempfile.TemporaryDirectory(prefix='sipf1-route-') as t:
 d=Path(t);(d/'t.c').write_text(h)
 subprocess.run([os.environ.get('CC','cc'),'-std=gnu11','-Wall','-Wextra','-Werror','-pthread','-fsanitize=address,undefined','-fno-pie','-no-pie',str(d/'t.c'),'-o',str(d/'test')],check=True)
 subprocess.run([str(d/'test')],check=True,timeout=30)
 # Compile and execute the actual generic header fallback without IOKit.
 marker='static inline void pvr_darwin_sipf1_log(const char *text) { (void)text; }'
 assert marker in s_header
 (d/'generic.c').write_text(marker+'\nint main(void){pvr_darwin_sipf1_log("generic");return 0;}\n')
 subprocess.run([os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror',str(d/'generic.c'),'-o',str(d/'generic')],check=True)
 subprocess.run([str(d/'generic')],check=True,timeout=30)
 print('PASS actual generic header fallback; no IOKit dependency')
