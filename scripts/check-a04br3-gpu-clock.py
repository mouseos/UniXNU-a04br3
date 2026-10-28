#!/usr/bin/env python3
"""Run real board clock helpers against MMIO mocks and the pre-change sequence.
Requires a normal checkout containing baseline ec583f9, c++ and ASan/UBSan.
No device access, vendor dump or local SDK is required.
"""
from pathlib import Path
import subprocess, hashlib, re, tempfile
root = Path(__file__).resolve().parents[1]
_tmp = tempfile.TemporaryDirectory(prefix='a04br3-clock-policy-')
out = Path(_tmp.name)
gpu = (root/'package/A04BR3Gpu.cpp').read_text()
fw = (root/'package/powervr/NativePowerVR.cpp').read_text()
def definition(s,sig):
 st=s.index(sig);b=s.index('{',st);e=b+1;n=1
 while n:n+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[st:e]
old=subprocess.check_output(['git','-C',str(root),'show','ec583f94e516d749553299e3614734b9d928ec33:package/A04BR3RoguePower.h'],text=True)
# Baseline must really be the pre-change crystal-only implementation.
assert 'a04_rgx_power_on_parent' not in old
(out/'old.h').write_text(old)
h=r'''
#include <cassert>
#include <cstdint>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <tuple>
using vm_offset_t=uintptr_t;using vm_size_t=size_t;
using u32=uint32_t;using u64=uint64_t;using u8=uint8_t;
#define INTEGER_BITS 7
#define GENMASK(h,l) ((uint32_t)((~0u << (l)) & (~0u >> (31-(h)))))
struct linux_data{int pcwbits;};struct mtk_clk_pll{linux_data *data;};
static bool pwrapAvailable=true;struct A04BR3PwrapGuard{explicit operator bool()const{return pwrapAvailable;}};
'''
# Independent arithmetic oracle for MT8167's 21-bit MMPLL PCW / 7 integer bits.
# Formula documented by MediaTek Linux clk-pll.c __mtk_pll_recalc_rate:
# ceil(ceil(parent * PCW / 2^14) / postdiv), including both roundups.
h += r'''
static unsigned long __mtk_pll_recalc_rate(mtk_clk_pll *, unsigned long parent,
                                         uint32_t pcw, unsigned postdiv) {
  const uint64_t vco = (uint64_t(parent) * pcw + 16383) / 16384;
  return (vco + postdiv - 1) / postdiv;
}
'''
h+='#include "'+str(root/'package/A04BR3RoguePower.h')+'"\nnamespace baseline {\n#undef A04BR3_ROGUE_POWER_H\n#include "'+str(out/'old.h')+'"\n}\n'
h+=r'''
using Event=std::tuple<bool,unsigned,unsigned,uint32_t>;
struct State {uint32_t r[4][1024]{};std::vector<Event> trace;unsigned delays=0;bool fail=false;};
static uint32_t readBank(void *ctx,unsigned bank,unsigned offset){auto &s=*(State*)ctx;uint32_t v=s.r[bank][offset/4];if(bank==A04_RGX_SPM&&(offset==0x60c||offset==0x610))v=s.fail?0:((1u<<25)|(1u<<24)|(1u<<4));s.trace.emplace_back(false,bank,offset,v);return v;}
static void writeBank(void *ctx,unsigned bank,unsigned offset,uint32_t v){auto &s=*(State*)ctx;s.trace.emplace_back(true,bank,offset,v);s.r[bank][offset/4]=v;}
static void delayBank(void *ctx,unsigned us){assert(us==10);++((State*)ctx)->delays;}
static State fresh(){State s;s.r[A04_RGX_TOP][0]=0x27209726;s.r[A04_RGX_TOP][1]=0x00a98852;s.r[A04_RGX_TOP][0x40/4]=0x553fffff;return s;}
static State state;
static unsigned fixtures;
static alignas(8) uint8_t top[4096],pll[4096],pwrap[4096],other[4096],core[65536];
static unsigned requested=380,maps,reads,writes,pmicReads,powerCalls,fwCalls,phase;
static bool present=true,pllMap=true,pwrapMap=true,init=true,failPmic=false,muxMismatch=false;
static unsigned unstableOffset,unstableSeen,requestSnapshot;
static uint32_t con0=0x11,con1=0x020e9d8a,power=5;
static uint16_t rail[4]={2,0x3001,0x4e,0x4e},reply;
static std::string logs;
static volatile uint8_t *gBank[4];
static uint64_t a04br3_gpu_bvnc;
volatile uint32_t a04br3_gpu_core_hz=26000000;
static int a04br3_gpu_power_error;
static bool PE_parse_boot_argn(const char *n,void *v,int size){assert(size==sizeof(unsigned));if(!strcmp(n,"a04br3-gpu")){*(unsigned*)v=1;return true;}if(!strcmp(n,"a04br3-gpu-clock-diagnostic")){*(unsigned*)v=requestSnapshot;return true;}assert(!strcmp(n,"a04br3-gpu-clock"));*(unsigned*)v=requested;return present;}
static void IOLog(const char *fmt,...){char t[1024];va_list a;va_start(a,fmt);vsnprintf(t,sizeof t,fmt,a);va_end(a);logs+=t;}
static void IODelay(unsigned us){assert(us==10);}
static vm_offset_t ml_io_map(vm_offset_t a,vm_size_t size){++maps;assert(size==4096||size==65536);if(a==0x10018000)return pllMap?(uintptr_t)pll:0;if(a==0x1000f000)return pwrapMap?(uintptr_t)pwrap:0;if(a==0x10000000)return (uintptr_t)top;if(a==0x13000000)return (uintptr_t)core;return (uintptr_t)other;}
static uint32_t clockDiagnosticRead(volatile uint8_t *base,unsigned off){++reads;if(base==top){assert(off==0||off==4);return 0;}if(base==pll){uint32_t v=off==0x160?con0:off==0x164?con1:power;assert(off==0x160||off==0x164||off==0x170);if(off==unstableOffset&&++unstableSeen==2)v^=1;return v;}assert(base==pwrap);if(off==0x98||off==0x9c)return init?1:0;assert(off==0xa4);return (1u<<21)|((phase==1?6u:0u)<<16)|reply;}
static void clockDiagnosticWrite(volatile uint8_t *base,unsigned off,uint32_t v){assert(base==pwrap);++writes;if(off==0xa0){assert(!(v&0x8000ffffu));assert(!failPmic);unsigned addr=(v>>16)*2;unsigned i=addr==0x30a?0:addr==0x30e?1:addr==0x312?2:3;assert(addr==0x30a||addr==0x30e||addr==0x312||addr==0x314);reply=rail[i];phase=1;++pmicReads;}else{assert(off==0xa8&&v==1);phase=2;}}
'''
# Use real waits and PMIC reader, changing only the injected failure result through a wrapper.
for sig in ['bool clockDiagnosticWait(','const char *clockDiagnosticPmicRead(']:h+=definition(gpu,sig)+'\n'
sel=definition(gpu,'unsigned selectClockParent(').replace('clockDiagnosticPmicRead(', 'injectedPmicRead(')
h+='static const char *injectedPmicRead(volatile uint8_t *p,unsigned a,uint16_t *v){if(failPmic)return "injected read failure";return clockDiagnosticPmicRead(p,a,v);}\n'+sel+'\n'
h+=r'''
static void clockDiagnosticSnapshot(volatile uint8_t *){assert(requestSnapshot==0);}
static uint32_t bankRead(void *,unsigned b,unsigned o){uint32_t v=readBank(&state,b,o);if(muxMismatch&&b==A04_RGX_TOP&&o==0x004)v^=1u<<8;return v;}
static void bankWrite(void *,unsigned b,unsigned o,uint32_t v){writeBank(&state,b,o,v);}
static void waitUs(void *,unsigned us){delayBank(&state,us);}
'''
h+=definition(fw,'static uint32_t coreClockHz()')+'\n'
assert 'rt->core_clock_speed = coreClockHz();' in fw and 'sys->initial_core_clock_speed = coreClockHz();' in fw
h+=r'''
static void native_powervr_boot(){++fwCalls;struct Runtime {uint32_t core_clock_speed,active_pm_latency_ms;bool active_pm_latency_persistant;} runtime{};struct Sys {uint32_t initial_core_clock_speed;} system{};auto *rt=&runtime;auto *sys=&system;
'''
h+=next(x.strip() for x in fw.splitlines() if 'rt->core_clock_speed = coreClockHz();' in x)+'\n'
h+=next(x.strip() for x in fw.splitlines() if 'sys->initial_core_clock_speed = coreClockHz();' in x)+'\n'
h+='assert(rt->core_clock_speed==a04br3_gpu_core_hz && sys->initial_core_clock_speed==a04br3_gpu_core_hz);}\n'
h+=definition(gpu,'void a04br3_gpu_init(void)\n{').replace('__asm__ volatile("dmb oshld" ::: "memory");','/* host-only load barrier omission */')+'\n'
h+=r'''
static void reset(){requested=380;present=pllMap=pwrapMap=init=true;failPmic=muxMismatch=false;maps=reads=writes=pmicReads=powerCalls=fwCalls=phase=unstableOffset=unstableSeen=requestSnapshot=0;con0=0x11;con1=0x020e9d8a;power=5;rail[0]=2;rail[1]=0x3001;rail[2]=rail[3]=0x4e;logs.clear();state=fresh();a04br3_gpu_core_hz=26000000;}
int main(){
 pwrapAvailable=false;uint16_t blocked=0;assert(clockDiagnosticPmicRead(pwrap,0x30a,&blocked));assert(writes==0);pwrapAvailable=true;
 // Independent oracle for the Linux two-roundup formula, across all divisors.
 linux_data data{21};mtk_clk_pll linuxPll{&data};
 const uint32_t pcws[]={0,1,0x40000,0x80000,0xc0000,0xe7000,0xe9d89,0xe9d8a,0xe9d8b,0xf0000,0x100000,0x1fffff};
 for(unsigned div=0;div<8;div++)for(uint32_t pcw:pcws){unsigned long expected=__mtk_pll_recalc_rate(&linuxPll,26000000,pcw,1u<<div);uint32_t got=a04_rgx_mmpll_hz(0x11,pcw|(div<<24),5,2,0x3001,0x4e,0x4e);assert(got==((expected>=253500000&&expected<=390000000)?expected:0));++fixtures;}
 assert(a04_rgx_mmpll_hz(0x11,0x020e9d8a,5,2,0x3001,0x4e,0x4e)==380000062);++fixtures;
 for(unsigned control:{0u,2u})for(unsigned selector:{0u,0x47u,0x48u,0x4eu,0x60u,0x61u,0x7fu}){uint32_t uv=700000+6250*selector;uint32_t got=a04_rgx_mmpll_hz(0x11,0x020e9d8a,5,control,0x3001,control?0:selector,control?selector:0);assert(got==((uv>=1150000&&uv<=1300000)?380000062u:0));++fixtures;}
 for(unsigned status:{0u,1u,0x2000u}){assert(!a04_rgx_mmpll_hz(0x11,0x020e9d8a,5,2,status,0x4e,0x4e));++fixtures;}
 for(unsigned p:{0u,2u,3u,7u}){assert(!a04_rgx_mmpll_hz(0x11,0x020e9d8a,p,2,0x3001,0x4e,0x4e));++fixtures;}
 assert(!a04_rgx_mmpll_hz(0x10,0x020e9d8a,5,2,0x3001,0x4e,0x4e));++fixtures;
 // Compare the complete real parent4 sequence with the previous header.
 State before=fresh(),after=fresh(),seven=fresh();baseline::a04_rgx_io oldIo{&before,readBank,writeBank,delayBank};a04_rgx_io io{&after,readBank,writeBank,delayBank};assert(!baseline::a04_rgx_power_on(&oldIo)&&!a04_rgx_power_on(&io));assert(before.trace==after.trace);++fixtures;
 a04_rgx_io io7{&seven,readBank,writeBank,delayBank};assert(!a04_rgx_power_on_parent(&io7,7));assert(seven.trace.size()==after.trace.size());unsigned different=0;for(unsigned i=0;i<seven.trace.size();i++)if(seven.trace[i]!=after.trace[i]){bool w;unsigned b,o;uint32_t v;std::tie(w,b,o,v)=seven.trace[i];assert(w&&b==A04_RGX_TOP&&o==0&&((v>>8)&7)==7);++different;}assert(different==1);++fixtures;
 assert(std::get<2>(seven.trace[0])==0xa0&&std::get<2>(seven.trace[1])==0x50);assert((seven.r[A04_RGX_TOP][0]&~(7u<<8))==(0x27209726u&~(7u<<8)));assert(((seven.r[A04_RGX_TOP][1]>>8)&63)==8);++fixtures;
 for(unsigned parent:{0u,2u,3u,5u,6u,8u}){State s=fresh();a04_rgx_io x{&s,readBank,writeBank,delayBank};assert(a04_rgx_power_on_parent(&x,parent)==6&&s.trace.empty());++fixtures;}
 State timeout=fresh();timeout.fail=true;a04_rgx_io x{&timeout,readBank,writeBank,delayBank};assert(a04_rgx_power_on_parent(&x,7)==1&&timeout.delays==10000);++fixtures;
 // Real selector: default-off no MMIO, valid stock state, unavailable/unstable/unsafe fallback.
 for(unsigned request:{0u,26u,379u,381u}){reset();requested=request;uint32_t hz=26000000;assert(selectClockParent(&hz)==4&&hz==26000000&&!maps&&!reads&&!writes);++fixtures;}
 reset();present=false;uint32_t hz=26000000;assert(selectClockParent(&hz)==4&&!maps&&!reads&&!writes);++fixtures;
 reset();hz=26000000;assert(selectClockParent(&hz)==7&&hz==380000062&&writes==8&&pmicReads==4);++fixtures;
 for(unsigned reason=0;reason<11;reason++){reset();if(reason==0)pllMap=false;if(reason==1)pwrapMap=false;if(reason==2)init=false;if(reason==3)failPmic=true;if(reason==4)con0=0x10;if(reason==5)power=7;if(reason==6)rail[1]=1;if(reason==7)rail[3]=0x47;if(reason==8)unstableOffset=0x160;if(reason==9)unstableOffset=0x164;if(reason==10)unstableOffset=0x170;hz=26000000;assert(selectClockParent(&hz)==4&&hz==26000000);++fixtures;}
 // Actual init publishes the frequency only after successful mux readback; then both FW fields see it.
 *(uint32_t*)(core+0x20)=0x0036001e;*(uint32_t*)(core+0x24)=0x00160028;
 reset();a04br3_gpu_init();assert(fwCalls==1&&a04br3_gpu_core_hz==380000062&&((state.r[A04_RGX_TOP][0]>>8)&7)==7);++fixtures;
 reset();requested=0;a04br3_gpu_init();assert(fwCalls==1&&a04br3_gpu_core_hz==26000000&&((state.r[A04_RGX_TOP][0]>>8)&7)==4&&!reads&&!writes);++fixtures;
 reset();rail[3]=0x47;a04br3_gpu_init();assert(fwCalls==1&&a04br3_gpu_core_hz==26000000&&((state.r[A04_RGX_TOP][0]>>8)&7)==4);++fixtures;
 reset();muxMismatch=true;a04br3_gpu_init();assert(!fwCalls&&a04br3_gpu_core_hz==26000000&&logs.find("mux readback mismatch")!=std::string::npos);++fixtures;
 reset();state.fail=true;a04br3_gpu_init();assert(!fwCalls&&a04br3_gpu_core_hz==26000000);++fixtures;
 printf("PASS %u fixtures: actual header, independent PLL arithmetic oracle, selector and gpu_init; parent4 trace unchanged; parent7 differs only mux field; FW fields match; unsafe/unstable/default-off/readback failure paths\n",fixtures);
}
'''
h=h.replace('static alignas(8)','alignas(8) static')
(out/'fixture.cpp').write_text(h)
print('GPU_sha256',hashlib.sha256(gpu.encode()).hexdigest(),flush=True)
subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-g',str(out/'fixture.cpp'),'-o',str(out/'fixture')],check=True)
subprocess.run([str(out/'fixture')],check=True)
