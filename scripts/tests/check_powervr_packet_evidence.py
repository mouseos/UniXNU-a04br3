#!/usr/bin/env python3
"""実packet/HWR関数と実FWIF型をASan/UBSanで検証する。GPU/SDK不要。"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PVR = ROOT / 'package/powervr'
JOBS = (PVR / 'NativePowerVRJobs.cpp').read_text()
FW = (PVR / 'NativePowerVR.cpp').read_text()


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


h = r'''
#include <cassert>
#include <cstdint>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "compat/native_powervr_linux_compat.h"
#include "linux/pvr_rogue_fwif.h"
#include "linux/pvr_rogue_fwif_client.h"
struct Lock {bool held=false;};
static Lock jobsLock; static bool fwccbHeld=false,vmHeld=false;
static unsigned logs,cleans,reads,commands,kicks;
static bool supplied=false,racing=false;static unsigned arg=0;
static std::string output;
void IOLockLock(Lock *p){assert(!p->held&&!fwccbHeld&&!vmHeld);p->held=true;}
void IOLockUnlock(Lock *p){assert(p->held);p->held=false;}
void IOLog(const char *fmt,...) __attribute__((format(printf,1,2)));
void IOLog(const char *fmt,...){assert(!jobsLock.held&&!fwccbHeld&&!vmHeld);++logs;
 char b[2048];va_list a;va_start(a,fmt);int n=vsnprintf(b,sizeof(b),fmt,a);va_end(a);
 assert(n>=0&&size_t(n)<sizeof(b));output+=b;}
#define native_powervr_log IOLog
bool PE_parse_boot_argn(const char *key,void *p,size_t size){assert(!strcmp(key,"a04br3-gpu-packet"));assert(size==4);memcpy(p,&arg,4);return supplied;}
static bool gPacketEnabled;
'''
h += function(FW, 'void packetDiagnosticInit()') + '\n'
h += function(FW, 'extern "C" int native_powervr_packet_enabled(') + '\n'
h += r'''
static rogue_fwif_hwrinfobuf hwBuffer;static rogue_fwif_hwrinfobuf *gHwrInfo=&hwBuffer;
void clean(const void *p,size_t n){assert(!jobsLock.held&&!fwccbHeld&&!vmHeld);assert(p==gHwrInfo&&n==sizeof(hwBuffer));
 if(++cleans==2&&racing)++gHwrInfo->hwr_counter;}
#define READ_ONCE_A04(x) (x)
'''
h += function(FW, 'extern "C" void native_powervr_packet_hwr(') + '\n'
h += r'''
constexpr uint32_t DRM_PVR_JOB_TYPE_GEOMETRY=0,DRM_PVR_JOB_TYPE_FRAGMENT=1,DRM_PVR_JOB_TYPE_TRANSFER_FRAG=3;
struct native_powervr_vm{};struct native_powervr_vm_context {native_powervr_vm *vm;};
struct Obj {uint32_t fw;};struct pvr_hwrt_data {Obj *fw_obj;};
struct pvr_cccb {uint32_t write_offset=0;};
struct pvr_queue{pvr_cccb cccb;uint32_t ctx_offset=0,seqno=0;struct {Obj *fw_obj;} timeline_ufo;};
struct pvr_context {uint32_t ctx_id,type,fw;native_powervr_vm_context *vm_ctx;};
struct Job {uint32_t id,type,fw_ccb_cmd_type,cmd_len,seqno; pvr_context *ctx;pvr_hwrt_data *hwrt;pvr_queue *queue;void *cmd;Job *paired;uint64_t fence=99;};
struct {Lock *lock=&jobsLock;int dev;} gJobs;
uint32_t pvr_context_get_fw_addr(const pvr_context *c){assert(jobsLock.held);return c->fw;}
void pvr_fw_object_get_fw_addr(Obj *p,uint32_t *v){assert(jobsLock.held);*v=p->fw;}
native_powervr_vm *native_powervr_vm_context_vm(native_powervr_vm_context *p){assert(jobsLock.held);++reads;return p->vm;}
uint32_t native_powervr_vm_fw_mem_ctx(native_powervr_vm *){assert(jobsLock.held);return 0xc0001000;}
uint64_t native_powervr_vm_page_table_root(native_powervr_vm *){assert(jobsLock.held);return 0x12345000;}
'''
start = JOBS.index('struct PacketRecord {')
end = JOBS.index('struct UfoList {', start)
h += JOBS[start:end]
h += r'''
struct UfoList {rogue_fwif_ufo ufo[32];uint32_t n;};struct HwrtTrack{};
constexpr uint32_t kMaxUfosPerJob=32;
void pvr_cccb_write_command_with_header(pvr_cccb *q,uint32_t op,uint32_t size,const void *,uint32_t id,uint32_t){
 assert(jobsLock.held);++commands;q->write_offset+=size+16;
 if(op==ROGUE_FWIF_CCB_CMD_TYPE_GEOM||op==ROGUE_FWIF_CCB_CMD_TYPE_FRAG||op==ROGUE_FWIF_CCB_CMD_TYPE_TQ_3D)
 assert(gPacketCount&&gPackets[gPacketCount-1].id==id);
}
void hwrtDeps(Job *,HwrtTrack *,UfoList &d){d.n=0;}
void hwrtUpdate(Job *,HwrtTrack *){}
void pvr_cccb_send_kccb_combined_kick(int *,pvr_cccb *,pvr_cccb *,uint32_t,uint32_t,pvr_hwrt_data *,bool){assert(jobsLock.held);++kicks;}
void pvr_cccb_send_kccb_kick(int *,pvr_cccb *,uint32_t,pvr_hwrt_data *){assert(jobsLock.held);++kicks;}
'''
h += function(JOBS, 'void submit_job_to_cccb(') + '\n'
h += 'void capturePte(Job *) {} // Separate PTE fixture covers this values-only hook.\nstatic uint64_t nowAbs(){ static uint64_t t=100;return ++t;}\n'
h += function(JOBS, 'void run_job(') + '\n'
h += r'''
static_assert(offsetof(rogue_fwif_cmd_frag,regs)==16,"actual FRAG ABI");
static_assert(offsetof(rogue_fwif_cmd_transfer,regs)==8,"actual TQ ABI");
static_assert(sizeof(((rogue_fwif_frag_regs*)0)->pbe_word)==8*3*8,"render PBE bound");
static_assert(sizeof(((rogue_fwif_transfer_regs*)0)->pbe_wordx_mrty)==9*8,"TQ PBE bound");
static_assert(offsetof(rogue_fwif_transfer_regs,event_pixel_pds_code)==48,"TQ code ABI");
static_assert(offsetof(rogue_fwif_transfer_regs,event_pixel_pds_data)==52,"TQ data ABI");
static_assert(offsetof(rogue_fwif_fwccb_cmd_context_reset_data,fault_address)==32,"reset ABI");
static_assert(offsetof(rogue_hwrinfo,hwr_data)==0,"pinned rogue_hwrinfo.hwr_data");
static_assert(offsetof(rogue_hwrinfo,cr_timer)==32,"pinned rogue_hwrinfo.cr_timer");
static_assert(offsetof(rogue_hwrinfo,os_timer)==40,"pinned rogue_hwrinfo.os_timer");
static_assert(offsetof(rogue_hwrinfo,frame_num)==48,"pinned rogue_hwrinfo.frame_num");
static_assert(offsetof(rogue_hwrinfo,pid)==52,"pinned rogue_hwrinfo.pid");
static_assert(offsetof(rogue_hwrinfo,active_hwrt_data)==56,"pinned rogue_hwrinfo.active_hwrt_data");
static_assert(offsetof(rogue_hwrinfo,hwr_number)==60,"pinned rogue_hwrinfo.hwr_number");
static_assert(offsetof(rogue_hwrinfo,event_status)==64,"pinned rogue_hwrinfo.event_status");
static_assert(offsetof(rogue_hwrinfo,hwr_recovery_flags)==68,"pinned rogue_hwrinfo.hwr_recovery_flags");
static_assert(offsetof(rogue_hwrinfo,hwr_type)==72,"pinned rogue_hwrinfo.hwr_type");
static_assert(offsetof(rogue_hwrinfo,dm)==76,"pinned rogue_hwrinfo.dm");
static_assert(offsetof(rogue_hwrinfo,core_id)==80,"pinned rogue_hwrinfo.core_id");
static_assert(offsetof(rogue_hwrinfo,cr_time_of_kick)==88,"pinned rogue_hwrinfo.cr_time_of_kick");
static_assert(offsetof(rogue_hwrinfo,cr_time_hw_reset_start)==96,"pinned rogue_hwrinfo.cr_time_hw_reset_start");
static_assert(offsetof(rogue_hwrinfo,cr_time_hw_reset_finish)==104,"pinned rogue_hwrinfo.cr_time_hw_reset_finish");
static_assert(offsetof(rogue_hwrinfo,cr_time_freelist_ready)==112,"pinned rogue_hwrinfo.cr_time_freelist_ready");
static_assert(offsetof(rogue_hwrinfo,reserved)==120,"pinned rogue_hwrinfo.reserved");
static_assert(offsetof(rogue_fwif_hwrinfobuf,hwr_info)==0,"pinned rogue_fwif_hwrinfobuf.hwr_info");
static_assert(offsetof(rogue_fwif_hwrinfobuf,hwr_counter)==2176,"pinned rogue_fwif_hwrinfobuf.hwr_counter");
static_assert(offsetof(rogue_fwif_hwrinfobuf,write_index)==2180,"pinned rogue_fwif_hwrinfobuf.write_index");
static_assert(offsetof(rogue_fwif_hwrinfobuf,dd_req_count)==2184,"pinned rogue_fwif_hwrinfobuf.dd_req_count");
static_assert(offsetof(rogue_fwif_hwrinfobuf,hwr_info_buf_flags)==2188,"pinned rogue_fwif_hwrinfobuf.hwr_info_buf_flags");
static_assert(offsetof(rogue_fwif_hwrinfobuf,hwr_dm_locked_up_count)==2192,"pinned rogue_fwif_hwrinfobuf.hwr_dm_locked_up_count");
static_assert(offsetof(rogue_fwif_hwrinfobuf,hwr_dm_overran_count)==2228,"pinned rogue_fwif_hwrinfobuf.hwr_dm_overran_count");
static_assert(offsetof(rogue_fwif_hwrinfobuf,hwr_dm_recovered_count)==2264,"pinned rogue_fwif_hwrinfobuf.hwr_dm_recovered_count");
static_assert(offsetof(rogue_fwif_hwrinfobuf,hwr_dm_false_detect_count)==2300,"pinned rogue_fwif_hwrinfobuf.hwr_dm_false_detect_count");
static_assert(offsetof(rogue_fwif_fwccb_cmd_context_reset_data,server_common_context_id)==0,"pinned rogue_fwif_fwccb_cmd_context_reset_data.server_common_context_id");
static_assert(offsetof(rogue_fwif_fwccb_cmd_context_reset_data,reset_reason)==4,"pinned rogue_fwif_fwccb_cmd_context_reset_data.reset_reason");
static_assert(offsetof(rogue_fwif_fwccb_cmd_context_reset_data,dm)==8,"pinned rogue_fwif_fwccb_cmd_context_reset_data.dm");
static_assert(offsetof(rogue_fwif_fwccb_cmd_context_reset_data,reset_job_ref)==12,"pinned rogue_fwif_fwccb_cmd_context_reset_data.reset_job_ref");
static_assert(offsetof(rogue_fwif_fwccb_cmd_context_reset_data,flags)==16,"pinned rogue_fwif_fwccb_cmd_context_reset_data.flags");
static_assert(offsetof(rogue_fwif_fwccb_cmd_context_reset_data,pc_address)==24,"pinned rogue_fwif_fwccb_cmd_context_reset_data.pc_address");
static_assert(offsetof(rogue_fwif_fwccb_cmd_context_reset_data,fault_address)==32,"pinned rogue_fwif_fwccb_cmd_context_reset_data.fault_address");
static_assert(sizeof(rogue_hwrinfo)==136,"pinned rogue_hwrinfo size");
static_assert(sizeof(rogue_fwif_hwrinfobuf)==2336,"pinned rogue_fwif_hwrinfobuf size");
static_assert(sizeof(rogue_fwif_fwccb_cmd_context_reset_data)==40,"pinned rogue_fwif_fwccb_cmd_context_reset_data size");
int main(int argc,char **argv){
 assert(argc==2);std::string which=argv[1];
 // Actual boot parser: absent,0,2,max reject; exactly1 accepts.
 for(unsigned v:{0u,1u,2u,~0u}){arg=v;supplied=false;packetDiagnosticInit();assert(!native_powervr_packet_enabled());
 supplied=true;packetDiagnosticInit();assert(bool(native_powervr_packet_enabled())==(v==1));}
 if(which=="off"){
 arg=0;packetDiagnosticInit();capturePacket(nullptr);drainPackets();native_powervr_packet_hwr(1,2,3,0,0,1,1);
 assert(!logs&&!reads&&!cleans&&!gPacketCount);puts("PASS off/no-op + exact bootarg gate");return 0;}
 arg=1;packetDiagnosticInit();
 if(which=="packets"){
 native_powervr_vm vm;native_powervr_vm_context vc{&vm};pvr_context ctx{1,0,0xc0002000,&vc};
 Obj obj{0xc0003000},ufo{0xc0004000};pvr_hwrt_data hw{&obj};pvr_queue qa,qb;qa.timeline_ufo.fw_obj=&ufo;qb.timeline_ufo.fw_obj=&ufo;
 qa.ctx_offset=16;qb.ctx_offset=32;
 rogue_fwif_cmd_geom geom{};rogue_fwif_cmd_frag frag{};rogue_fwif_cmd_transfer tq{};
 frag.regs.event_pixel_pds_info=0x11;frag.regs.event_pixel_pds_data=0x41000;frag.regs.pds_bgnd[2]=0x1234567810ULL;
 frag.regs.pbe_word[7][2]=0xabcdef; tq.regs.event_pixel_pds_code=0x42000;tq.regs.pbe_wordx_mrty[8]=0x987654;
 Job a{11,0,ROGUE_FWIF_CCB_CMD_TYPE_GEOM,sizeof(geom),0,&ctx,&hw,&qa,&geom,nullptr};
 Job b{12,1,ROGUE_FWIF_CCB_CMD_TYPE_FRAG,sizeof(frag),0,&ctx,&hw,&qb,&frag,&a};a.paired=&b;
 IOLockLock(&jobsLock);run_job(&a,nullptr,nullptr);IOLockUnlock(&jobsLock);
 assert(gPacketCount==2&&kicks==1&&commands==4&&!logs);
 assert(gPackets[0].paired==12&&gPackets[1].paired==11&&gPackets[1].validSize);
 assert(gPackets[1].queueFw==ctx.fw+32&&gPackets[1].hwrtFw==obj.fw&&gPackets[1].root==0x12345000);
 ctx.type=2;Job c{13,3,ROGUE_FWIF_CCB_CMD_TYPE_TQ_3D,sizeof(tq),0,&ctx,nullptr,&qb,&tq,nullptr};
 uint64_t measured=0;
 IOLockLock(&jobsLock);run_job(&c,nullptr,&measured);IOLockUnlock(&jobsLock);
 assert(measured==1);
 assert(gPackets[2].ctxType==2&&gPackets[2].regs.tq.event_pixel_pds_code==0x42000);
 // Invalid size never dereferences the deliberately invalid pointer.
 c.id=14;c.cmd_len=UINT32_MAX;c.cmd=(void*)1;
 IOLockLock(&jobsLock);capturePacket(&c);IOLockUnlock(&jobsLock);assert(!gPackets[3].validSize);
 // Fill the configured bound (the controlled four-clear/four-copy capture uses eight).
 for(unsigned i=4;i<kPacketRecords+1;++i){c.id=15+i;IOLockLock(&jobsLock);capturePacket(&c);IOLockUnlock(&jobsLock);}
 assert(gPacketCount==kPacketRecords);
 // Simulated retirement destroys every source field. Diagnostic contains no pointers.
 memset(&frag,0,sizeof(frag));memset(&tq,0,sizeof(tq));memset(&ctx,0,sizeof(ctx));a={};b={};c={};
 drainPackets();assert(gPacketPrinted==kPacketRecords&&output.find("abcdef")!=std::string::npos&&output.find("987654")!=std::string::npos);
 assert(output.find("41000")!=std::string::npos&&output.find("paired 11")!=std::string::npos);
 unsigned old=logs;drainPackets();assert(logs==old);
 // Exact length/null/unknown-type gates are exercised independently of the cap.
 ctx={1,0,0xc0002000,&vc};c={13,3,ROGUE_FWIF_CCB_CMD_TYPE_TQ_3D,sizeof(tq),0,&ctx,nullptr,&qb,&tq,nullptr};
 for(uint32_t length:{0u,uint32_t(sizeof(tq)-1),uint32_t(sizeof(tq)+1),UINT32_MAX}){
 gPacketCount=gPacketPrinted=0;c.cmd_len=length;c.cmd=(void*)1;
 IOLockLock(&jobsLock);capturePacket(&c);IOLockUnlock(&jobsLock);assert(!gPackets[0].validSize);}
 gPacketCount=0;c.cmd_len=sizeof(tq);c.cmd=nullptr;
 IOLockLock(&jobsLock);capturePacket(&c);IOLockUnlock(&jobsLock);assert(!gPackets[0].validSize);
 gPacketCount=0;c.type=99;c.cmd=(void*)1;
 IOLockLock(&jobsLock);capturePacket(&c);IOLockUnlock(&jobsLock);assert(!gPackets[0].validSize);
 printf("PASS actual paired run/submit + typed copy/retirement/cap/size bounds; FRAG %zu TQ %zu Packet %zu\n",sizeof(frag),sizeof(tq),sizeof(PacketRecord));return 0;}
 if(which=="missing")gHwrInfo=nullptr;
 else if(which!="empty"){
 hwBuffer.hwr_counter=20;hwBuffer.write_index=15;
 for(unsigned i=0;i<16;++i){auto &r=hwBuffer.hwr_info[i];r.hwr_number=i+1;r.cr_timer=i+100;r.dm=3;
 r.hwr_type=ROGUE_HWRTYPE_BIF0FAULT;r.hwr_data.bif_info.bif_req_status=0xdead0000+i;}
 auto &r=hwBuffer.hwr_info[15];r.hwr_number=20;r.hwr_type=ROGUE_HWRTYPE_MMUFAULT;r.hwr_data.mmu_info.mmu_status[1]=0xbeef;
 if(which=="firstzero"){hwBuffer={};hwBuffer.write_index=1;auto &z=hwBuffer.hwr_info[0];z.cr_timer=123;z.hwr_type=ROGUE_HWRTYPE_BIF0FAULT;z.hwr_data.bif_info.bif_req_status=0x1234;}
 if(which=="racing")racing=true;
 if(which=="wrap"){hwBuffer.hwr_counter=1;for(unsigned i=0;i<16;++i)hwBuffer.hwr_info[i].hwr_number=uint32_t(1-i);}
 }
 native_powervr_packet_hwr(1,12,3,0,0x12345000,1,which=="nonpf"?0:1);
 assert(output.find("packet reset ctx 1 job 12 dm 3 fault 0")!=std::string::npos);
 if(which=="missing")assert(output.find("unavailable")!=std::string::npos&&!cleans);
 else if(which=="empty"||which=="racing")assert(output.find("HWR slot")==std::string::npos);
 else {assert(output.find("HWR slot")!=std::string::npos);size_t count=0,pos=0;
 while((pos=output.find("HWR slot",pos))!=std::string::npos){++count;++pos;}assert(count==(which=="firstzero"?1u:4u));
 if(which=="firstzero")assert(output.find("num 0")!=std::string::npos&&output.find("1234")!=std::string::npos);
 if(which=="normal")assert(output.find("num 20")<output.find("num 15")&&output.find("beef")!=std::string::npos);}
 if(which=="nonpf")assert(output.find("reason 1 flags 0")!=std::string::npos);
 unsigned old=logs;native_powervr_packet_hwr(2,99,3,1,2,1,1);assert(logs==old);
 puts(("PASS HWR "+which+" first-reset cap/owned-buffer/unlocked logs").c_str());
}
'''

# Integration checks ensure these tested helpers are on the actual final-write and
# unlocked completion paths, not only callable in the standalone fixture.
submit = function(JOBS, 'void submit_job_to_cccb(')
assert submit.index('capturePacket(job);') < submit.index('// Submit job to FW')
completion = function(JOBS, 'void completionThread(')
assert completion.index('IOLockUnlock(gJobs.lock);') < completion.index('drainPackets();')
assert completion.index('IOLockUnlock(gJobs.lock);') < completion.index('native_powervr_packet_hwr(')
assert completion.index('native_powervr_packet_hwr(') < completion.index('drainPackets();')
assert completion.index('native_powervr_packet_hwr(') < completion.index('pvr_job_release(j);')
assert 'd->dm, d->fault_address, d->pc_address' in JOBS
assert 'if (notes[i].flags & ROGUE_FWIF_FWCCB_CMD_CONTEXT_RESET_FLAG_PF)' not in completion
assert 'notes[i].reason, notes[i].flags' in completion
assert function(FW, 'void native_powervr_boot(void)\n{').index('packetDiagnosticInit();') < function(FW, 'void native_powervr_boot(void)\n{').index('kernel_thread_start(')

with tempfile.TemporaryDirectory(prefix='powervr-packet-') as tmp:
    tmp = Path(tmp)
    # Real FWIF headers use the project's actual type/macro shim. Only external
    # Linux include shells are supplied, just as the native build supplies them.
    for f in (PVR / 'linux').glob('*.h'):
        for name in re.findall(r'#include <(linux/[^>]+)>', f.read_text()):
            q = tmp / name
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text('#pragma once\n')
    source, binary = tmp / 'fixture.cpp', tmp / 'fixture'
    source.write_text(h)
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-g',
                    '-I'+str(tmp), '-I'+str(PVR), str(source), '-o', str(binary)], check=True)
    for case in ('off', 'packets', 'normal', 'missing', 'empty', 'racing', 'wrap', 'firstzero', 'nonpf'):
        subprocess.run([str(binary), case], check=True, timeout=30)
print('PASS 9 actual-function ASan/UBSan scenarios + ABI/integration assertions; no device access')
