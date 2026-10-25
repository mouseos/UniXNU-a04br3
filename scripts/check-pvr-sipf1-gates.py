#!/usr/bin/env python3
"""Actual separate encoding/routing gates and Metal preparation; no GPU access."""
from pathlib import Path
import os
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
patch=(ROOT/'patches/mesa/pvr-darwin-winsys.patch').read_text()
s='\n'.join(l[1:] for l in patch.splitlines() if l.startswith('+') and not l.startswith('+++'))
def function(source,sig):
 a=source.index(sig);b=source.index('{',a);e=b+1;d=1
 while d:d+=(source[e]=='{')-(source[e]=='}');e+=1
 return source[a:e]
h=r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#define PVR_HAS_FEATURE(d,f) ((d)->f)
#define PVR_GET_FEATURE_VALUE(d,f,z) ((d)->f)
struct pvr_device_info {struct{unsigned b,v,n,c;}ident;bool tile_size_16x16,simple_internal_parameter_format;unsigned simple_parameter_format_version;};
'''+function(s,'static bool pvr_darwin_tq_sipf1(')+'\n#ifdef PVR_WINSYS_DARWIN\n'+function(s,'static bool pvr_darwin_tq_clip_enabled(')+'\n#else\n#define pvr_darwin_tq_clip_enabled(d,n) false\n#endif\n'+r'''
static bool exact(const char *s){return s&&!strcmp(s,"1");}
static void flag(const char *name,const char *s){assert((s?setenv(name,s,1):unsetenv(name))==0);}
int main(){const char *values[]={nullptr,"","0","2","01","true","1"};unsigned cases=0;
for(auto encoding:values)for(auto legacy:values){flag("PVR_DARWIN_SIPF1",encoding);flag("PVR_DARWIN_TQ_CLIP",legacy);
pvr_device_info d={{22,40,54,30},true,true,1};bool e=false,r=false;
#ifdef PVR_WINSYS_DARWIN
 e=exact(encoding)||exact(legacy);r=exact(legacy);
#endif
assert(pvr_darwin_tq_sipf1(&d)==e);assert(pvr_darwin_tq_clip_enabled(&d,0)==r);assert(!pvr_darwin_tq_clip_enabled(&d,2));
unsigned *ids[]={&d.ident.b,&d.ident.v,&d.ident.n,&d.ident.c};for(auto id:ids){++*id;assert(!pvr_darwin_tq_sipf1(&d));assert(!pvr_darwin_tq_clip_enabled(&d,0));--*id;}
d.tile_size_16x16=false;assert(!pvr_darwin_tq_sipf1(&d));assert(!pvr_darwin_tq_clip_enabled(&d,0));d.tile_size_16x16=true;
d.simple_internal_parameter_format=false;assert(!pvr_darwin_tq_sipf1(&d));d.simple_internal_parameter_format=true;
d.simple_parameter_format_version=2;assert(!pvr_darwin_tq_sipf1(&d));assert(!pvr_darwin_tq_clip_enabled(&d,0));cases++;}
printf("PASS %u actual gate combinations + HW/feature/source-count rejection\n",cases);}
'''
board=(ROOT/'package/powervr/user/NativePowerVRMetalBoard.mm').read_text()
b=r'''
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <dlfcn.h>
using OpenFn=void(*)();using CloseFn=void(*)();using CallFn=void(*)();using MapFn=void(*)();using RetainFn=int(*)(unsigned);
struct{OpenFn open;CloseFn close;CallFn call;MapFn map;unsigned port;}gIOKit;
static constexpr unsigned kMetalOpenType=0x5056;
static void *fakeOpen(const char*,int){return nullptr;}
static void *fakeSymbol(void*,const char*){return nullptr;}
#define dlopen fakeOpen
#define dlsym fakeSymbol
'''+function(board,'extern "C" void native_metal_board_prepare(')+r'''
int main(int argc,char **argv){assert(argc==2);if(!strcmp(argv[1],"absent"))unsetenv("PVR_DARWIN_TQ_CLIP");else setenv("PVR_DARWIN_TQ_CLIP",argv[1],1);
setenv("PVR_DARWIN_SIPF1","0",1);native_metal_board_prepare(7);assert(gIOKit.port==7);assert(!strcmp(getenv("PVR_DARWIN_SIPF1"),"1"));
if(!strcmp(argv[1],"absent"))assert(!getenv("PVR_DARWIN_TQ_CLIP"));else assert(!strcmp(getenv("PVR_DARWIN_TQ_CLIP"),argv[1]));
setenv("PVR_DARWIN_SIPF1","2",1);native_metal_board_prepare(9);assert(gIOKit.port==7&&!strcmp(getenv("PVR_DARWIN_SIPF1"),"2"));puts("PASS actual Metal prepare enables encoding, preserves explicit routing, remains one-time");}
'''
with tempfile.TemporaryDirectory(prefix='sipf1-gates-') as t:
 p=Path(t);cc=[os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined','-fno-pie','-no-pie']
 for darwin in (False,True):
  (p/'g.cpp').write_text(h);subprocess.run(cc+(['-DPVR_WINSYS_DARWIN'] if darwin else [])+[str(p/'g.cpp'),'-o',str(p/'gate')],check=True)
  subprocess.run([str(p/'gate')],check=True,timeout=30)
 (p/'b.cpp').write_text(b);subprocess.run(cc+[str(p/'b.cpp'),'-o',str(p/'board')],check=True)
 for flag in ('absent','0','1','01'):subprocess.run([str(p/'board'),flag],check=True,timeout=30)
