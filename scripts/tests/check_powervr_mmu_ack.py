#!/usr/bin/env python3
"""Actual device MMU ack helpers and submit loop, mocked FW/cache/clock only."""
from pathlib import Path
import subprocess,tempfile
R=Path(__file__).resolve().parents[2]
s=(R/'package/powervr/NativePowerVR.cpp').read_text()
def fun(sig):
 a=s.index(sig);e=s.index('{',a)+1;d=1
 while d:d+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[a:e]
helpers=fun('static int mmuAckStamp(')+fun('static bool mmuAckComplete(')
submit=fun('extern "C" int native_powervr_kccb_submit(').replace('__asm__ volatile("dsb sy" ::: "memory");','__asm__ volatile("" ::: "memory");')
pre=r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cerrno>
#include <cstdio>
#define ROGUE_FWIF_KCCB_CMD_MMUCACHE 7
#define ROGUE_FWIF_KCCB_CMD_KICK 8
#define ROGUE_FWIF_KCCB_CMD_COMBINED_GEOM_FRAG_KICK 9
#define ROGUE_CR_MTS_SCHEDULE 1
#define READ_ONCE_A04(x) (x)
struct rogue_fwif_kccb_cmd {uint32_t cmd_type;struct {struct {uint32_t cache_flags,mmu_cache_sync_fw_addr,mmu_cache_sync_update_value;}mmu_cache_data;}cmd_data;};
struct Ctl{uint32_t write_offset,read_offset,wrap_mask;};
struct Os{uint32_t kccb_cmds_executed;};
struct FW{struct {void*cpu;uint32_t fw_addr;}o;};
static Ctl ctl;static Os od;static uint32_t syncword;static FW fw;
static Ctl*gKctl=&ctl;static Os*gOsdata=&od;static FW*gMmuSync=&fw;
static rogue_fwif_kccb_cmd gKccb[8];static bool gFwStarted=true,gMmuAckEnabled,gKccbFastPoll;
static int delays,completeDelay;
static uint32_t gKccbExpected,gMmuAckLast;static int lock,gLock,mode,ticks,kicks,cacheSync;
static const uint32_t kKccbKick=1;
static void IOLockLock(int){assert(!lock);lock=1;}
static void IOLockUnlock(int){assert(lock);lock=0;}
static void clean(const void*p,size_t){if(p==&syncword)cacheSync++;}
static void wr32(uint32_t,uint32_t){assert(lock);kicks++;}
static void IODelay(unsigned n){assert(!lock&&n==10);delays++;if(mode==7&&delays==1)od.kccb_cmds_executed=gKccbExpected-1;if(delays==completeDelay)od.kccb_cmds_executed=gKccbExpected;}
static void IOSleep(unsigned n){assert(!lock&&n==1);ticks++;if(ticks==1){if(mode!=2&&mode!=4)od.kccb_cmds_executed=gKccbExpected;if(mode==2||mode==3)syncword=gMmuAckLast;if(mode==5)syncword=gMmuAckLast-1;if(mode==6)syncword=0;}if(ticks==2&&(mode==1||mode==5))syncword=gMmuAckLast;}
#define IOLog(...) ((void)0)
static void reset(bool ack,int m){ctl={0,0,7};od={0};syncword=0;fw={{&syncword,0xc0001000}};gMmuSync=&fw;gKctl=&ctl;gFwStarted=true;gKccbExpected=gMmuAckLast=0;lock=ticks=kicks=cacheSync=0;gMmuAckEnabled=ack;gKccbFastPoll=false;delays=completeDelay=0;mode=m;memset(gKccb,0,sizeof(gKccb));}
'''
main=r'''
int main(){
rogue_fwif_kccb_cmd caller={7,{{0x800,0xc0001000,0}}};
// Default and non-MMU keep caller bytes and require only executed counter.
reset(false,0);assert(!native_powervr_kccb_submit(&caller,3));assert(!memcmp(gKccb,&caller,sizeof(caller))&&!cacheSync&&ticks==1);
reset(true,0);auto other=caller;other.cmd_type=8;assert(!native_powervr_kccb_submit(&other,3));assert(!memcmp(gKccb,&other,sizeof(other))&&!cacheSync);
// Default-off KICK retains the original sleeping path.
reset(false,0);assert(!native_powervr_kccb_submit(&other,3)&&delays==0&&ticks==1);
// Explicit opt-in: an early KICK acknowledgement skips the 1ms sleep.
reset(false,0);gKccbFastPoll=true;completeDelay=2;assert(!native_powervr_kccb_submit(&other,3)&&delays==2&&ticks==0);
reset(false,0);gKccbFastPoll=true;other.cmd_type=9;completeDelay=1;assert(!native_powervr_kccb_submit(&other,3)&&delays==1&&ticks==0);
// An older command finishing cannot satisfy the newer cumulative target.
reset(false,7);gKccbFastPoll=true;gKccbExpected=10;od.kccb_cmds_executed=9;completeDelay=3;other.cmd_type=8;assert(!native_powervr_kccb_submit(&other,3)&&gKccbExpected==11&&delays==3&&ticks==0);
// After five 10us delays, no acknowledgement falls back to the original sleep.
reset(false,0);gKccbFastPoll=true;other.cmd_type=8;assert(!native_powervr_kccb_submit(&other,3)&&delays==5&&ticks==1);
// Completion during the fifth delay is detected by the first normal read.
reset(false,0);gKccbFastPoll=true;completeDelay=5;assert(!native_powervr_kccb_submit(&other,3)&&delays==5&&ticks==0);
// If the target never executes, fast polling must still return timeout.
reset(false,2);gKccbFastPoll=true;assert(native_powervr_kccb_submit(&other,3)==-ETIMEDOUT&&delays==5&&ticks==4);
// Other commands and zero-timeout do not gain a spin phase.
reset(true,3);gKccbFastPoll=true;completeDelay=1;assert(!native_powervr_kccb_submit(&caller,3)&&delays==0&&ticks==1);
reset(false,0);gKccbFastPoll=true;other.cmd_type=10;assert(!native_powervr_kccb_submit(&other,3)&&delays==0&&ticks==1);other.cmd_type=8;
reset(false,0);gKccbFastPoll=true;completeDelay=1;assert(native_powervr_kccb_submit(&other,0)==-ETIMEDOUT&&delays==0&&ticks==1);
// Counter alone, sync alone, neither: all timeout. Late sync and both: success.
for(int m=0;m<=4;m++){reset(true,m);auto before=caller;int err=native_powervr_kccb_submit(&caller,3);assert(err==((m==1||m==3)?0:-ETIMEDOUT));assert(!memcmp(&caller,&before,sizeof(caller)));assert(gKccb[0].cmd_data.mmu_cache_data.mmu_cache_sync_update_value==1);assert(!lock&&kicks==1&&cacheSync>=2);if(m==1)assert(ticks==2);if(m==3)assert(ticks==1);}
reset(true,3);caller.cmd_data.mmu_cache_data.mmu_cache_sync_fw_addr++;assert(native_powervr_kccb_submit(&caller,3)==-EINVAL);assert(!kicks&&!ctl.write_offset&&!gMmuAckLast&&!gKccbExpected&&!lock);caller.cmd_data.mmu_cache_data.mmu_cache_sync_fw_addr--;
reset(true,3);gMmuSync=nullptr;assert(native_powervr_kccb_submit(&caller,3)==-ENODEV&&!kicks&&!lock);
// An older command's late sync cannot satisfy this token.
reset(true,5);gMmuAckLast=1;assert(!native_powervr_kccb_submit(&caller,3));assert(ticks==2&&syncword==2);
// A full ring rejects before allocating a token.
reset(true,3);ctl.read_offset=1;assert(native_powervr_kccb_submit(&caller,3)==-EBUSY&&!gMmuAckLast&&!kicks&&!lock);
// Wrap skips zero; completion signed differences allow a later token, never initial zero.
reset(true,3);gMmuAckLast=0xffffffff;syncword=0xffffffff;assert(!native_powervr_kccb_submit(&caller,3));assert(gMmuAckLast==1&&syncword==1);
// Full submit loop must time out if FW sync becomes/stays zero at FFFFFFFF.
reset(true,6);gMmuAckLast=0xfffffffe;syncword=0xfffffffe;
assert(native_powervr_kccb_submit(&caller,3)==-ETIMEDOUT);
assert(gMmuAckLast==0xffffffff&&syncword==0&&ticks==4&&!lock);
assert(!mmuAckComplete(1,1,0,0xffffffff));assert(mmuAckComplete(0,0xffffffff,1,0xffffffff));assert(!mmuAckComplete(0,1,1,1));assert(!mmuAckComplete(1,1,0,1));
uint32_t last=7,token=9;auto local=caller;assert(mmuAckStamp(true,&local,0xc0001000,0,7,&last,&token)==-EBUSY&&last==7&&!token);last=0x7fffffff;assert(mmuAckStamp(true,&local,0xc0001000,0,0xffffffff,&last,&token)==-EBUSY);
// Two enqueues use unique values; caller stays zero.
reset(true,3);assert(!native_powervr_kccb_submit(&caller,3));ticks=0;assert(!native_powervr_kccb_submit(&caller,3));assert(gKccb[0].cmd_data.mmu_cache_data.mmu_cache_sync_update_value==1&&gKccb[1].cmd_data.mmu_cache_data.mmu_cache_sync_update_value==2&&caller.cmd_data.mmu_cache_data.mmu_cache_sync_update_value==0);
puts("PASS actual MMU ack stamp+submit: default/non-MMU, both conditions, late sync, timeout, wrap/zero/half-range/pending bound, bad addr, local copy, locked enqueue/unlocked wait and cache maintenance; ASan/UBSan");}
'''
with tempfile.TemporaryDirectory(prefix='mmu-ack-') as t:
 p=Path(t);(p/'t.cpp').write_text(pre+helpers+submit+main)
 subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(p/'t.cpp'),'-o',str(p/'test')],check=True);subprocess.run([str(p/'test')],check=True)
 # Actual startup flag expression, compiled separately.
 a=s.index('  unsigned mmuAck = 0;');b=s.index('\n',s.index('  gMmuAckEnabled =',a))
 flag=s[a:b]
 (p/'flag.cpp').write_text('#include <cassert>\n#include <cstring>\nstatic bool present,gMmuAckEnabled;static unsigned argument;static bool PE_parse_boot_argn(const char*n,void*p,unsigned long){assert(!strcmp(n,"a04br3-mmu-ack"));*static_cast<unsigned*>(p)=argument;return present;}\nstatic void init(){'+flag+'}\nint main(){for(unsigned i=0;i<4;i++)for(int p=0;p<2;p++){argument=i;present=p;init();assert(gMmuAckEnabled==(p&&i==1));}}\n')
 subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(p/'flag.cpp'),'-o',str(p/'flag')],check=True);subprocess.run([str(p/'flag')],check=True)

print('PASS actual MMU startup policy: absent/0/1/2/3; no GPU execution')
