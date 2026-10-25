#!/usr/bin/env python3
"""Execute actual owner-probe main with console/dup2 mocks; no device access."""
from pathlib import Path
import os
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[2]
s=(ROOT/'package/powervr/user/powervrprobed.c').read_text()
a=s.index('int main(int argc, char **argv)');b=s.index('{',a);e=b+1;d=1
while d:
 d+=(s[e]=='{')-(s[e]=='}');e+=1
actual=s[a:e].replace('int main(', 'int owner_main(',1)
h=r'''
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define POWERVRPROBED_VULKAN 1
#define POWERVRPROBED_VULKAN_LIBRARY "mock"
static int gConsole=-1;static unsigned gPass,gFail,opens,dups,io,vi,failures;static int consoleFd=5,dupFails;
static int fakeOpen(const char *p,int flags){assert(!strcmp(p,"/dev/console"));assert(flags==(O_WRONLY|O_NOCTTY|O_CLOEXEC));opens++;if(consoleFd<0)errno=EACCES;return consoleFd;}
static int fakeDup(int a,int b){assert(a==consoleFd&&b==STDERR_FILENO);assert(!io&&!vi);dups++;if(dupFails){errno=EBADF;return -1;}return b;}
static void probe_line(const char *fmt,...){if(strstr(fmt,"FAIL SIPF1 dump:"))failures++;}
static int ioStage(unsigned seconds){assert(seconds==180);assert(opens==1);io++;return 0;}
static int probe_vulkan(const char *s){assert(!strcmp(s,"mock"));assert(opens==1);vi++;return 0;}
static void usage(void){assert(0);}
#define open fakeOpen
#define dup2 fakeDup
'''+actual+r'''
int main(void){char *argv[]={"probe",NULL};const char *flags[]={NULL,"0","2","01","true","1"};unsigned cases=0;
for(unsigned f=0;f<6;f++)for(unsigned mode=0;mode<3;mode++){
 if(flags[f])setenv("PVR_DARWIN_SIPF1_DUMP",flags[f],1);else unsetenv("PVR_DARWIN_SIPF1_DUMP");
 opens=dups=io=vi=failures=0;consoleFd=mode==1?-1:5;dupFails=mode==2;
 int r=owner_main(1,argv);int enabled=f==5;int failed=enabled&&mode;
 assert(opens==1&&dups==(unsigned)(enabled&&mode!=1));assert((r!=0)==failed);
 assert(failures==(unsigned)failed&&io==(unsigned)!failed&&vi==(unsigned)!failed);cases++;}
printf("PASS %u actual main cases: exact dump gate, default stderr unchanged, redirect before tests, console/dup2 failures abort\\n",cases);}
'''
with tempfile.TemporaryDirectory(prefix='pvr-dump-stderr-') as t:
 p=Path(t);(p/'t.c').write_text(h)
 subprocess.run([os.environ.get('CC','cc'),'-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(p/'t.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,timeout=30)
