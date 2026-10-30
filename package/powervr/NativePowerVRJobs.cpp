// SPDX-License-Identifier: MIT
// NativePowerVR job layer (native_powervr_jobs.h): render, compute and transfer contexts, free lists, HWRT datasets, job
// submission and completion for the user-client layer.
//
// Written after Linux drivers/gpu/drm/imagination (torvalds/linux 734bef548a96e8774d6eef5dfa2026c6edc4e80b; pvr_job.c,
// pvr_queue.c, pvr_fw.c, pvr_ccb.c, pvr_device.c, pvr_drv.c, "GPL-2.0-only OR MIT", used under MIT, Copyright Imagination
// Technologies Ltd.). The object code is Linux's own, unchanged, in package/powervr/kernel: pvr_context.c, pvr_cccb.c,
// pvr_free_list.c, pvr_hwrt.c, pvr_stream.c, pvr_stream_defs.c and pvr_dump.c, through the Linux API shim
// (compat/native_powervr_linux_jobs.h) and the pvr_device stand-in (stub/native_powervr_kernel.h). This file provides
// what they call (the FW object, GEM, VM, KCCB/FWCCB and queue functions) and replaces the two Linux files that depend on
// drm_sched and dma_fence, pvr_queue.c and pvr_job.c. Each function carries the name of the Linux one it stands for.
//
// a04br3 differences:
//  - No drm_sched. A job is written to its queue's client CCB (CCCB) and kicked when it is submitted; every dependency
//    is a firmware wait on another queue's timeline UFO (pvr_queue_submit_job_to_cccb() writes the same FENCE_PR
//    commands for the dependencies Linux finds UFO-backed). The dependencies are those Linux gets for a Mesa submission
//    without sync objects (NativePowerVRABI.h version 1): one queue runs its jobs in order; a job on an HWRT data set
//    waits as the dma_resv of its FW object makes it on Linux (geometry writes it, so it waits for every earlier job on
//    it; fragment reads it, so it waits for the last geometry job); a geometry job followed by a fragment job of the same
//    context and HWRT in the same call is paired and sent with one combined kick (pvr_jobs_link_geom_frag()), the
//    geometry -> fragment sync object of Mesa being implied by the ABI.
//  - Completion is the timeline UFO reaching the job's seqno, polled by a kernel thread every 1 ms while jobs are queued
//    (Linux: pvr_queue_signal_done_fences() on the FWCCB interrupt). The same thread consumes the FWCCB
//    (pvr_fwccb_process()) and applies the drm_sched timeout (SCHED_TIMEOUT_PERIOD): a job that has not completed by
//    then fails and its context becomes faulty, as pvr_queue_start() does after a reset.
//  - A context reset notification that names a guilty context (lockup, overrun or page fault) fails the job it names
//    and makes the context faulty. Linux only prints it (pvr_dump_context_reset_notification(), which runs here too) and
//    finds the hang through the timeout; the ABI reports failed jobs through WaitFence.
//  - Linux maps every FW object write-combined for the CPU (PVR_BO_FW_FLAGS_DEVICE_UNCACHED, pvr_fw_object_vmap()) and
//    its C code performs no cache maintenance. The firmware heap of NativePowerVR.cpp is mapped cached, so each FW object
//    gets a write-combined alias of its pages (the Linux arm64 dma_alloc_coherent() arrangement: the cached alias is
//    cleaned and invalidated once and not written through again). Objects are cache-line aligned, so no cached access
//    to a neighbouring object shares a line with them.
//  - The KCCB is NativePowerVR.cpp's, which waits for every command to execute (there is no slot reservation and the
//    return slot is not reported: pvr_fw_structure_cleanup() never sees CLEANUP_BUSY).
//  - The handles are the user-client layer's: it passes resolved objects, and the Linux functions that look a handle up
//    in the pvr_file (pvr_vm_context_lookup(), pvr_free_list_lookup()) find them under the same handle in this file's
//    pvr_file for the duration of the call.
#include <IOKit/IOLib.h>
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <kern/thread.h>
#include <string.h>
#include <sys/errno.h>
#include "compat/native_powervr_linux_compat.h"
#include "linux/pvr_rogue_fwif.h"
#include "linux/pvr_rogue_fwif_client.h"
#include "compat/native_powervr_linux_kernel.h"
#include "stub/native_powervr_kernel.h"
extern "C" {
#include "kernel/pvr_cccb.h"
#include "kernel/pvr_context.h"
#include "kernel/pvr_dump.h"
#include "kernel/pvr_free_list.h"
#include "kernel/pvr_hwrt.h"
#include "kernel/pvr_stream.h"
#include "kernel/pvr_stream_defs.h"
}
#include "native_powervr_internal.h"
#include "native_powervr_jobs.h"
#include "native_powervr_pte.h"
#include "NativePowerVRABI.h"
#include "NativePowerVRIoTrace.h"
#include "NativePowerVRIoAggregate.h"
#include "NativePowerVRSubmitStage.h"
#include "NativePowerVRWaitSeries.h"
#include "NativePowerVRWaitReason.h"
#undef min
#undef max

extern "C" {
uint64_t mach_absolute_time(void);
void nanoseconds_to_absolutetime(uint64_t nanoseconds, uint64_t *result);
void absolutetime_to_nanoseconds(uint64_t absolute, uint64_t *result);
int proc_selfpid(void);
void proc_selfname(char *, int);
kern_return_t kernel_thread_start(void (*)(void *, wait_result_t), void *, thread_t *);
void thread_deallocate(thread_t);
void thread_terminate_self(void);
}

// struct pvr_fw_object of the stub (stub/native_powervr_kernel.h), NativePowerVR.cpp's definition token for token.
struct pvr_fw_object { native_powervr_fw_obj o; };

namespace {
constexpr uint32_t kMaxDeadlineMs = 30000;                  // pvr_queue.c MAX_DEADLINE_MS
constexpr uint32_t kSchedTimeoutMs = 2 * kMaxDeadlineMs;    // pvr_queue.c SCHED_TIMEOUT_PERIOD
constexpr uint32_t kCccbSizeLog2 = 15;                      // pvr_queue.c CTX_{COMPUTE,FRAG,GEOM,TRANSFER}_CCCB_SIZE_LOG2
constexpr uint32_t kKccbTimeoutMs = 1000;
constexpr uint32_t kCacheLine = 64;                         // Cortex-A53 L1/L2 line
constexpr uint32_t kHwrtReaders = 8;                        // reader queues remembered per HWRT data
constexpr uint32_t kMaxUfosPerJob = 1 + kHwrtReaders;       // a geometry job waits for one writer and every reader
constexpr uint32_t kFailedFences = 64;                      // bounded late-reset attribution history
constexpr uint32_t kFailureRanges = 128;                    // bounded exact failure intervals per file
constexpr uint32_t kResetNotes = 16;

// A write-combined kernel mapping of physical memory.
struct WcMap {
  IOMemoryDescriptor *md;
  IOMemoryMap *map;
  uint8_t *cpu;
};

bool wcMap(uint64_t phys, uint64_t size, WcMap &m)
{
  const uint64_t mask = uint64_t(PAGE_MASK);
  const uint64_t base = phys & ~mask, end = (phys + size + mask) & ~mask;
  m = {};
  m.md = IOMemoryDescriptor::withPhysicalAddress(IOPhysicalAddress(base), IOByteCount(end - base), kIODirectionInOut);
  if (!m.md) return false;
  m.map = m.md->createMappingInTask(kernel_task, 0, kIOMapAnywhere | kIOMapWriteCombineCache);
  if (!m.map) {
    m.md->release();
    m.md = nullptr;
    return false;
  }
  m.cpu = reinterpret_cast<uint8_t *>(m.map->getVirtualAddress()) + (phys - base);
  return true;
}

void wcUnmap(WcMap &m)
{
  if (m.map) m.map->release();
  if (m.md) m.md->release();
  m = {};
}

// A FW object: its piece of the firmware heap and the write-combined view the C code uses.
struct JobsFwObject {
  pvr_fw_object base;
  WcMap wc;
};
JobsFwObject *jobsFwObject(pvr_fw_object *o) { return reinterpret_cast<JobsFwObject *>(o); }

// A GEM object of the C code: kernel pages for a free list (pvr_gem_object_create()), or a user BO found in a VM
// context (pvr_vm_find_gem_object()).
struct JobsGem {
  pvr_gem_object gem;
  native_powervr_bo *bo;              // user BO, or null
  IOBufferMemoryDescriptor *mem;      // kernel pages
  sg_table sgt;
  size_t size;
};
JobsGem *jobsGem(pvr_gem_object *g) { return reinterpret_cast<JobsGem *>(g); }

struct Job;

// A dependency: queue and seqno of its timeline.
struct Dep {
  pvr_queue *queue;
  uint32_t seqno;
};

// What the dma_resv of an HWRT data's FW object holds on Linux: the last writer (geometry) and the readers (fragment)
// since then, one seqno per queue.
struct HwrtTrack {
  pvr_hwrt_data *hwrt;
  Dep write;
  Dep reads[kHwrtReaders];
  uint32_t nreads;
};

struct ResetNote {
  uint32_t ctx_id, job_ref, reason, flags, dm;
  uint64_t fault_address, pc_address;
};

void applyResets(const ResetNote *notes, uint32_t count);

struct JobsDevice {
  pvr_device dev;
  IOLock *lock;          // queued jobs, CCCB writes, HWRT tracking, files, fences
  IOLock *fwccbLock;     // pvr_dev->fwccb.lock
  list_head jobs;        // queued jobs in submission order
  list_head files;
  HwrtTrack *tracks;
  uint32_t ntracks, captracks;
  WcMap fwccbCtlMap, fwccbMap;
  rogue_fwif_ccb_ctl *fwccbCtl;
  rogue_fwif_fwccb_cmd *fwccb;
  uint32_t fwccbSlots;
  ResetNote resets[kResetNotes];
  uint32_t resetCount;   // pending error updates, under fwccbLock
  ResetNote resetReports[kResetNotes];
  uint32_t resetReportCount; // bounded diagnostic copies; independent of error delivery
  uint32_t fwccbActive; // callbacks outside fwccbLock; success must wait for all of them
  uint64_t timeoutAbs;
};
JobsDevice gJobs;
volatile int32_t gJobsState;   // 0 not set up, 1 being set up, 2 ready
}  // namespace

// struct pvr_queue (pvr_queue.h) without the scheduler, entity and fence contexts.
struct pvr_queue {
  enum drm_pvr_job_type type;
  uint32_t ctx_offset;
  pvr_context *ctx;
  uint64_t callstack_addr;
  pvr_cccb cccb;
  pvr_fw_object *reg_state_obj;
  struct {
    pvr_fw_object *fw_obj;
    uint32_t *value;
  } timeline_ufo;
  uint64_t generation; // host-only queue incarnation; never reused by a later context
  uint32_t seqno;   // job_fence_ctx.seqno: the last done-fence seqno handed out
  bool killed;      // pvr_queue_kill()
};

namespace {
uint64_t gQueueGeneration;
struct RetiredFence {
  uint64_t fence, generation;
  uint32_t ctx, job, dm;
  bool eligible;
};
}

// Sorted, non-overlapping failure intervals; accessed only under gJobs.lock.
struct FailedFenceRange {
  uint64_t first, last;
};

// The job part of struct pvr_file (native_powervr_jobs.h).
struct native_powervr_jobs_file {
  pvr_file pf;
  drm_file df;
  list_head link;    // gJobs.files
  list_head jobs;    // this file's queued jobs
  uint64_t nextFence;
  FailedFenceRange failed[kFailureRanges];
  uint32_t failedCount;
  uint64_t failureUncertainThrough; // exhausted interval storage: older retired fences fail closed
  RetiredFence retired[kFailedFences];
  uint32_t retiredNext;
};

// A context of the user-client layer: the context and the handle pvr_context_create() gave it in this file's pvr_file.
struct native_powervr_context {
  native_powervr_jobs_file *file;
  uint32_t handle;
  pvr_context *ctx;
};

namespace {
// struct pvr_job without the drm_sched job.
struct Job {
  list_head dev_link;    // gJobs.jobs, or a retirement list
  list_head file_link;   // file->jobs
  native_powervr_jobs_file *file;   // null once the file is gone
  uint64_t fence;
  enum drm_pvr_job_type type;
  pvr_context *ctx;
  pvr_hwrt_data *hwrt;
  pvr_queue *queue;
  uint32_t id;
  uint32_t fw_ccb_cmd_type;
  void *cmd;
  uint32_t cmd_len;
  uint32_t seqno;        // done_fence->seqno
  Job *paired;           // paired_job, during the submission only
  uint64_t kicked;
  bool failed, timed_out;
  native_powervr_host_sync *hosts;
  bool hosts_completed;   // protected by gJobs.lock; success cannot precede the post-GPU cache maintenance
  native_powervr_pte_snapshot *pte;
  native_powervr_pte_record *pre_pte;
  bool pte_requested;
};

// First two prepared transfers only; actual sequence/fence is attached at kick.
// gJobs.lock protects the retained values and completion state.
native_powervr_pte_record gPtes[2];
uint32_t gPteCount;
bool gPteDone[2], gPtePrinted;
uint32_t gPtePrepared; // atomic, reserve at most two preparation/capture attempts globally
uint64_t gPteSubmission; // atomic diagnostic submission number

bool reservePte()
{
  uint32_t n = __atomic_load_n(&gPtePrepared, __ATOMIC_RELAXED);
  while (n < 2)
    if (__atomic_compare_exchange_n(&gPtePrepared, &n, n + 1, false, __ATOMIC_RELAXED, __ATOMIC_RELAXED)) return true;
  return false;
}

void capturePte(Job *j)
{
  if (!j->pte_requested || gPteCount == 2) return;
  auto &r = gPtes[gPteCount++];
  r = j->pre_pte ? *j->pre_pte : native_powervr_pte_record{};
  if (!j->pre_pte) for (auto &t : r.target) t.status = 5;
  r.job = j->id; r.seqno = j->seqno; r.ctx = j->ctx->ctx_id; r.fence = j->fence;
  // Values-only copy. Absolutely no VM/map/pmap operations under gJobs.lock.
}

void pteCompleted(const Job *j)
{
  for (uint32_t i = 0; i < gPteCount; ++i)
    if (gPtes[i].ctx == j->ctx->ctx_id && gPtes[i].seqno == j->seqno && gPtes[i].fence == j->fence)
      gPteDone[i] = true;
}

// Completion-thread only, after both sampled jobs complete or a failure is seen.
// Snapshot values under the lock, then format/print outside every VM/pmap/jobs lock.
void drainPtes(bool failed)
{
  if (!__atomic_load_n(&gPtePrepared, __ATOMIC_RELAXED)) return;
  native_powervr_pte_record records[2];
  IOLockLock(gJobs.lock);
  const uint32_t count = gPteCount;
  const bool ready = count == 2 && gPteDone[0] && gPteDone[1];
  if (!count || gPtePrinted || (!ready && !failed)) { IOLockUnlock(gJobs.lock); return; }
  memcpy(records, gPtes, count * sizeof(records[0]));
  gPtePrinted = true;
  IOLockUnlock(gJobs.lock);
  for (uint32_t i = 0; i < count; ++i) {
    const auto &r = records[i];
    if (i == 0) {
      const auto &p = r.first_bo;
      native_powervr_log("powervr: first BO policy v1 ready %u initialized %u enabled %u supplied %u request %u allocationOptions %x descriptorPresent %u descriptorFlags %x size %llu\n",
        p.ready, p.initialized, p.enabled, p.supplied, p.requested, p.allocation_options,
        p.descriptor_present, p.descriptor_flags, (unsigned long long)p.size);
    }
    native_powervr_log("powervr: PTE pre-submit %llu index %u job %u ctx %u seq %u fence %llu fixed-NOP-probes (not decoded targets)\n",
                       (unsigned long long)r.submission, r.index, r.job, r.ctx, r.seqno, (unsigned long long)r.fence);
    for (unsigned n = 0; n < NATIVE_PVR_PTE_TARGETS; ++n) {
      const auto &t = r.target[n];
      native_powervr_log("powervr: PTE target %u gpu %llx status %u map %llx+%llx boOff %llx sgPA %llx options %x\n",
        n, (unsigned long long)t.gpu, t.status, (unsigned long long)t.mapping_va,
        (unsigned long long)t.mapping_size, (unsigned long long)t.bo_offset,
        (unsigned long long)t.expected_pa, t.map_options);
      const native_powervr_pte_alias *aliases[3] = {&t.user, &t.kernel, &t.physmap};
      for (unsigned a = 0; a < 3; ++a) {
        const auto &v = *aliases[a];
        native_powervr_log("powervr: PTE alias %u va %llx pa %llx entry %llx root %llx MAIR %llx status %u level %u attr %u SH %u\n",
          a, (unsigned long long)v.va, (unsigned long long)v.pa, (unsigned long long)v.entry,
          (unsigned long long)v.root, (unsigned long long)v.mair, v.status, v.level, v.attr, v.sh);
      }
    }
  }
}

// Values only: records remain valid after the job, queue, BO and context retire.
struct PacketRecord {
  uint32_t id, ctx, ctxType, type, opcode, cmdLen, seqno, paired;
  uint32_t queueFw, queueWrite, hwrtFw, vmFw, cmdFlags;
  uint64_t root, fence;
  bool validSize;
  union { rogue_fwif_frag_regs frag; rogue_fwif_transfer_regs tq; } regs;
};
// Four UI mip clears are followed by four copies; retain both halves.
// Still bounded and gated by the default-off a04br3-gpu-packet option.
// The Vulkan probe consumes eight packets before backboardd starts. Keep a
// bounded window that also includes the first compositor submissions.
constexpr uint32_t kPacketRecords = 32;
PacketRecord gPackets[kPacketRecords];
uint32_t gPacketCount, gPacketPrinted; // gJobs.lock
static_assert(sizeof(((rogue_fwif_frag_regs *)nullptr)->pbe_word) == 8 * 3 * sizeof(uint64_t), "FRAG PBE bounds");
static_assert(sizeof(((rogue_fwif_transfer_regs *)nullptr)->pbe_wordx_mrty) == 9 * sizeof(uint64_t), "TQ PBE bounds");

// Called immediately before the final job command is copied to its CCCB.
// No logs, allocations, VM lookup or firmware commands while gJobs.lock is held.
void capturePacket(const Job *job)
{
  if (!native_powervr_packet_enabled() || gPacketCount >= kPacketRecords) return;
  PacketRecord &r = gPackets[gPacketCount++];
  r = {};
  r.id = job->id; r.ctx = job->ctx->ctx_id; r.ctxType = job->ctx->type;
  r.type = job->type; r.opcode = job->fw_ccb_cmd_type; r.cmdLen = job->cmd_len;
  r.seqno = job->seqno; r.fence = job->fence; r.paired = job->paired ? job->paired->id : 0;
  r.queueFw = pvr_context_get_fw_addr(job->ctx) + job->queue->ctx_offset;
  r.queueWrite = job->queue->cccb.write_offset;
  auto *vm = native_powervr_vm_context_vm(reinterpret_cast<native_powervr_vm_context *>(job->ctx->vm_ctx));
  r.vmFw = native_powervr_vm_fw_mem_ctx(vm); r.root = native_powervr_vm_page_table_root(vm);
  if (job->hwrt) pvr_fw_object_get_fw_addr(job->hwrt->fw_obj, &r.hwrtFw);
  if (job->cmd && job->type == DRM_PVR_JOB_TYPE_FRAGMENT && job->cmd_len == sizeof(rogue_fwif_cmd_frag)) {
    r.regs.frag = static_cast<const rogue_fwif_cmd_frag *>(job->cmd)->regs;
    r.cmdFlags = static_cast<const rogue_fwif_cmd_frag *>(job->cmd)->flags;
    r.validSize = true;
  } else if (job->cmd && job->type == DRM_PVR_JOB_TYPE_TRANSFER_FRAG && job->cmd_len == sizeof(rogue_fwif_cmd_transfer)) {
    r.regs.tq = static_cast<const rogue_fwif_cmd_transfer *>(job->cmd)->regs;
    r.cmdFlags = static_cast<const rogue_fwif_cmd_transfer *>(job->cmd)->flags;
    r.validSize = true;
  }
}

void printPacket(const PacketRecord &r)
{
  native_powervr_log("powervr: packet job %u ctx %u ctxType %u jobType %u opcode %x len %u seq %u paired %u "
                     "queue %x write %u hwrt %x vm %x root %llx typed %u flags %x fence %llu (evidence, not a fix)\n",
                     r.id, r.ctx, r.ctxType, r.type, r.opcode, r.cmdLen, r.seqno, r.paired,
                     r.queueFw, r.queueWrite, r.hwrtFw, r.vmFw, (unsigned long long)r.root, unsigned(r.validSize),
                     r.cmdFlags, (unsigned long long)r.fence);
  if (!r.validSize) return;
  if (r.type == DRM_PVR_JOB_TYPE_TRANSFER_FRAG) {
    const rogue_fwif_transfer_regs &t = r.regs.tq;
    native_powervr_log("powervr: packet TQ ABI regsOff %u regsSize %u infoOff %u codeOff %u dataOff %u pbeOff %u\n",
      unsigned(offsetof(rogue_fwif_cmd_transfer, regs)), unsigned(sizeof(t)),
      unsigned(offsetof(rogue_fwif_transfer_regs, event_pixel_pds_info)),
      unsigned(offsetof(rogue_fwif_transfer_regs, event_pixel_pds_code)),
      unsigned(offsetof(rogue_fwif_transfer_regs, event_pixel_pds_data)),
      unsigned(offsetof(rogue_fwif_transfer_regs, pbe_wordx_mrty)));
    native_powervr_log("powervr: packet TQ bg %llx %llx size %llx mtile %llx pds info/code/data %x %x %x "
                       "ctl %x aa %x bgval %x out %x render %x rgn %x mtileSize %x origin %x\n",
      (unsigned long long)t.pds_bgnd0_base, (unsigned long long)t.pds_bgnd1_base,
      (unsigned long long)t.pds_bgnd3_sizeinfo, (unsigned long long)t.isp_mtile_base,
      t.event_pixel_pds_info, t.event_pixel_pds_code, t.event_pixel_pds_data, t.isp_ctl, t.isp_aa,
      t.isp_bgobjvals, t.usc_pixel_output_ctrl, t.isp_render, t.isp_rgn, t.isp_mtile_size, t.isp_render_origin);
    native_powervr_log("powervr: packet TQ clear %x %x %x %x\n", t.usc_clear_register0,
      t.usc_clear_register1, t.usc_clear_register2, t.usc_clear_register3);
    for (uint32_t i = 0; i < 3; ++i)
      native_powervr_log("powervr: packet TQ pbe %u %llx %llx %llx\n", i,
        (unsigned long long)t.pbe_wordx_mrty[3*i], (unsigned long long)t.pbe_wordx_mrty[3*i+1],
        (unsigned long long)t.pbe_wordx_mrty[3*i+2]);
  } else {
    const rogue_fwif_frag_regs &f = r.regs.frag;
    native_powervr_log("powervr: packet FRAG ABI regsOff %u regsSize %u infoOff %u dataOff %u pbeOff %u\n",
      unsigned(offsetof(rogue_fwif_cmd_frag, regs)), unsigned(sizeof(f)),
      unsigned(offsetof(rogue_fwif_frag_regs, event_pixel_pds_info)),
      unsigned(offsetof(rogue_fwif_frag_regs, event_pixel_pds_data)), unsigned(offsetof(rogue_fwif_frag_regs, pbe_word)));
    native_powervr_log("powervr: packet FRAG pds info/data %x %x ctl %x aa %x bgval %x out %x "
                       "scissor %llx dbias %llx zls %llx zload %llx stencil %llx\n",
      f.event_pixel_pds_info, f.event_pixel_pds_data, f.isp_ctl, f.isp_aa, f.isp_bgobjvals,
      f.usc_pixel_output_ctrl, (unsigned long long)f.isp_scissor_base, (unsigned long long)f.isp_dbias_base,
      (unsigned long long)f.isp_zlsctl, (unsigned long long)f.isp_zload_store_base,
      (unsigned long long)f.isp_stencil_load_store_base);
    for (uint32_t i = 0; i < 3; ++i)
      native_powervr_log("powervr: packet FRAG bg %u %llx pr %llx\n", i,
        (unsigned long long)f.pds_bgnd[i], (unsigned long long)f.pds_pr_bgnd[i]);
    for (uint32_t i = 0; i < 8; ++i)
      native_powervr_log("powervr: packet FRAG pbe %u %llx %llx %llx\n", i,
        (unsigned long long)f.pbe_word[i][0], (unsigned long long)f.pbe_word[i][1], (unsigned long long)f.pbe_word[i][2]);
  }
}

// Completion-thread only. Each record is copied under the jobs lock, then printed
// after unlocking. Nothing references a retired Job or context. IDs can be reused;
// the bounded records are not asserted to match a later reset solely by job ID.
void drainPackets()
{
  if (!native_powervr_packet_enabled()) return;
  for (;;) {
    PacketRecord r;
    IOLockLock(gJobs.lock);
    if (gPacketPrinted == gPacketCount) { IOLockUnlock(gJobs.lock); break; }
    r = gPackets[gPacketPrinted++];
    IOLockUnlock(gJobs.lock);
    printPacket(r);
  }
}

struct UfoList {
  rogue_fwif_ufo ufo[kMaxUfosPerJob];
  pvr_queue *queue[kMaxUfosPerJob];
  uint32_t n;
};

// Best-effort cumulative wall intervals. Reports are deliberately non-transactional:
// inflight counts may be transiently inconsistent with totals on other CPUs.
enum PerfStage { PerfLock, PerfSpace, PerfBo, PerfKick, PerfKccb, PerfHwrt, PerfSubmit, PerfIoctlLock, PerfIoctlCall, PerfWaitFence, PerfWaitFenceBackboard, PerfStages };
struct PerfCounter { uint64_t entered, finished, ticks; };
PerfCounter gSubmitPerf[PerfStages];
unsigned gSubmitPerfEnabled;
uint64_t gWaitFenceResult[2][3];
NativeIoTrace gNativeIoTrace;
NativeWaitSeries gNativeWaitSeries;
NativeWaitReasonLimit gNativeWaitReason;
unsigned gWaitMonitorStarted;
unsigned gIoLiveEnabled;
NativeIoAggregate gNativeIoAggregate;
NativeSubmitStage gSubmitStage;
unsigned gSubmitStageEnabled;
uint64_t nowAbs() { return mach_absolute_time(); }
uint64_t perfBegin(PerfStage s)
{
  if (!__atomic_load_n(&gSubmitPerfEnabled, __ATOMIC_RELAXED)) return 0;
  __atomic_fetch_add(&gSubmitPerf[s].entered, 1ULL, __ATOMIC_RELAXED);
  return nowAbs();
}
void perfEnd(PerfStage s, uint64_t start)
{
  if (!start) return;
  __atomic_fetch_add(&gSubmitPerf[s].ticks, nowAbs() - start, __ATOMIC_RELAXED);
  __atomic_fetch_add(&gSubmitPerf[s].finished, 1ULL, __ATOMIC_RELAXED);
}

bool queueReached(pvr_queue *q, uint32_t seqno) { return int32_t(READ_ONCE(*q->timeline_ufo.value) - seqno) >= 0; }
bool depDone(const Dep &d) { return !d.queue || queueReached(d.queue, d.seqno); }
}  // namespace

static void native_wait_monitor(void *, wait_result_t)
{
  unsigned aggregateBusy=~0u,aggregateReportBusy=~0u,aggregateFull=~0u,aggregateRetired=~0u;
  unsigned stageBusy=~0u,stageReportBusy=~0u,stageFull=~0u;
  for (;;) {
    IOSleep(250);
    if (!__atomic_load_n(&gNativeWaitSeries.enabled,__ATOMIC_ACQUIRE) || gNativeWaitSeries.mode<4) break;
    NativeWaitActive active[2]={};
    unsigned count=native_wait_active(&gNativeWaitSeries,mach_absolute_time(),active);
    for (unsigned i=0;i<count;i++) {
      uint64_t begin=0,now=0,sum=0;
      absolutetime_to_nanoseconds(active[i].pendingAt,&begin);
      absolutetime_to_nanoseconds(active[i].now,&now);
      absolutetime_to_nanoseconds(active[i].sum,&sum);
      IOLog("a04br3 gpu wait active: clock=mach_absolute_ns pid=%u tid=%llu fence=%llu series=%llu pending_ns=%llu now_ns=%llu completed_ns=%llu count=%u errors=%u\n",
        active[i].pid,(unsigned long long)active[i].tid,(unsigned long long)active[i].fence,(unsigned long long)active[i].serial,
        (unsigned long long)begin,(unsigned long long)now,(unsigned long long)sum,active[i].count,active[i].errors);
    }
    if (__atomic_load_n(&gSubmitStageEnabled,__ATOMIC_ACQUIRE)) {
      NativeSubmitRow rows[2]={};
      unsigned n=native_submit_stage_take(&gSubmitStage,rows);
      for (unsigned i=0;i<n;i++) {
        const auto &r=rows[i]; uint64_t first=0,last=0,ns[NativeSubmitStages]={};
        absolutetime_to_nanoseconds(r.sample.first,&first); absolutetime_to_nanoseconds(r.sample.last,&last);
        for(unsigned k=0;k<NativeSubmitStages;k++) absolutetime_to_nanoseconds(r.sample.ticks[k],&ns[k]);
        IOLog("a04br3 gpu submit stage: pid=%u tid=%llu first_ns=%llu last_ns=%llu calls=%llu success=%llu failed=%llu total=%llu create=%llu pte=%llu lock=%llu space=%llu relock=%llu bo=%llu kick=%llu kccb=%llu busy=%u report_busy=%u full=%u nested=1\n",
          r.sample.pid,(unsigned long long)r.sample.tid,(unsigned long long)first,(unsigned long long)last,(unsigned long long)r.count,(unsigned long long)r.succeeded,(unsigned long long)r.failed,
          (unsigned long long)ns[0],(unsigned long long)ns[1],(unsigned long long)ns[2],(unsigned long long)ns[3],
          (unsigned long long)ns[4],(unsigned long long)ns[5],(unsigned long long)ns[6],(unsigned long long)ns[7],(unsigned long long)ns[8],
          __atomic_load_n(&gSubmitStage.busy,__ATOMIC_RELAXED),__atomic_load_n(&gSubmitStage.reportBusy,__ATOMIC_RELAXED),__atomic_load_n(&gSubmitStage.full,__ATOMIC_RELAXED));
      }
      const unsigned busy=__atomic_load_n(&gSubmitStage.busy,__ATOMIC_RELAXED);
      const unsigned reportBusy=__atomic_load_n(&gSubmitStage.reportBusy,__ATOMIC_RELAXED);
      const unsigned full=__atomic_load_n(&gSubmitStage.full,__ATOMIC_RELAXED);
      if (busy!=stageBusy || reportBusy!=stageReportBusy || full!=stageFull) {
        IOLog("a04br3 gpu submit stage loss: busy=%u report_busy=%u full=%u cumulative=1\n",busy,reportBusy,full);
        stageBusy=busy;stageReportBusy=reportBusy;stageFull=full;
      }
    }
    if (__atomic_load_n(&gIoLiveEnabled,__ATOMIC_ACQUIRE)) {
      NativeIoAggregateRow rows[2]={};
      unsigned n=native_io_aggregate_take(&gNativeIoAggregate,rows);
      for (unsigned i=0;i<n;i++) {
        const auto &r=rows[i];uint64_t first=0,last=0,ns[3]={};
        absolutetime_to_nanoseconds(r.first,&first);absolutetime_to_nanoseconds(r.last,&last);
        for (unsigned k=0;k<3;k++) absolutetime_to_nanoseconds(r.ticks[k],&ns[k]);
        IOLog("a04br3 gpu io aggregate: pid=%u tid=%llu series=%llu first_ns=%llu last_ns=%llu wait=%llu/%llu/%llu lock=%llu/%llu/%llu call=%llu/%llu/%llu busy_record=%u busy_report=%u full=%u retired=%u\n",
          r.pid,(unsigned long long)r.tid,(unsigned long long)r.serial,(unsigned long long)first,(unsigned long long)last,
          (unsigned long long)r.count[0],(unsigned long long)ns[0],(unsigned long long)r.errors[0],
          (unsigned long long)r.count[1],(unsigned long long)ns[1],(unsigned long long)r.errors[1],
          (unsigned long long)r.count[2],(unsigned long long)ns[2],(unsigned long long)r.errors[2],
          __atomic_load_n(&gNativeIoAggregate.busyRecord,__ATOMIC_RELAXED),__atomic_load_n(&gNativeIoAggregate.busyReport,__ATOMIC_RELAXED),__atomic_load_n(&gNativeIoAggregate.full,__ATOMIC_RELAXED),__atomic_load_n(&gNativeIoAggregate.retired,__ATOMIC_RELAXED));
      }
      const unsigned busy=__atomic_load_n(&gNativeIoAggregate.busyRecord,__ATOMIC_RELAXED);
      const unsigned reportBusy=__atomic_load_n(&gNativeIoAggregate.busyReport,__ATOMIC_RELAXED);
      const unsigned full=__atomic_load_n(&gNativeIoAggregate.full,__ATOMIC_RELAXED);
      const unsigned retired=__atomic_load_n(&gNativeIoAggregate.retired,__ATOMIC_RELAXED);
      if (busy!=aggregateBusy || reportBusy!=aggregateReportBusy || full!=aggregateFull || retired!=aggregateRetired) {
        IOLog("a04br3 gpu io aggregate loss: busy_record=%u busy_report=%u full=%u retired=%u cumulative=1\n",busy,reportBusy,full,retired);
        aggregateBusy=busy;aggregateReportBusy=reportBusy;aggregateFull=full;aggregateRetired=retired;
      }
    }
  }
  __atomic_store_n(&gWaitMonitorStarted,0u,__ATOMIC_RELEASE);
  thread_terminate_self();
}

// Requires the existing mode-6 monitor; no worker, timestamps or records by default.
extern "C" void native_powervr_submit_stage_enable(unsigned enabled, unsigned ioMode)
{
  __atomic_store_n(&gSubmitStageEnabled,enabled==1 && ioMode==6,__ATOMIC_RELEASE);
  if (enabled==1 && ioMode!=6) IOLog("a04br3 gpu submit stage: requires a04br3-gio=6\n");
}
extern "C" void native_powervr_io_trace_enable(unsigned enabled)
{
  if (enabled >= 1 && enabled <= 6) {
    uint64_t threshold = 0;
    nanoseconds_to_absolutetime(2000000ull, &threshold);
    gNativeIoTrace.threshold_ticks = threshold ? threshold : 1;
    uint64_t longThreshold = 0;
    nanoseconds_to_absolutetime(1000000000ull, &longThreshold);
    gNativeIoTrace.immediate_threshold_ticks = longThreshold ? longThreshold : 1;
    uint64_t sumThreshold=0,window=0,stale=0,pendingLease=0,activeThreshold=0;
    nanoseconds_to_absolutetime(enabled>=4?250000000ull:1000000000ull,&sumThreshold);
    nanoseconds_to_absolutetime(enabled>=4?1000000000ull:10000000000ull,&window);
    nanoseconds_to_absolutetime(5000000000ull,&stale);
    if (enabled==6) gNativeIoAggregate.staleTicks=stale?stale:1;
    nanoseconds_to_absolutetime(65000000000ull,&pendingLease);
    nanoseconds_to_absolutetime(500000000ull,&activeThreshold);
    gNativeWaitSeries.threshold=sumThreshold?sumThreshold:1;gNativeWaitSeries.windowTicks=window?window:1;
    gNativeWaitSeries.staleTicks=stale?stale:1;
    gNativeWaitSeries.pendingLeaseTicks=pendingLease?pendingLease:1;
    gNativeWaitSeries.activeThreshold=activeThreshold?activeThreshold:1;gNativeWaitSeries.mode=enabled==6?5:enabled;
    if (enabled==5 || enabled==6) {
      uint64_t shortWait=0,spacing=0;
      nanoseconds_to_absolutetime(50000000ull,&shortWait);
      nanoseconds_to_absolutetime(500000000ull,&spacing);
      gNativeWaitReason.minTicks=shortWait?shortWait:1;gNativeWaitReason.perFenceTicks=spacing?spacing:1;
      gNativeWaitReason.windowTicks=window?window:1;
    }
  }
  __atomic_store_n(&gIoLiveEnabled,enabled==6,__ATOMIC_RELEASE);
  __atomic_store_n(&gNativeWaitSeries.enabled,enabled == 3 || enabled == 4 || enabled == 5 || enabled == 6,__ATOMIC_RELEASE);
  __atomic_store_n(&gNativeIoTrace.immediate_enabled, enabled == 2 || enabled == 3 || enabled == 4 || enabled == 5 || enabled == 6, __ATOMIC_RELEASE);
  __atomic_store_n(&gNativeIoTrace.enabled, enabled >= 1 && enabled <= 6, __ATOMIC_RELEASE);
  if (enabled==4 || enabled==5 || enabled==6) {
    unsigned expected=0;
    if (__atomic_compare_exchange_n(&gWaitMonitorStarted,&expected,1u,false,__ATOMIC_ACQ_REL,__ATOMIC_RELAXED)) {
      thread_t thread=nullptr;
      if (kernel_thread_start(native_wait_monitor,nullptr,&thread)==KERN_SUCCESS) thread_deallocate(thread);
      else {__atomic_store_n(&gWaitMonitorStarted,0u,__ATOMIC_RELEASE);IOLog("a04br3 gpu wait active: monitor unavailable\n");}
    }
  }
}
extern "C" unsigned native_powervr_io_trace_enabled(void)
{
  return __atomic_load_n(&gNativeIoTrace.enabled, __ATOMIC_RELAXED);
}
extern "C" unsigned native_powervr_wait_series_enabled(void)
{
  return __atomic_load_n(&gNativeWaitSeries.enabled,__ATOMIC_ACQUIRE);
}
extern "C" void native_powervr_wait_series_begin(unsigned pid,uint64_t tid,uint64_t fence,uint64_t owner)
{
  uint64_t at=mach_absolute_time();
  uint64_t serial=0;
  if (native_wait_begin(&gNativeWaitSeries,pid,tid,fence,owner,at,&serial)) {
    uint64_t ns=0;absolutetime_to_nanoseconds(at,&ns);
    IOLog("a04br3 gpu wait begin: clock=mach_absolute_ns pid=%u tid=%llu fence=%llu series=%llu at_ns=%llu\n",
          pid,(unsigned long long)tid,(unsigned long long)fence,(unsigned long long)serial,(unsigned long long)ns);
  }
}
extern "C" void native_powervr_io_trace_record(uint64_t start, uint64_t end, uint64_t tid,
                                               uint64_t fence, unsigned pid, unsigned kind, int result,uint64_t owner)
{
  if (!start || end < start) return;
  NativeIoRecord record = {start, end, tid, fence, pid, kind, result};
  if (__atomic_load_n(&gIoLiveEnabled,__ATOMIC_ACQUIRE)) native_io_aggregate_record(&gNativeIoAggregate,record);
  else native_io_record(&gNativeIoTrace, record);
  NativeWaitResult series={};
  if (kind==1 && native_wait_end(&gNativeWaitSeries,pid,tid,fence,owner,start,end,result,result!=-ETIMEDOUT,&series)) {
    uint64_t firstNs=0,lastNs=0,sumNs=0;
    absolutetime_to_nanoseconds(series.first,&firstNs);absolutetime_to_nanoseconds(series.last,&lastNs);
    absolutetime_to_nanoseconds(series.sum,&sumNs);
    IOLog("a04br3 gpu wait series: clock=mach_absolute_ns pid=%u tid=%llu fence=%llu series=%llu start_ns=%llu end_ns=%llu sum_ns=%llu count=%u errors=%u result=%d\n",
          series.pid,(unsigned long long)series.tid,(unsigned long long)series.fence,(unsigned long long)series.serial,
          (unsigned long long)firstNs,(unsigned long long)lastNs,(unsigned long long)sumNs,series.count,series.errors,series.result);
  }
  if (native_io_immediate_claim(&gNativeIoTrace, record)) {
    uint64_t startNs=0, endNs=0;
    absolutetime_to_nanoseconds(start, &startNs);
    absolutetime_to_nanoseconds(end, &endNs);
    IOLog("a04br3 gpu io immediate: clock=mach_absolute_ns pid=%u tid=%llu kind=%u start_ns=%llu end_ns=%llu fence=%llu result=%d\n",
          pid,(unsigned long long)tid,kind,(unsigned long long)startNs,(unsigned long long)endNs,
          (unsigned long long)fence,result);
  }
}
extern "C" void native_powervr_io_trace_report(void)
{
  if (!native_powervr_io_trace_enabled()) return;
  for (unsigned i=0; i<128; i++) {
    NativeIoRecord r;
    if (!native_io_take(&gNativeIoTrace, i, &r)) continue;
    uint64_t startNs=0, endNs=0;
    absolutetime_to_nanoseconds(r.start, &startNs);
    absolutetime_to_nanoseconds(r.end, &endNs);
    IOLog("a04br3 gpu io trace: pid=%u tid=%llu kind=%u start_ns=%llu end_ns=%llu fence=%llu result=%d\n",
          r.pid, (unsigned long long)r.tid, r.kind, (unsigned long long)startNs,
          (unsigned long long)endNs, (unsigned long long)r.fence, r.result);
  }
  uint64_t thresholdNs=0;
  absolutetime_to_nanoseconds(gNativeIoTrace.threshold_ticks,&thresholdNs);
  for (unsigned k=0; k<3; ++k) {
    const uint64_t count=__atomic_load_n(&gNativeIoTrace.short_count[k],__ATOMIC_RELAXED);
    const uint64_t ticks=__atomic_load_n(&gNativeIoTrace.short_ticks[k],__ATOMIC_RELAXED);
    uint64_t ns=0; absolutetime_to_nanoseconds(ticks,&ns);
    IOLog("a04br3 gpu io trace: short kind=%u count=%llu ns=%llu threshold_ns=%llu cumulative=1 non_atomic=1\n",
          k+1,(unsigned long long)count,(unsigned long long)ns,(unsigned long long)thresholdNs);
  }
  IOLog("a04br3 gpu io trace: dropped=%llu (cumulative)\n",
        (unsigned long long)__atomic_load_n(&gNativeIoTrace.dropped,__ATOMIC_RELAXED));
  if (__atomic_load_n(&gNativeIoTrace.immediate_enabled,__ATOMIC_RELAXED))
    IOLog("a04br3 gpu io immediate: claims=%u skipped=%u cap=8\n",
          __atomic_load_n(&gNativeIoTrace.immediate_count,__ATOMIC_RELAXED),
          __atomic_load_n(&gNativeIoTrace.immediate_skipped,__ATOMIC_RELAXED));
  if (__atomic_load_n(&gNativeWaitSeries.enabled,__ATOMIC_RELAXED))
    IOLog("a04br3 gpu wait series: busy=%u full=%u late=%u evicted=%u expired_pending=%u suppressed_begin=%u suppressed_series=%u suppressed_active=%u\n",
      __atomic_load_n(&gNativeWaitSeries.busy,__ATOMIC_RELAXED),__atomic_load_n(&gNativeWaitSeries.full,__ATOMIC_RELAXED),
      __atomic_load_n(&gNativeWaitSeries.late,__ATOMIC_RELAXED),__atomic_load_n(&gNativeWaitSeries.evicted,__ATOMIC_RELAXED),
      __atomic_load_n(&gNativeWaitSeries.expiredPending,__ATOMIC_RELAXED),
      __atomic_load_n(&gNativeWaitSeries.suppressedBegin,__ATOMIC_RELAXED),__atomic_load_n(&gNativeWaitSeries.suppressedSeries,__ATOMIC_RELAXED),
      __atomic_load_n(&gNativeWaitSeries.suppressedActive,__ATOMIC_RELAXED));
}

extern "C" void native_powervr_submit_perf_enable(unsigned enabled)
{
  __atomic_store_n(&gSubmitPerfEnabled, enabled == 1, __ATOMIC_RELAXED);
}
// Entry points used by the user-client for the submit ioctl only. Stable IDs are
// local to these two translation units, not part of the user ABI.
extern "C" uint64_t native_powervr_submit_perf_ioctl_begin(unsigned stage)
{
  return stage == 0 ? perfBegin(PerfIoctlLock) : stage == 1 ? perfBegin(PerfIoctlCall) : 0;
}
extern "C" void native_powervr_submit_perf_ioctl_end(unsigned stage, uint64_t start)
{
  if (stage == 0) perfEnd(PerfIoctlLock, start);
  else if (stage == 1) perfEnd(PerfIoctlCall, start);
}
// WaitFence is outside the connection lock. Both clocks include descheduling;
// per-name classification is diagnostic only and does not identify a frame.
extern "C" unsigned native_powervr_wait_perf_enabled(void)
{
  return __atomic_load_n(&gSubmitPerfEnabled, __ATOMIC_RELAXED) == 1;
}
extern "C" uint64_t native_powervr_wait_perf_begin(unsigned backboard)
{
  return perfBegin(backboard ? PerfWaitFenceBackboard : PerfWaitFence);
}
extern "C" void native_powervr_wait_perf_end(unsigned backboard, uint64_t start, int result)
{
  if (!start) return;
  perfEnd(backboard ? PerfWaitFenceBackboard : PerfWaitFence, start);
  const unsigned bucket = result == 0 ? 0 : result == -ETIMEDOUT ? 1 : 2;
  __atomic_fetch_add(&gWaitFenceResult[backboard ? 1 : 0][bucket], 1ULL, __ATOMIC_RELAXED);
}
extern "C" void native_powervr_submit_perf_report(void)
{
  if (!__atomic_load_n(&gSubmitPerfEnabled, __ATOMIC_RELAXED)) return;
  const char *names[PerfStages] = {"lock", "space", "bo", "kick", "kccb", "hwrt", "submit", "ioctl_lock", "ioctl_call", "wait_fence_other", "wait_fence_backboard"};
  for (unsigned i = 0; i < PerfStages; ++i) {
    const uint64_t entered = __atomic_load_n(&gSubmitPerf[i].entered, __ATOMIC_RELAXED);
    const uint64_t finished = __atomic_load_n(&gSubmitPerf[i].finished, __ATOMIC_RELAXED);
    const uint64_t ticks = __atomic_load_n(&gSubmitPerf[i].ticks, __ATOMIC_RELAXED);
    uint64_t elapsedNs = 0;
    absolutetime_to_nanoseconds(ticks, &elapsedNs);
    IOLog("a04br3 gpu submit perf: stage=%s entered=%llu finished=%llu ticks=%llu ns=%llu\n", names[i],
          (unsigned long long)entered, (unsigned long long)finished, (unsigned long long)ticks,
          (unsigned long long)elapsedNs);
  }
  for (unsigned i = 0; i < 2; ++i)
    IOLog("a04br3 gpu wait perf: scope=%s success=%llu timeout=%llu other=%llu (non-atomic)\n",
          i ? "backboardd" : "others", (unsigned long long)__atomic_load_n(&gWaitFenceResult[i][0], __ATOMIC_RELAXED),
          (unsigned long long)__atomic_load_n(&gWaitFenceResult[i][1], __ATOMIC_RELAXED),
          (unsigned long long)__atomic_load_n(&gWaitFenceResult[i][2], __ATOMIC_RELAXED));
}

// Cumulative fence-wait counters for the app launch breakdown (BootProcessProbe.c, a04br3-input-latency=2): other
// entered/finished/ticks, backboardd entered/finished/ticks/success/timeout, other success/timeout. Zero unless the submit
// perf counters are enabled (a04br3-gpu-submit-perf=1 or a04br3-home-perf=1; A04BR3Board.cpp also enables them for the
// breakdown).
extern "C" void native_powervr_wait_snapshot(uint64_t out[10])
{
  const PerfCounter &o = gSubmitPerf[PerfWaitFence], &b = gSubmitPerf[PerfWaitFenceBackboard];
  out[0] = __atomic_load_n(&o.entered, __ATOMIC_RELAXED); out[1] = __atomic_load_n(&o.finished, __ATOMIC_RELAXED);
  out[2] = __atomic_load_n(&o.ticks, __ATOMIC_RELAXED);
  out[3] = __atomic_load_n(&b.entered, __ATOMIC_RELAXED); out[4] = __atomic_load_n(&b.finished, __ATOMIC_RELAXED);
  out[5] = __atomic_load_n(&b.ticks, __ATOMIC_RELAXED);
  out[6] = __atomic_load_n(&gWaitFenceResult[1][0], __ATOMIC_RELAXED); out[7] = __atomic_load_n(&gWaitFenceResult[1][1], __ATOMIC_RELAXED);
  out[8] = __atomic_load_n(&gWaitFenceResult[0][0], __ATOMIC_RELAXED); out[9] = __atomic_load_n(&gWaitFenceResult[0][1], __ATOMIC_RELAXED);
}

// ---- The Linux API shim (compat/native_powervr_linux_jobs.h) ----
extern "C" void native_powervr_lock_init(void **lock)
{
  *lock = IOLockAlloc();
  if (!*lock) native_powervr_log("powervr: jobs: lock allocation failed\n");
}
extern "C" void native_powervr_lock_destroy(void **lock)
{
  if (*lock) IOLockFree(static_cast<IOLock *>(*lock));
  *lock = nullptr;
}
extern "C" void native_powervr_lock(void *lock) { IOLockLock(static_cast<IOLock *>(lock)); }
extern "C" void native_powervr_unlock(void *lock) { IOLockUnlock(static_cast<IOLock *>(lock)); }

extern "C" void xa_init_flags(struct xarray *xa, unsigned int flags)
{
  memset(xa, 0, sizeof(*xa));
  xa->flags = flags;
  native_powervr_lock_init(&xa->native_lock);
}
extern "C" void xa_destroy(struct xarray *xa)
{
  kfree(xa->slots);
  xa->slots = nullptr;
  xa->nr_slots = 0;
  native_powervr_lock_destroy(&xa->native_lock);
}
namespace {
// Called with the xarray's lock held.
bool xaReserve(struct xarray *xa, uint64_t index)
{
  if (index < xa->nr_slots) return true;
  if (index >= U32_MAX) return false;
  uint64_t n = xa->nr_slots ? xa->nr_slots : 16;
  while (n <= index) n *= 2;
  if (n > U32_MAX) n = U32_MAX;
  void **slots = static_cast<void **>(kcalloc(size_t(n), sizeof(void *), GFP_KERNEL));
  if (!slots) return false;
  if (xa->slots) memcpy(slots, xa->slots, xa->nr_slots * sizeof(void *));
  kfree(xa->slots);
  xa->slots = slots;
  xa->nr_slots = uint32_t(n);
  return true;
}
// xa_insert(): entry at index, which must be free. Used to show the objects the user-client layer resolved to the Linux
// lookups under their handles.
int xaInsert(struct xarray *xa, unsigned long index, void *entry)
{
  native_powervr_lock(xa->native_lock);
  int err = 0;
  if (!xaReserve(xa, index)) err = -ENOMEM;
  else if (xa->slots[index]) err = -EBUSY;
  else xa->slots[index] = entry;
  native_powervr_unlock(xa->native_lock);
  return err;
}
}  // namespace
extern "C" int xa_alloc(struct xarray *xa, u32 *id, void *entry, struct xa_limit limit, gfp_t gfp)
{
  (void)gfp;
  native_powervr_lock(xa->native_lock);
  uint64_t first = limit.min;
  if ((xa->flags & XA_FLAGS_ALLOC1) == XA_FLAGS_ALLOC1 && first == 0) first = 1;
  int err = -EBUSY;
  for (uint64_t i = first; i <= limit.max; i++) {
    if (!xaReserve(xa, i)) {
      err = -ENOMEM;
      break;
    }
    if (!xa->slots[i]) {
      xa->slots[i] = entry;
      *id = u32(i);
      err = 0;
      break;
    }
  }
  native_powervr_unlock(xa->native_lock);
  return err;
}
extern "C" void *native_powervr_xa_erase(struct xarray *xa, unsigned long index)
{
  native_powervr_lock(xa->native_lock);
  void *old = nullptr;
  if (index < xa->nr_slots) {
    old = xa->slots[index];
    xa->slots[index] = nullptr;
  }
  native_powervr_unlock(xa->native_lock);
  return old;
}
extern "C" void *native_powervr_xa_load(struct xarray *xa, unsigned long index)
{
  return index < xa->nr_slots ? xa->slots[index] : nullptr;
}
extern "C" void *native_powervr_xa_find(struct xarray *xa, unsigned long *index, int after)
{
  native_powervr_lock(xa->native_lock);
  void *entry = nullptr;
  for (uint64_t i = uint64_t(*index) + (after ? 1 : 0); i < xa->nr_slots; i++) {
    if (xa->slots[i]) {
      entry = xa->slots[i];
      *index = static_cast<unsigned long>(i);
      break;
    }
  }
  native_powervr_unlock(xa->native_lock);
  return entry;
}
extern "C" bool xa_empty(struct xarray *xa)
{
  unsigned long index = 0;
  return !native_powervr_xa_find(xa, &index, 0);
}
extern "C" void *memdup_user(const void *src, size_t len)
{
  void *p = kmalloc(len, GFP_KERNEL);
  if (!p) return ERR_PTR(-ENOMEM);
  memcpy(p, src, len);
  return p;
}

// ---- pvr_device.c ----
extern "C" bool pvr_device_has_uapi_quirk(struct pvr_device *pvr_dev, u32 quirk)
{
  switch (quirk) {
  case 47217: return PVR_HAS_QUIRK(pvr_dev, 47217);
  case 48545: return PVR_HAS_QUIRK(pvr_dev, 48545);
  case 49927: return PVR_HAS_QUIRK(pvr_dev, 49927);
  case 51764: return PVR_HAS_QUIRK(pvr_dev, 51764);
  case 62269: return PVR_HAS_QUIRK(pvr_dev, 62269);
  default: return false;
  }
}
extern "C" bool pvr_device_has_uapi_enhancement(struct pvr_device *pvr_dev, u32 enhancement)
{
  switch (enhancement) {
  case 35421: return PVR_HAS_ENHANCEMENT(pvr_dev, 35421);
  case 42064: return PVR_HAS_ENHANCEMENT(pvr_dev, 42064);
  default: return false;
  }
}
extern "C" bool pvr_device_has_feature(struct pvr_device *pvr_dev, u32 feature)
{
  switch (feature) {
  case PVR_FEATURE_CLUSTER_GROUPING: return PVR_HAS_FEATURE(pvr_dev, cluster_grouping);
  case PVR_FEATURE_COMPUTE_MORTON_CAPABLE: return PVR_HAS_FEATURE(pvr_dev, compute_morton_capable);
  case PVR_FEATURE_FB_CDC_V4: return PVR_HAS_FEATURE(pvr_dev, fb_cdc_v4);
  case PVR_FEATURE_GPU_MULTICORE_SUPPORT: return PVR_HAS_FEATURE(pvr_dev, gpu_multicore_support);
  case PVR_FEATURE_ISP_ZLS_D24_S8_PACKING_OGL_MODE: return PVR_HAS_FEATURE(pvr_dev, isp_zls_d24_s8_packing_ogl_mode);
  case PVR_FEATURE_S7_TOP_INFRASTRUCTURE: return PVR_HAS_FEATURE(pvr_dev, s7_top_infrastructure);
  case PVR_FEATURE_TESSELLATION: return PVR_HAS_FEATURE(pvr_dev, tessellation);
  case PVR_FEATURE_TPU_DM_GLOBAL_REGISTERS: return PVR_HAS_FEATURE(pvr_dev, tpu_dm_global_registers);
  case PVR_FEATURE_VDM_DRAWINDIRECT: return PVR_HAS_FEATURE(pvr_dev, vdm_drawindirect);
  case PVR_FEATURE_VDM_OBJECT_LEVEL_LLS: return PVR_HAS_FEATURE(pvr_dev, vdm_object_level_lls);
  case PVR_FEATURE_ZLS_SUBTILE: return PVR_HAS_FEATURE(pvr_dev, zls_subtile);
  // Derived features.
  case PVR_FEATURE_CDM_USER_MODE_QUEUE: {
    u8 cdm_control_stream_format = 0;
    PVR_FEATURE_VALUE(pvr_dev, cdm_control_stream_format, &cdm_control_stream_format);
    return cdm_control_stream_format >= 2 && cdm_control_stream_format <= 4;
  }
  case PVR_FEATURE_REQUIRES_FB_CDC_ZLS_SETUP:
    if (PVR_HAS_FEATURE(pvr_dev, fbcdc_algorithm)) {
      u8 fbcdc_algorithm = 0;
      PVR_FEATURE_VALUE(pvr_dev, fbcdc_algorithm, &fbcdc_algorithm);
      return fbcdc_algorithm < 3 || PVR_HAS_FEATURE(pvr_dev, fb_cdc_v4);
    }
    return false;
  default:
    WARN(true, "Looking up undefined feature %u\n", feature);
    return false;
  }
}

// ---- pvr_fw.c: FW objects in the firmware heap, seen through a write-combined alias ----
extern "C" int pvr_fw_object_create(struct pvr_device *pvr_dev, size_t size, u64 flags, void (*init)(void *cpu_ptr, void *priv),
                                    void *init_priv, struct pvr_fw_object **pvr_obj_out)
{
  // flags: every caller here asks for PVR_BO_FW_FLAGS_DEVICE_UNCACHED, which the whole heap is for the firmware
  // (NativePowerVR.cpp maps it uncached in the MIPS page table).
  (void)pvr_dev;
  (void)flags;
  if (!size || size > SZ_16M) return -EINVAL;
  JobsFwObject *o = static_cast<JobsFwObject *>(kzalloc(sizeof(*o), GFP_KERNEL));
  if (!o) return -ENOMEM;
  const uint32_t sz = ALIGN(uint32_t(size), kCacheLine);
  if (native_powervr_fw_alloc(sz, sz >= SZ_4K ? SZ_4K : kCacheLine, &o->base.o)) {
    kfree(o);
    return -ENOMEM;
  }
  // The allocator zeroed it through the cached heap mapping: write those lines back and drop them before the alias is
  // used, and never write through the cached mapping again.
  native_powervr_invalidate(o->base.o.cpu, sz);
  if (!wcMap(o->base.o.phys, sz, o->wc)) {
    native_powervr_fw_free(&o->base.o);
    kfree(o);
    return -ENOMEM;
  }
  if (init) init(o->wc.cpu, init_priv);
  wmb();
  *pvr_obj_out = &o->base;
  return 0;
}
extern "C" void *pvr_fw_object_create_and_map(struct pvr_device *pvr_dev, size_t size, u64 flags,
                                              void (*init)(void *cpu_ptr, void *priv), void *init_priv,
                                              struct pvr_fw_object **pvr_obj_out)
{
  const int err = pvr_fw_object_create(pvr_dev, size, flags, init, init_priv, pvr_obj_out);
  return err ? ERR_PTR(err) : jobsFwObject(*pvr_obj_out)->wc.cpu;
}
extern "C" void *pvr_fw_object_vmap(struct pvr_fw_object *fw_obj) { return jobsFwObject(fw_obj)->wc.cpu; }
extern "C" void pvr_fw_object_vunmap(struct pvr_fw_object *fw_obj) { (void)fw_obj; wmb(); }
extern "C" void pvr_fw_object_destroy(struct pvr_fw_object *fw_obj)
{
  if (!fw_obj) return;
  JobsFwObject *o = jobsFwObject(fw_obj);
  wcUnmap(o->wc);
  native_powervr_fw_free(&o->base.o);
  kfree(o);
}
extern "C" void pvr_fw_object_unmap_and_destroy(struct pvr_fw_object *fw_obj)
{
  pvr_fw_object_vunmap(fw_obj);
  pvr_fw_object_destroy(fw_obj);
}
extern "C" void pvr_fw_object_get_fw_addr_offset(struct pvr_fw_object *fw_obj, u32 offset, u32 *fw_addr_out)
{
  *fw_addr_out = fw_obj->o.fw_addr + offset;
}

// pvr_fw_structure_cleanup()
extern "C" int pvr_fw_structure_cleanup(struct pvr_device *pvr_dev, u32 type, struct pvr_fw_object *fw_obj, u32 offset)
{
  struct rogue_fwif_kccb_cmd cmd;
  memset(&cmd, 0, sizeof(cmd));
  struct rogue_fwif_cleanup_request *cleanup_req = &cmd.cmd_data.cleanup_data;
  cmd.cmd_type = ROGUE_FWIF_KCCB_CMD_CLEANUP;
  cmd.kccb_flags = 0;
  cleanup_req->cleanup_type = static_cast<enum rogue_fwif_cleanup_type>(type);
  switch (type) {
  case ROGUE_FWIF_CLEANUP_FWCOMMONCONTEXT:
    pvr_fw_object_get_fw_addr_offset(fw_obj, offset, &cleanup_req->cleanup_data.context_fw_addr);
    break;
  case ROGUE_FWIF_CLEANUP_HWRTDATA:
    pvr_fw_object_get_fw_addr_offset(fw_obj, offset, &cleanup_req->cleanup_data.hwrt_data_fw_addr);
    break;
  case ROGUE_FWIF_CLEANUP_FREELIST:
    pvr_fw_object_get_fw_addr_offset(fw_obj, offset, &cleanup_req->cleanup_data.freelist_fw_addr);
    break;
  default:
    return -EINVAL;
  }
  u32 slot_nr = 0, rtn = 0;
  int err = pvr_kccb_send_cmd(pvr_dev, &cmd, &slot_nr);
  if (err) return err;
  err = pvr_kccb_wait_for_completion(pvr_dev, slot_nr, HZ, &rtn);
  if (err) return err;
  if (rtn & ROGUE_FWIF_KCCB_RTN_SLOT_CLEANUP_BUSY) err = -EBUSY;
  return err;
}

// pvr_kccb_send_cmd_reserved_powered(): Linux writes into a slot the job reserved and does not wait.
// native_powervr_kccb_submit() waits for the firmware to execute the command; a full KCCB (which the reservation rules
// out on Linux) is retried for up to a second.
extern "C" void pvr_kccb_send_cmd_reserved_powered(struct pvr_device *pvr_dev, struct rogue_fwif_kccb_cmd *cmd, u32 *kccb_slot)
{
  (void)pvr_dev;
  const uint64_t perf = perfBegin(PerfKccb);
  int err = -EBUSY;
  for (uint32_t ms = 0; err == -EBUSY && ms <= kKccbTimeoutMs; ms++) {
    err = native_powervr_kccb_submit(cmd, kKccbTimeoutMs);
    if (err == -EBUSY) IOSleep(1);
  }
  perfEnd(PerfKccb, perf);
  if (err) native_powervr_log("powervr: jobs: KCCB command %x: %d\n", cmd->cmd_type, err);
  if (kccb_slot) *kccb_slot = 0;
}

// ---- pvr_gem.c: free list pages and user BOs ----
extern "C" struct pvr_gem_object *pvr_gem_object_create(struct pvr_device *pvr_dev, size_t size, u64 flags)
{
  (void)pvr_dev;
  if (!size) return static_cast<pvr_gem_object *>(ERR_PTR(-EINVAL));
  JobsGem *g = static_cast<JobsGem *>(kzalloc(sizeof(*g), GFP_KERNEL));
  if (!g) return static_cast<pvr_gem_object *>(ERR_PTR(-ENOMEM));
  // Below 4 GiB: PHYS_BUS_WIDTH is 32 on this core, and the PM takes 32-bit page numbers.
  g->mem = IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task, kIODirectionInOut, size, 0x00000000FFFFF000ULL);
  if (!g->mem) {
    kfree(g);
    return static_cast<pvr_gem_object *>(ERR_PTR(-ENOMEM));
  }
  g->size = size;
  void *cpu = g->mem->getBytesNoCopy();
  memset(cpu, 0, size);
  native_powervr_clean(cpu, size);   // shmem pages are zeroed; the GPU sees the zeros
  // The physical runs (drm_gem_shmem_get_pages_sgt()).
  uint32_t n = 0;
  for (int pass = 0; pass < 2; pass++) {
    uint64_t lastEnd = ~0ULL;
    n = 0;
    for (size_t off = 0; off < size;) {
      IOByteCount len = 0;
      const uint64_t pa = g->mem->getPhysicalSegment(off, &len, kIOMemoryMapperNone);
      if (!pa || !len) break;
      if (len > size - off) len = size - off;
      if (pa == lastEnd) {
        if (pass) g->sgt.sgl[n - 1].dma_length += unsigned(len);
      } else {
        if (pass) g->sgt.sgl[n] = {pa, unsigned(len)};
        n++;
      }
      lastEnd = pa + len;
      off += len;
    }
    if (!pass) {
      g->sgt.sgl = static_cast<scatterlist *>(kcalloc(n ? n : 1, sizeof(scatterlist), GFP_KERNEL));
      if (!g->sgt.sgl) {
        g->mem->release();
        kfree(g);
        return static_cast<pvr_gem_object *>(ERR_PTR(-ENOMEM));
      }
    }
  }
  g->sgt.nents = g->sgt.orig_nents = n;
  g->gem.base.sgt = &g->sgt;
  g->gem.flags = flags;
  return &g->gem;
}
extern "C" struct sg_table *drm_gem_shmem_get_pages_sgt(struct drm_gem_shmem_object *shmem)
{
  return shmem->sgt ? shmem->sgt : static_cast<sg_table *>(ERR_PTR(-EINVAL));
}
extern "C" void pvr_gem_object_put(struct pvr_gem_object *pvr_obj)
{
  if (!pvr_obj) return;
  JobsGem *g = jobsGem(pvr_obj);
  if (g->bo) native_powervr_bo_put(g->bo);
  if (g->mem) g->mem->release();
  kfree(g->sgt.sgl);
  kfree(g);
}
// pvr_gem_object_vmap(): the BO's kernel view is cached (native_powervr_jobs.h), so it is cleaned and invalidated
// before the C code reads it and cleaned when it is done (pvr_gem_object_vunmap()).
extern "C" void *pvr_gem_object_vmap(struct pvr_gem_object *pvr_obj)
{
  JobsGem *g = jobsGem(pvr_obj);
  void *cpu = g->bo ? native_powervr_bo_cpu(g->bo) : g->mem->getBytesNoCopy();
  if (!cpu) return ERR_PTR(-ENOMEM);
  native_powervr_invalidate(cpu, g->bo ? size_t(native_powervr_bo_size(g->bo)) : g->size);
  return cpu;
}
extern "C" void pvr_gem_object_vunmap(struct pvr_gem_object *pvr_obj)
{
  JobsGem *g = jobsGem(pvr_obj);
  void *cpu = g->bo ? native_powervr_bo_cpu(g->bo) : g->mem->getBytesNoCopy();
  if (cpu) native_powervr_clean(cpu, g->bo ? size_t(native_powervr_bo_size(g->bo)) : g->size);
}

// ---- pvr_vm.c: the user-client layer's VM contexts ----
// struct pvr_vm_context of the C code is the user-client layer's struct native_powervr_vm_context.
static native_powervr_vm_context *nativeVm(pvr_vm_context *v) { return reinterpret_cast<native_powervr_vm_context *>(v); }
extern "C" struct pvr_vm_context *pvr_vm_context_lookup(struct pvr_file *pvr_file, u32 handle)
{
  xa_lock(&pvr_file->vm_ctx_handles);
  native_powervr_vm_context *vm = xa_load(&pvr_file->vm_ctx_handles, handle);
  if (vm) vm = native_powervr_vm_context_get(vm);
  xa_unlock(&pvr_file->vm_ctx_handles);
  return reinterpret_cast<pvr_vm_context *>(vm);
}
// Linux returns whether this put destroyed the context; no caller of the C code reads it, and the user-client layer
// does not report it.
extern "C" bool pvr_vm_context_put(struct pvr_vm_context *vm_ctx)
{
  if (vm_ctx) native_powervr_vm_context_put(nativeVm(vm_ctx));
  return false;
}
extern "C" struct pvr_gem_object *pvr_vm_find_gem_object(struct pvr_vm_context *vm_ctx, u64 device_addr,
                                                        u64 *mapped_offset_out, u64 *mapped_size_out)
{
  native_powervr_bo_mapping m;
  memset(&m, 0, sizeof(m));
  if (native_powervr_vm_find_bo(nativeVm(vm_ctx), device_addr, &m) || !m.bo) return nullptr;
  JobsGem *g = static_cast<JobsGem *>(kzalloc(sizeof(*g), GFP_KERNEL));
  if (!g) {
    native_powervr_bo_put(m.bo);
    return nullptr;
  }
  g->bo = m.bo;
  g->gem.flags = native_powervr_bo_flags(m.bo);
  if (mapped_offset_out) *mapped_offset_out = m.bo_offset;
  if (mapped_size_out) *mapped_size_out = m.va_range;
  return &g->gem;
}
// Linux unmaps the VM context of a context that queued jobs still hold when its file closes, so that those jobs fault
// rather than touch freed memory. The user-client layer owns the mappings; native_powervr_jobs_file_destroy() instead
// waits for the file's jobs to complete.
extern "C" void pvr_vm_unmap_all(struct pvr_vm_context *vm_ctx)
{
  (void)vm_ctx;
  native_powervr_log("powervr: jobs: a closing file's context still has queued jobs\n");
}

// ---- pvr_queue.c without drm_sched ----
namespace {
int get_xfer_ctx_state_size(struct pvr_device *pvr_dev)
{
  u32 num_isp_store_registers;
  if (PVR_HAS_FEATURE(pvr_dev, xe_memory_hierarchy)) {
    num_isp_store_registers = 1;
  } else {
    int err = PVR_FEATURE_VALUE(pvr_dev, num_isp_ipp_pipes, &num_isp_store_registers);
    if (WARN_ON(err)) return err;
  }
  // struct_size_t(struct rogue_fwif_frag_ctx_state, frag_reg_isp_store, n)
  return int(sizeof(struct rogue_fwif_frag_ctx_state) + num_isp_store_registers * sizeof(u32));
}
int get_frag_ctx_state_size(struct pvr_device *pvr_dev)
{
  u32 num_isp_store_registers;
  int err;
  if (PVR_HAS_FEATURE(pvr_dev, xe_memory_hierarchy)) {
    err = PVR_FEATURE_VALUE(pvr_dev, num_raster_pipes, &num_isp_store_registers);
    if (WARN_ON(err)) return err;
    if (PVR_HAS_FEATURE(pvr_dev, gpu_multicore_support)) {
      u32 xpu_max_slaves;
      err = PVR_FEATURE_VALUE(pvr_dev, xpu_max_slaves, &xpu_max_slaves);
      if (WARN_ON(err)) return err;
      num_isp_store_registers *= (1 + xpu_max_slaves);
    }
  } else {
    err = PVR_FEATURE_VALUE(pvr_dev, num_isp_ipp_pipes, &num_isp_store_registers);
    if (WARN_ON(err)) return err;
  }
  return int(sizeof(struct rogue_fwif_frag_ctx_state) + num_isp_store_registers * sizeof(u32));
}
int get_ctx_state_size(struct pvr_device *pvr_dev, enum drm_pvr_job_type type)
{
  switch (type) {
  case DRM_PVR_JOB_TYPE_GEOMETRY: return int(sizeof(struct rogue_fwif_geom_ctx_state));
  case DRM_PVR_JOB_TYPE_FRAGMENT: return get_frag_ctx_state_size(pvr_dev);
  case DRM_PVR_JOB_TYPE_COMPUTE: return int(sizeof(struct rogue_fwif_compute_ctx_state));
  case DRM_PVR_JOB_TYPE_TRANSFER_FRAG: return get_xfer_ctx_state_size(pvr_dev);
  }
  WARN(1, "Invalid queue type");
  return -EINVAL;
}
u32 get_ctx_offset(enum drm_pvr_job_type type)
{
  switch (type) {
  case DRM_PVR_JOB_TYPE_GEOMETRY: return u32(offsetof(struct rogue_fwif_fwrendercontext, geom_context));
  case DRM_PVR_JOB_TYPE_FRAGMENT: return u32(offsetof(struct rogue_fwif_fwrendercontext, frag_context));
  case DRM_PVR_JOB_TYPE_COMPUTE: return u32(offsetof(struct rogue_fwif_fwcomputecontext, cdm_context));
  case DRM_PVR_JOB_TYPE_TRANSFER_FRAG: return u32(offsetof(struct rogue_fwif_fwtransfercontext, tq_context));
  }
  return 0;
}
u32 get_dm_type(struct pvr_queue *queue)
{
  switch (queue->type) {
  case DRM_PVR_JOB_TYPE_GEOMETRY: return PVR_FWIF_DM_GEOM;
  case DRM_PVR_JOB_TYPE_TRANSFER_FRAG:
  case DRM_PVR_JOB_TYPE_FRAGMENT: return PVR_FWIF_DM_FRAG;
  case DRM_PVR_JOB_TYPE_COMPUTE: return PVR_FWIF_DM_CDM;
  }
  return ~0U;
}
void init_fw_context(struct pvr_queue *queue, void *fw_ctx_map)
{
  struct pvr_context *ctx = queue->ctx;
  struct rogue_fwif_fwcommoncontext *cctx_fw =
      reinterpret_cast<rogue_fwif_fwcommoncontext *>(static_cast<u8 *>(fw_ctx_map) + queue->ctx_offset);
  struct pvr_cccb *cccb = &queue->cccb;
  cctx_fw->ccbctl_fw_addr = cccb->ctrl_fw_addr;
  cctx_fw->ccb_fw_addr = cccb->cccb_fw_addr;
  cctx_fw->dm = get_dm_type(queue);
  cctx_fw->priority = ctx->priority;
  cctx_fw->priority_seq_num = 0;
  cctx_fw->max_deadline_ms = kMaxDeadlineMs;
  cctx_fw->pid = u32(proc_selfpid());   // task_tgid_nr(current): contexts are created on the caller's thread
  cctx_fw->server_common_context_id = ctx->ctx_id;
  // pvr_vm_get_fw_mem_context()
  cctx_fw->fw_mem_context_fw_addr = native_powervr_vm_fw_mem_ctx(native_powervr_vm_context_vm(nativeVm(ctx->vm_ctx)));
  pvr_fw_object_get_fw_addr(queue->reg_state_obj, &cctx_fw->context_state_addr);
}
int pvr_queue_cleanup_fw_context(struct pvr_queue *queue)
{
  if (!queue->ctx->fw_obj) return 0;
  return pvr_fw_structure_cleanup(queue->ctx->pvr_dev, ROGUE_FWIF_CLEANUP_FWCOMMONCONTEXT, queue->ctx->fw_obj,
                                  queue->ctx_offset);
}
void reg_state_init(void *cpu_ptr, void *priv)
{
  struct pvr_queue *queue = static_cast<pvr_queue *>(priv);
  if (queue->type == DRM_PVR_JOB_TYPE_GEOMETRY) {
    struct rogue_fwif_geom_ctx_state *geom_ctx_state_fw = static_cast<rogue_fwif_geom_ctx_state *>(cpu_ptr);
    geom_ctx_state_fw->geom_core[0].geom_reg_vdm_call_stack_pointer_init = queue->callstack_addr;
  }
}

// Forget a queue that goes away in the HWRT tracking (called with gJobs.lock held).
void purgeQueue(pvr_queue *q)
{
  for (uint32_t i = 0; i < gJobs.ntracks; i++) {
    HwrtTrack &t = gJobs.tracks[i];
    if (t.write.queue == q) t.write = {};
    for (uint32_t r = 0; r < t.nreads;) {
      if (t.reads[r].queue == q) t.reads[r] = t.reads[--t.nreads];
      else r++;
    }
  }
}
}  // namespace

extern "C" struct pvr_queue *pvr_queue_create(struct pvr_context *ctx, enum drm_pvr_job_type type,
                                              struct drm_pvr_ioctl_create_context_args *args, void *fw_ctx_map)
{
  static const char *const names[] = {"geometry", "fragment", "compute", "transfer_frag"};
  struct pvr_device *pvr_dev = ctx->pvr_dev;
  if (WARN_ON(u32(type) > DRM_PVR_JOB_TYPE_TRANSFER_FRAG)) return static_cast<pvr_queue *>(ERR_PTR(-EINVAL));
  switch (ctx->type) {
  case DRM_PVR_CTX_TYPE_RENDER:
    if (type != DRM_PVR_JOB_TYPE_GEOMETRY && type != DRM_PVR_JOB_TYPE_FRAGMENT) return static_cast<pvr_queue *>(ERR_PTR(-EINVAL));
    break;
  case DRM_PVR_CTX_TYPE_COMPUTE:
    if (type != DRM_PVR_JOB_TYPE_COMPUTE) return static_cast<pvr_queue *>(ERR_PTR(-EINVAL));
    break;
  case DRM_PVR_CTX_TYPE_TRANSFER_FRAG:
    if (type != DRM_PVR_JOB_TYPE_TRANSFER_FRAG) return static_cast<pvr_queue *>(ERR_PTR(-EINVAL));
    break;
  default:
    return static_cast<pvr_queue *>(ERR_PTR(-EINVAL));
  }
  const int ctx_state_size = get_ctx_state_size(pvr_dev, type);
  if (ctx_state_size < 0) return static_cast<pvr_queue *>(ERR_PTR(ctx_state_size));
  pvr_queue *queue = static_cast<pvr_queue *>(kzalloc(sizeof(*queue), GFP_KERNEL));
  if (!queue) return static_cast<pvr_queue *>(ERR_PTR(-ENOMEM));
  queue->generation = __atomic_add_fetch(&gQueueGeneration, 1ULL, __ATOMIC_RELAXED);
  queue->type = type;
  queue->ctx_offset = get_ctx_offset(type);
  queue->ctx = ctx;
  queue->callstack_addr = args->callstack_addr;
  void *cpu_map;
  int err = pvr_cccb_init(pvr_dev, &queue->cccb, kCccbSizeLog2, names[type]);
  if (err) goto err_free_queue;
  err = pvr_fw_object_create(pvr_dev, size_t(ctx_state_size), PVR_BO_FW_FLAGS_DEVICE_UNCACHED, reg_state_init, queue,
                             &queue->reg_state_obj);
  if (err) goto err_cccb_fini;
  init_fw_context(queue, fw_ctx_map);
  if (type != DRM_PVR_JOB_TYPE_GEOMETRY && type != DRM_PVR_JOB_TYPE_FRAGMENT && args->callstack_addr) {
    err = -EINVAL;
    goto err_release_reg_state;
  }
  cpu_map = pvr_fw_object_create_and_map(pvr_dev, sizeof(*queue->timeline_ufo.value), PVR_BO_FW_FLAGS_DEVICE_UNCACHED,
                                         nullptr, nullptr, &queue->timeline_ufo.fw_obj);
  if (IS_ERR(cpu_map)) {
    err = int(PTR_ERR(cpu_map));
    goto err_release_reg_state;
  }
  queue->timeline_ufo.value = static_cast<u32 *>(cpu_map);
  return queue;

err_release_reg_state:
  pvr_fw_object_destroy(queue->reg_state_obj);
err_cccb_fini:
  pvr_cccb_fini(&queue->cccb);
err_free_queue:
  kfree(queue);
  return static_cast<pvr_queue *>(ERR_PTR(err));
}

// pvr_queue_kill(): no job is queued to it after this (the scheduler entity is destroyed on Linux).
extern "C" void pvr_queue_kill(struct pvr_queue *queue)
{
  IOLockLock(gJobs.lock);
  queue->killed = true;
  IOLockUnlock(gJobs.lock);
}

// pvr_queue_destroy(): runs once nothing references the context, so no job of the queue is still queued.
extern "C" void pvr_queue_destroy(struct pvr_queue *queue, bool cleanup_queue_entity)
{
  (void)cleanup_queue_entity;
  if (!queue) return;
  IOLockLock(gJobs.lock);
  purgeQueue(queue);
  IOLockUnlock(gJobs.lock);
  const int err = pvr_queue_cleanup_fw_context(queue);
  if (err) native_powervr_log("powervr: jobs: FW context cleanup: %d\n", err);
  pvr_fw_object_unmap_and_destroy(queue->timeline_ufo.fw_obj);
  pvr_fw_object_destroy(queue->reg_state_obj);
  pvr_cccb_fini(&queue->cccb);
  kfree(queue);
}

// ---- pvr_ccb.c: the firmware CCB ----
namespace {
// process_fwccb_command()
void process_fwccb_command(struct pvr_device *pvr_dev, struct rogue_fwif_fwccb_cmd *cmd)
{
  if ((cmd->cmd_type & ROGUE_CMD_MAGIC_DWORD_MASK) != ROGUE_CMD_MAGIC_DWORD_SHIFTED) {
    native_powervr_log("powervr: Received FWCCB command with bad magic value; ignoring (type=0x%08x)\n", cmd->cmd_type);
    return;
  }
  switch (cmd->cmd_type) {
  case ROGUE_FWIF_FWCCB_CMD_REQUEST_GPU_RESTART:
    pvr_power_reset(pvr_dev, false);
    break;
  case ROGUE_FWIF_FWCCB_CMD_FREELISTS_RECONSTRUCTION:
    pvr_free_list_process_reconstruct_req(pvr_dev, &cmd->cmd_data.cmd_freelists_reconstruction);
    break;
  case ROGUE_FWIF_FWCCB_CMD_FREELIST_GROW:
    pvr_free_list_process_grow_req(pvr_dev, &cmd->cmd_data.cmd_free_list_gs);
    break;
  case ROGUE_FWIF_FWCCB_CMD_UPDATE_STATS:
    break;
  case ROGUE_FWIF_FWCCB_CMD_CONTEXT_RESET_NOTIFICATION: {
    rogue_fwif_fwccb_cmd_context_reset_data *d = &cmd->cmd_data.cmd_context_reset_notification;
    pvr_dump_context_reset_notification(pvr_dev, d);
    const ResetNote note = {d->server_common_context_id, d->reset_job_ref, d->reset_reason, d->flags,
                            d->dm, d->fault_address, d->pc_address};
    // Submission may hold jobs lock while waiting for KCCB. Callbacks only
    // publish notes; the verdict arbiter applies them after callbacks return.
    IOLockLock(gJobs.fwccbLock);
    // pvr_fwccb_process reserves one note slot for every in-flight callback.
    gJobs.resets[gJobs.resetCount++] = note;
    IOLockUnlock(gJobs.fwccbLock);
    break;
  }
  default:
    native_powervr_log("powervr: Received unknown FWCCB command (type=%d)\n", int(cmd->cmd_type & ~ROGUE_CMD_MAGIC_DWORD_MASK));
    break;
  }
}
}  // namespace

// pvr_fwccb_process()
extern "C" void pvr_fwccb_process(struct pvr_device *pvr_dev)
{
  rogue_fwif_ccb_ctl *ctrl = gJobs.fwccbCtl;
  if (!ctrl) return;
  IOLockLock(gJobs.fwccbLock);
  u32 read_offset;
  uint32_t budget = kResetNotes; // a replenished ring cannot starve completion timeouts
  while (budget-- && (read_offset = READ_ONCE(ctrl->read_offset)) != READ_ONCE(ctrl->write_offset)) {
    // Reserve worst-case reset storage before taking a command off the ring.
    // Leave commands for the arbiter to drain after applying a full batch.
    if (gJobs.resetCount + gJobs.fwccbActive >= kResetNotes) break;
    if (read_offset >= gJobs.fwccbSlots) {
      native_powervr_log("powervr: jobs: FWCCB read offset %u out of range\n", read_offset);
      break;
    }
    struct rogue_fwif_fwccb_cmd cmd;
    memcpy(&cmd, &gJobs.fwccb[read_offset], sizeof(cmd));
    WRITE_ONCE(ctrl->read_offset, (read_offset + 1) & READ_ONCE(ctrl->wrap_mask));
    wmb();
    ++gJobs.fwccbActive;
    // Drop FWCCB lock while we process command. Success arbitration observes fwccbActive.
    IOLockUnlock(gJobs.fwccbLock);
    process_fwccb_command(pvr_dev, &cmd);
    IOLockLock(gJobs.fwccbLock);
    --gJobs.fwccbActive;
  }
  IOLockUnlock(gJobs.fwccbLock);
}

// ---- pvr_job.c and the job half of pvr_queue.c ----
namespace {
u32 convert_geom_flags(u32 in_flags)
{
  u32 out_flags = 0;
  if (in_flags & DRM_PVR_SUBMIT_JOB_GEOM_CMD_FIRST) out_flags |= ROGUE_GEOM_FLAGS_FIRSTKICK;
  if (in_flags & DRM_PVR_SUBMIT_JOB_GEOM_CMD_LAST) out_flags |= ROGUE_GEOM_FLAGS_LASTKICK;
  if (in_flags & DRM_PVR_SUBMIT_JOB_GEOM_CMD_SINGLE_CORE) out_flags |= ROGUE_GEOM_FLAGS_SINGLE_CORE;
  return out_flags;
}
u32 convert_frag_flags(u32 in_flags)
{
  u32 out_flags = 0;
  if (in_flags & DRM_PVR_SUBMIT_JOB_FRAG_CMD_SINGLE_CORE) out_flags |= ROGUE_FRAG_FLAGS_SINGLE_CORE;
  if (in_flags & DRM_PVR_SUBMIT_JOB_FRAG_CMD_DEPTHBUFFER) out_flags |= ROGUE_FRAG_FLAGS_DEPTHBUFFER;
  if (in_flags & DRM_PVR_SUBMIT_JOB_FRAG_CMD_STENCILBUFFER) out_flags |= ROGUE_FRAG_FLAGS_STENCILBUFFER;
  if (in_flags & DRM_PVR_SUBMIT_JOB_FRAG_CMD_PREVENT_CDM_OVERLAP) out_flags |= ROGUE_FRAG_FLAGS_PREVENT_CDM_OVERLAP;
  if (in_flags & DRM_PVR_SUBMIT_JOB_FRAG_CMD_SCRATCHBUFFER) out_flags |= ROGUE_FRAG_FLAGS_SCRATCHBUFFER;
  if (in_flags & DRM_PVR_SUBMIT_JOB_FRAG_CMD_GET_VIS_RESULTS) out_flags |= ROGUE_FRAG_FLAGS_GET_VIS_RESULTS;
  if (in_flags & DRM_PVR_SUBMIT_JOB_FRAG_CMD_DISABLE_PIXELMERGE) out_flags |= ROGUE_FRAG_FLAGS_DISABLE_PIXELMERGE;
  return out_flags;
}
u32 convert_compute_flags(u32 in_flags)
{
  u32 out_flags = 0;
  if (in_flags & DRM_PVR_SUBMIT_JOB_COMPUTE_CMD_PREVENT_ALL_OVERLAP) out_flags |= ROGUE_COMPUTE_FLAG_PREVENT_ALL_OVERLAP;
  if (in_flags & DRM_PVR_SUBMIT_JOB_COMPUTE_CMD_SINGLE_CORE) out_flags |= ROGUE_COMPUTE_FLAG_SINGLE_CORE;
  return out_flags;
}
u32 convert_transfer_flags(u32 in_flags)
{
  u32 out_flags = 0;
  if (in_flags & DRM_PVR_SUBMIT_JOB_TRANSFER_CMD_SINGLE_CORE) out_flags |= ROGUE_TRANSFER_FLAGS_SINGLE_CORE;
  return out_flags;
}

// pvr_fw_cmd_init() with pvr_job_process_stream(); the stream is the kernel copy of the user-client layer.
int pvr_fw_cmd_init(struct pvr_device *pvr_dev, Job *job, const struct pvr_stream_cmd_defs *stream_def, const void *stream_copy,
                    u32 stream_len)
{
  void *stream = memdup_user(stream_copy, stream_len);
  if (IS_ERR(stream)) return int(PTR_ERR(stream));
  int err = -ENOMEM;
  job->cmd = kzalloc(stream_def->dest_size, GFP_KERNEL);
  if (job->cmd) {
    job->cmd_len = u32(stream_def->dest_size);
    err = pvr_stream_process(pvr_dev, stream_def, stream, stream_len, job->cmd);
    if (err) {
      kfree(job->cmd);
      job->cmd = nullptr;
    }
  }
  kfree(stream);
  return err;
}

// pvr_job_fw_cmd_init() and the four pvr_*_job_fw_cmd_init()
int pvr_job_fw_cmd_init(Job *job, const struct drm_pvr_job *args, const void *stream)
{
  struct pvr_device *pvr_dev = &gJobs.dev;
  int err;
  switch (args->type) {
  case DRM_PVR_JOB_TYPE_GEOMETRY: {
    if (args->flags & ~DRM_PVR_SUBMIT_JOB_GEOM_CMD_FLAGS_MASK) return -EINVAL;
    if (job->ctx->type != DRM_PVR_CTX_TYPE_RENDER) return -EINVAL;
    if (!job->hwrt) return -EINVAL;
    job->fw_ccb_cmd_type = ROGUE_FWIF_CCB_CMD_TYPE_GEOM;
    err = pvr_fw_cmd_init(pvr_dev, job, &pvr_cmd_geom_stream, stream, args->cmd_stream_len);
    if (err) return err;
    rogue_fwif_cmd_geom *cmd = static_cast<rogue_fwif_cmd_geom *>(job->cmd);
    cmd->cmd_shared.cmn.frame_num = 0;
    cmd->flags = convert_geom_flags(args->flags);
    pvr_fw_object_get_fw_addr(job->hwrt->fw_obj, &cmd->cmd_shared.hwrt_data_fw_addr);
    return 0;
  }
  case DRM_PVR_JOB_TYPE_FRAGMENT: {
    if (args->flags & ~DRM_PVR_SUBMIT_JOB_FRAG_CMD_FLAGS_MASK) return -EINVAL;
    if (job->ctx->type != DRM_PVR_CTX_TYPE_RENDER) return -EINVAL;
    if (!job->hwrt) return -EINVAL;
    job->fw_ccb_cmd_type = (args->flags & DRM_PVR_SUBMIT_JOB_FRAG_CMD_PARTIAL_RENDER) ? ROGUE_FWIF_CCB_CMD_TYPE_FRAG_PR
                                                                                     : ROGUE_FWIF_CCB_CMD_TYPE_FRAG;
    err = pvr_fw_cmd_init(pvr_dev, job, &pvr_cmd_frag_stream, stream, args->cmd_stream_len);
    if (err) return err;
    rogue_fwif_cmd_frag *cmd = static_cast<rogue_fwif_cmd_frag *>(job->cmd);
    cmd->cmd_shared.cmn.frame_num = 0;
    cmd->flags = convert_frag_flags(args->flags);
    pvr_fw_object_get_fw_addr(job->hwrt->fw_obj, &cmd->cmd_shared.hwrt_data_fw_addr);
    return 0;
  }
  case DRM_PVR_JOB_TYPE_COMPUTE: {
    if (args->flags & ~DRM_PVR_SUBMIT_JOB_COMPUTE_CMD_FLAGS_MASK) return -EINVAL;
    if (job->ctx->type != DRM_PVR_CTX_TYPE_COMPUTE) return -EINVAL;
    job->fw_ccb_cmd_type = ROGUE_FWIF_CCB_CMD_TYPE_CDM;
    err = pvr_fw_cmd_init(pvr_dev, job, &pvr_cmd_compute_stream, stream, args->cmd_stream_len);
    if (err) return err;
    rogue_fwif_cmd_compute *cmd = static_cast<rogue_fwif_cmd_compute *>(job->cmd);
    cmd->common.frame_num = 0;
    cmd->flags = convert_compute_flags(args->flags);
    return 0;
  }
  case DRM_PVR_JOB_TYPE_TRANSFER_FRAG: {
    if (args->flags & ~DRM_PVR_SUBMIT_JOB_TRANSFER_CMD_FLAGS_MASK) return -EINVAL;
    if (job->ctx->type != DRM_PVR_CTX_TYPE_TRANSFER_FRAG) return -EINVAL;
    job->fw_ccb_cmd_type = ROGUE_FWIF_CCB_CMD_TYPE_TQ_3D;
    err = pvr_fw_cmd_init(pvr_dev, job, &pvr_cmd_transfer_stream, stream, args->cmd_stream_len);
    if (err) return err;
    rogue_fwif_cmd_transfer *cmd = static_cast<rogue_fwif_cmd_transfer *>(job->cmd);
    cmd->common.frame_num = 0;
    cmd->flags = convert_transfer_flags(args->flags);
    return 0;
  }
  default:
    return -EINVAL;
  }
}

// ufo_cmds_size(), job_cmds_size()
u32 ufo_cmds_size(u32 elem_count)
{
  const u32 full_cmd_count = elem_count / ROGUE_FWIF_CCB_CMD_MAX_UFOS;
  const u32 remaining_elems = elem_count % ROGUE_FWIF_CCB_CMD_MAX_UFOS;
  u32 size = full_cmd_count * pvr_cccb_get_size_of_cmd_with_hdr(ROGUE_FWIF_CCB_CMD_MAX_UFOS * sizeof(struct rogue_fwif_ufo));
  if (remaining_elems) size += pvr_cccb_get_size_of_cmd_with_hdr(remaining_elems * sizeof(struct rogue_fwif_ufo));
  return size;
}
u32 job_cmds_size(const Job *job, u32 ufo_wait_count)
{
  return ufo_cmds_size(ufo_wait_count) + pvr_cccb_get_size_of_cmd_with_hdr(job->cmd_len) + ufo_cmds_size(1);
}

// pvr_job_release(); called without gJobs.lock (dropping the last context reference destroys the queues).
void pvr_job_release(Job *job)
{
  native_powervr_pte_put(job->pte);
  kfree(job->pre_pte);
  native_powervr_hosts_put(job->hosts);
  if (job->id) native_powervr_xa_erase(&gJobs.dev.job_ids, job->id);
  pvr_hwrt_data_put(job->hwrt);
  pvr_context_put(job->ctx);
  kfree(job->cmd);
  kfree(job);
}

// create_job() with pvr_queue_job_init()
int create_job(native_powervr_jobs_file *file, const native_powervr_job_submit *s, Job **out)
{
  const struct drm_pvr_job *args = s->args;
  *out = nullptr;
  if (!args || !s->ctx || !args->cmd_stream || !args->cmd_stream_len || !s->cmd_stream) return -EINVAL;
  if (args->type != DRM_PVR_JOB_TYPE_GEOMETRY && args->type != DRM_PVR_JOB_TYPE_FRAGMENT &&
      (args->hwrt.set_handle || args->hwrt.data_index))
    return -EINVAL;
  if (args->sync_ops.count) return -EINVAL;   // NativePowerVRABI.h version 1
  if (!args->hwrt.set_handle != !s->hwrt) return -EINVAL;
  Job *job = static_cast<Job *>(kzalloc(sizeof(*job), GFP_KERNEL));
  if (!job) return -ENOMEM;
  job->type = static_cast<enum drm_pvr_job_type>(args->type);
  int err = xa_alloc(&gJobs.dev.job_ids, &job->id, job, xa_limit_32b, GFP_KERNEL);
  if (err) {
    job->id = 0;
    goto err_put_job;
  }
  IOLockLock(gJobs.lock);
  native_powervr_jobs_file *owner;
  list_for_each_entry(owner, &gJobs.files, link)
    for (auto &r : owner->retired)
      if (r.job == job->id) r.eligible = false;
  IOLockUnlock(gJobs.lock);
  job->ctx = pvr_context_lookup(&file->pf, s->ctx->handle);
  if (!job->ctx) {
    err = -EINVAL;
    goto err_put_job;
  }
  if (args->hwrt.set_handle) {
    // pvr_hwrt_data_lookup() on the dataset the user-client layer resolved.
    pvr_hwrt_dataset *hwrt_dataset = reinterpret_cast<pvr_hwrt_dataset *>(s->hwrt);
    if (args->hwrt.data_index >= ARRAY_SIZE(hwrt_dataset->data)) {
      err = -EINVAL;
      goto err_put_job;
    }
    kref_get(&hwrt_dataset->ref_count);
    job->hwrt = &hwrt_dataset->data[args->hwrt.data_index];
  }
  err = pvr_job_fw_cmd_init(job, args, s->cmd_stream);
  if (err) goto err_put_job;
  {
    // pvr_queue_job_init(): fragment jobs need at least one native fence wait on the geometry job fence.
    const u32 min_native_dep_count = job->type == DRM_PVR_JOB_TYPE_FRAGMENT ? 1 : 0;
    if (atomic_read(&job->ctx->faulty)) {
      err = -EIO;
      goto err_put_job;
    }
    job->queue = pvr_context_get_queue_for_job(job->ctx, job->type);
    if (!job->queue) {
      err = -EINVAL;
      goto err_put_job;
    }
    if (!pvr_cccb_cmdseq_can_fit(&job->queue->cccb, job_cmds_size(job, min_native_dep_count))) {
      err = -E2BIG;
      goto err_put_job;
    }
  }
  err = native_powervr_vm_hosts_snapshot(nativeVm(job->ctx->vm_ctx), &job->hosts);
  if (err) goto err_put_job;
  if (job->type == DRM_PVR_JOB_TYPE_TRANSFER_FRAG && job->cmd_len == sizeof(rogue_fwif_cmd_transfer)) {
    job->pte_requested = native_powervr_pte_enabled() && reservePte();
    if (job->pte_requested)
      job->pte = native_powervr_pte_prepare(nativeVm(job->ctx->vm_ctx),
                   static_cast<const rogue_fwif_cmd_transfer *>(job->cmd)->regs.isp_mtile_base);
    if (job->pte_requested)
      job->pre_pte = static_cast<native_powervr_pte_record *>(kzalloc(sizeof(native_powervr_pte_record), GFP_KERNEL));
  }
  *out = job;
  return 0;

err_put_job:
  pvr_job_release(job);
  return err;
}

// can_combine_jobs(): the explicit geometry -> fragment dependency Linux requires is implied by NativePowerVRABI.h.
bool can_combine_jobs(const Job *a, const Job *b)
{
  return a->type == DRM_PVR_JOB_TYPE_GEOMETRY && b->type == DRM_PVR_JOB_TYPE_FRAGMENT && a->ctx == b->ctx && a->hwrt == b->hwrt;
}

// ---- dependencies (gJobs.lock held) ----
// Room for extra more entries, so that trackFor() cannot fail while a call writes its jobs.
bool reserveTracks(uint32_t extra)
{
  if (gJobs.ntracks + extra <= gJobs.captracks) return true;
  uint32_t cap = gJobs.captracks ? gJobs.captracks : 32;
  while (cap < gJobs.ntracks + extra) cap *= 2;
  HwrtTrack *t = static_cast<HwrtTrack *>(kcalloc(cap, sizeof(HwrtTrack), GFP_KERNEL));
  if (!t) return false;
  if (gJobs.tracks) memcpy(t, gJobs.tracks, gJobs.ntracks * sizeof(HwrtTrack));
  kfree(gJobs.tracks);
  gJobs.tracks = t;
  gJobs.captracks = cap;
  return true;
}

// The entry of an HWRT data, called right before its job is written (an entry without pending work is free for reuse,
// so entries must not be taken ahead). reserveTracks() made room.
HwrtTrack *trackFor(pvr_hwrt_data *hwrt)
{
  HwrtTrack *spare = nullptr;
  for (uint32_t i = 0; i < gJobs.ntracks; i++) {
    HwrtTrack &t = gJobs.tracks[i];
    if (t.hwrt == hwrt) return &t;
    if (spare) continue;
    // An entry whose jobs have all completed carries no dependency; it may belong to a dataset that is gone.
    bool idle = depDone(t.write);
    for (uint32_t r = 0; idle && r < t.nreads; r++) idle = depDone(t.reads[r]);
    if (idle) spare = &t;
  }
  if (!spare) {
    if (WARN_ON(gJobs.ntracks == gJobs.captracks)) return nullptr;
    spare = &gJobs.tracks[gJobs.ntracks++];
  }
  memset(spare, 0, sizeof(*spare));
  spare->hwrt = hwrt;
  return spare;
}

void addDep(UfoList &l, const Dep &d, const pvr_queue *self)
{
  if (!d.queue || d.queue == self || depDone(d)) return;   // one queue runs its jobs in order
  for (uint32_t i = 0; i < l.n; i++) {
    if (l.queue[i] == d.queue) {
      if (int32_t(d.seqno - l.ufo[i].value) > 0) l.ufo[i].value = d.seqno;
      return;
    }
  }
  if (WARN_ON(l.n == kMaxUfosPerJob)) return;
  pvr_fw_object_get_fw_addr(d.queue->timeline_ufo.fw_obj, &l.ufo[l.n].addr);
  l.ufo[l.n].value = d.seqno;
  l.queue[l.n] = d.queue;
  l.n++;
}

// The waits dma_resv gives the job on Linux: geometry (DMA_RESV_USAGE_WRITE) waits for every earlier job on the HWRT
// data, fragment (READ) for the last geometry job.
void hwrtDeps(const Job *j, HwrtTrack *t, UfoList &l)
{
  if (!t) return;
  addDep(l, t->write, j->queue);
  if (j->type == DRM_PVR_JOB_TYPE_GEOMETRY)
    for (uint32_t r = 0; r < t->nreads; r++) addDep(l, t->reads[r], j->queue);
}

// update_job_resvs()
void hwrtUpdate(const Job *j, HwrtTrack *t)
{
  if (!t) return;
  if (j->type == DRM_PVR_JOB_TYPE_GEOMETRY) {
    t->write = {j->queue, j->seqno};
    t->nreads = 0;
    return;
  }
  for (uint32_t r = 0; r < t->nreads; r++) {
    if (t->reads[r].queue == j->queue) {
      t->reads[r].seqno = j->seqno;
      return;
    }
  }
  for (uint32_t r = 0; r < t->nreads;) {   // completed readers need no wait
    if (depDone(t->reads[r])) t->reads[r] = t->reads[--t->nreads];
    else r++;
  }
  if (t->nreads == kHwrtReaders) {
    // More reader queues than remembered: wait here for the oldest to finish (the firmware makes progress without
    // this lock).
    native_powervr_log("powervr: jobs: more than %u reader queues on one HWRT data; waiting on the CPU\n", kHwrtReaders);
    const uint64_t perf = perfBegin(PerfHwrt);
    while (!depDone(t->reads[0])) IOSleep(1);
    perfEnd(PerfHwrt, perf);
    t->reads[0] = t->reads[--t->nreads];
  }
  t->reads[t->nreads++] = {j->queue, j->seqno};
}

// pvr_queue_submit_job_to_cccb(); the caller checked the space.
void submit_job_to_cccb(Job *job, UfoList &deps)
{
  struct pvr_cccb *cccb = &job->queue->cccb;
  for (u32 off = 0; off < deps.n; off += ROGUE_FWIF_CCB_CMD_MAX_UFOS) {
    const u32 count = deps.n - off < ROGUE_FWIF_CCB_CMD_MAX_UFOS ? deps.n - off : ROGUE_FWIF_CCB_CMD_MAX_UFOS;
    pvr_cccb_write_command_with_header(cccb, ROGUE_FWIF_CCB_CMD_TYPE_FENCE_PR, u32(sizeof(deps.ufo[0]) * count),
                                       &deps.ufo[off], 0, 0);
  }
  if (job->type == DRM_PVR_JOB_TYPE_GEOMETRY && job->paired) {
    rogue_fwif_cmd_geom *cmd = static_cast<rogue_fwif_cmd_geom *>(job->cmd);
    // Reference value for the partial render test is the current queue fence seqno minus one.
    pvr_fw_object_get_fw_addr(job->queue->timeline_ufo.fw_obj, &cmd->partial_render_geom_frag_fence.addr);
    cmd->partial_render_geom_frag_fence.value = job->seqno - 1;
  }
  capturePacket(job);
  // Submit job to FW
  pvr_cccb_write_command_with_header(cccb, job->fw_ccb_cmd_type, job->cmd_len, job->cmd, job->id, job->id);
  // Update command to signal the job fence.
  struct rogue_fwif_ufo ufo;
  pvr_fw_object_get_fw_addr(job->queue->timeline_ufo.fw_obj, &ufo.addr);
  ufo.value = job->seqno;
  pvr_cccb_write_command_with_header(cccb, ROGUE_FWIF_CCB_CMD_TYPE_UPDATE, u32(sizeof(ufo)), &ufo, 0, 0);
}

// Write one job (or a geometry job and its paired fragment job) and kick it (pvr_queue_run_job()).
void run_job(Job *job, HwrtTrack *track, uint64_t *kccbTicks)
{
  UfoList deps;
  deps.n = 0;
  job->seqno = ++job->queue->seqno;
  hwrtDeps(job, track, deps);
  capturePte(job);
  submit_job_to_cccb(job, deps);
  hwrtUpdate(job, track);
  const u32 ctx_fw_addr = pvr_context_get_fw_addr(job->ctx);
  if (job->paired) {
    Job *frag_job = job->paired;
    // The fragment job waits for its geometry job: the HWRT data's last writer is that job now.
    UfoList frag_deps;
    frag_deps.n = 0;
    frag_job->seqno = ++frag_job->queue->seqno;
    hwrtDeps(frag_job, track, frag_deps);
    submit_job_to_cccb(frag_job, frag_deps);
    hwrtUpdate(frag_job, track);
    const uint64_t stageStart=kccbTicks ? nowAbs() : 0;
    pvr_cccb_send_kccb_combined_kick(&gJobs.dev, &job->queue->cccb, &frag_job->queue->cccb, ctx_fw_addr + job->queue->ctx_offset,
                                     ctx_fw_addr + frag_job->queue->ctx_offset, job->hwrt,
                                     frag_job->fw_ccb_cmd_type == ROGUE_FWIF_CCB_CMD_TYPE_FRAG_PR);
    if (stageStart) *kccbTicks += nowAbs()-stageStart;
  } else {
    const uint64_t stageStart=kccbTicks ? nowAbs() : 0;
    pvr_cccb_send_kccb_kick(&gJobs.dev, &job->queue->cccb, ctx_fw_addr + job->queue->ctx_offset, job->hwrt);
    if (stageStart) *kccbTicks += nowAbs()-stageStart;
  }
}

// Caller holds gJobs.lock. An exhausted interval table fails closed through the newly failed ID. Exact ranges
// above that watermark remain available; successful fences below it may conservatively report an error.
void recordFailed(native_powervr_jobs_file *f, uint64_t fence)
{
  if (fence <= f->failureUncertainThrough) return;
  uint32_t lo = 0, hi = f->failedCount;
  while (lo < hi) {
    const uint32_t mid = lo + (hi - lo) / 2;
    if (f->failed[mid].first < fence) lo = mid + 1;
    else hi = mid;
  }
  const uint32_t i = lo;
  if (i < f->failedCount && f->failed[i].first == fence) return;
  if (i && f->failed[i - 1].last >= fence) return;
  const bool before = i && fence - f->failed[i - 1].last == 1;
  const bool after = i < f->failedCount && f->failed[i].first - fence == 1;
  if (before && after) {
    f->failed[i - 1].last = f->failed[i].last;
    for (uint32_t j = i + 1; j < f->failedCount; ++j) f->failed[j - 1] = f->failed[j];
    --f->failedCount;
  } else if (before) {
    f->failed[i - 1].last = fence;
  } else if (after) {
    f->failed[i].first = fence;
  } else if (f->failedCount < kFailureRanges) {
    for (uint32_t j = f->failedCount; j > i; --j) f->failed[j] = f->failed[j - 1];
    f->failed[i] = {fence, fence};
    ++f->failedCount;
  } else {
    f->failureUncertainThrough = fence;
    uint32_t keep = 0;
    for (uint32_t j = 0; j < f->failedCount; ++j)
      if (f->failed[j].last > fence) f->failed[keep++] = f->failed[j];
    f->failedCount = keep;
  }
}
bool wasFailed(const native_powervr_jobs_file *f, uint64_t fence)
{
  if (fence <= f->failureUncertainThrough) return true;
  uint32_t lo = 0, hi = f->failedCount;
  while (lo < hi) {
    const uint32_t mid = lo + (hi - lo) / 2;
    if (f->failed[mid].first <= fence) lo = mid + 1;
    else hi = mid;
  }
  return lo && fence <= f->failed[lo - 1].last;
}

// The primary queue identifies a live context incarnation without retaining pointers in history.
uint64_t contextGeneration(const pvr_context *ctx)
{
  switch (ctx->type) {
  case DRM_PVR_CTX_TYPE_RENDER: return ctx->queues.geometry ? ctx->queues.geometry->generation : 0;
  case DRM_PVR_CTX_TYPE_COMPUTE: return ctx->queues.compute ? ctx->queues.compute->generation : 0;
  case DRM_PVR_CTX_TYPE_TRANSFER_FRAG: return ctx->queues.transfer ? ctx->queues.transfer->generation : 0;
  }
  return 0;
}
void rememberRetired(Job *job)
{
  auto *f = job->file;
  if (!f) return;
  f->retired[f->retiredNext++ % kFailedFences] =
      {job->fence, contextGeneration(job->ctx), job->ctx->ctx_id, job->id, get_dm_type(job->queue), true};
  if (job->failed || atomic_read(&job->ctx->faulty)) recordFailed(f, job->fence);
}

// Both the completion worker and WaitFence can observe a retired UFO. Cache maintenance must happen exactly once,
// while the job is still discoverable under gJobs.lock, before either path exposes success or releases imported pages.
void complete_job_hosts(Job *job)
{
  if (job->hosts_completed) return;
  if (native_powervr_hosts_sync(job->hosts)) job->failed = true;
  job->hosts_completed = true;
  pteCompleted(job);
  // gJobs.lock serializes worker/WaitFence; one snapshot per job, first four successful fragments globally.
  // Completion is observed before these reads, but later queued work can already have overwritten the live registers.
  static unsigned renderSnapshots;
  if (!job->failed && !atomic_read(&job->ctx->faulty) && job->type == DRM_PVR_JOB_TYPE_FRAGMENT && renderSnapshots < 4) {
    native_powervr_log("powervr: render snapshot %u after Fragment job %u fence %llu (live, later work may be active)\n",
                       ++renderSnapshots, job->id, static_cast<unsigned long long>(job->fence));
    native_powervr_dump_render_regs();
  }
}

// Reset notifications (gJobs.lock held): the named job fails and its context becomes faulty when the context is
// guilty of the reset.
static bool gFirmwareDumpedAfterReset;

void applyResets(const ResetNote *notes, uint32_t count)
{
  for (uint32_t n = 0; n < count; n++) {
    const ResetNote &r = notes[n];
    const bool guilty = r.reason == ROGUE_CONTEXT_RESET_REASON_GUILTY_LOCKUP ||
                        r.reason == ROGUE_CONTEXT_RESET_REASON_GUILTY_OVERRUNING ||
                        (r.flags & ROGUE_FWIF_FWCCB_CMD_CONTEXT_RESET_FLAG_PF);
    if (!guilty || (r.flags & ROGUE_FWIF_FWCCB_CMD_CONTEXT_RESET_FLAG_ALL_CTXS)) continue;
    // A completed UFO can retire the last queued job before its FW reset note
    // arrives. Keep the user-owned context faulty even with no queued jobs;
    // otherwise the next submission incorrectly reuses the failed context.
    // Holding ctx_ids protects against erase/free; no context reference is kept.
    xa_lock(&gJobs.dev.ctx_ids);
    pvr_context *ctx = static_cast<pvr_context *>(xa_load(&gJobs.dev.ctx_ids, r.ctx_id));
    const uint64_t generation = ctx ? contextGeneration(ctx) : 0;
    if (ctx) atomic_set(&ctx->faulty, 1);
    // Only an exact, still-unambiguous receipt in this live incarnation is evidence.
    // A context already destroyed, overwritten history or a reused job ID cannot be attributed.
    native_powervr_jobs_file *f;
    list_for_each_entry(f, &gJobs.files, link)
      for (const auto &receipt : f->retired)
        if (generation && receipt.eligible && receipt.generation == generation &&
            receipt.ctx == r.ctx_id && receipt.job == r.job_ref && receipt.dm == r.dm)
          recordFailed(f, receipt.fence);
    xa_unlock(&gJobs.dev.ctx_ids);
    Job *j;
    list_for_each_entry(j, &gJobs.jobs, dev_link) {
      if (j->ctx->ctx_id != r.ctx_id) continue;
      atomic_set(&j->ctx->faulty, 1);
      if (j->id == r.job_ref) j->failed = true;
    }
  }
}

// Lock order: fwccbLock -> jobs try-lock (never wait holding FWCCB). Callbacks (including recursive freelist cleanup)
// run with neither lock held. A consumer that already removed a FW command remains
// visible through fwccbActive until its callback has published the reset note.
// Linux pvr_queue_start() CPU-signals the timeline during recovery to unblock
// dependencies; UFO arrival alone therefore cannot establish successful execution.
bool fwccbPendingLocked()
{
  return gJobs.fwccbActive ||
      READ_ONCE(gJobs.fwccbCtl->read_offset) != READ_ONCE(gJobs.fwccbCtl->write_offset);
}
// Nonwaiting: WaitFence must never execute a freelist/KCCB callback, nor wait
// for a callback or lock on a zero-timeout poll. Pending work is not success.
bool tryLockJobsAfterResets(bool *ready, unsigned *reason = nullptr)
{
  if (reason) *reason = 0;
  if (!IOLockTryLock(gJobs.fwccbLock)) { if (reason) *reason = 1; return false; }
  if (!IOLockTryLock(gJobs.lock)) {
    if (reason) *reason = 2;
    IOLockUnlock(gJobs.fwccbLock);
    return false;
  }
  applyResets(gJobs.resets, gJobs.resetCount);
  for (uint32_t i = 0; i < gJobs.resetCount && gJobs.resetReportCount < kResetNotes; ++i)
    gJobs.resetReports[gJobs.resetReportCount++] = gJobs.resets[i];
  gJobs.resetCount = 0;
  *ready = !fwccbPendingLocked();
  return true; // both locks held; even !ready permits timeout/error processing
}
void unlockJobsAfterResets()
{
  IOLockUnlock(gJobs.lock);
  IOLockUnlock(gJobs.fwccbLock);
}

// The completion thread: pvr_queue_signal_done_fences(), the drm_sched timeout and the FWCCB.
void completionThread(void *, wait_result_t)
{
  for (;;) {
    pvr_fwccb_process(&gJobs.dev);
    ResetNote notes[kResetNotes];
    bool ready;
    if (!tryLockJobsAfterResets(&ready)) { IOSleep(1); continue; }
    const uint32_t nnotes = gJobs.resetReportCount;
    memcpy(notes, gJobs.resetReports, nnotes * sizeof(notes[0]));
    gJobs.resetReportCount = 0;
    list_head done;
    INIT_LIST_HEAD(&done);
    bool dump = false;
    const uint64_t now = nowAbs();
    Job *j, *n;
    list_for_each_entry_safe(j, n, &gJobs.jobs, dev_link) {
      if (ready && queueReached(j->queue, j->seqno)) {
        complete_job_hosts(j);
        // FW can append a reset during the UFO/cache reads. Do not retire it ahead of that note.
        if (fwccbPendingLocked()) { ready = false; continue; }
        rememberRetired(j);
        list_del(&j->dev_link);
        if (j->file) {
          list_del(&j->file_link);
        }
        list_add_tail(&j->dev_link, &done);
      } else if (!j->timed_out && now - j->kicked > gJobs.timeoutAbs) {
        // pvr_queue_timedout_job() + pvr_queue_start(): an unfinished job after the timeout makes its context faulty.
        j->timed_out = true;
        j->failed = true;
        atomic_set(&j->ctx->faulty, 1);
        native_powervr_log("powervr: jobs: job %u (type %d, fence %llu) timed out; queue at %u, job seqno %u\n", j->id,
                           int(j->type), static_cast<unsigned long long>(j->fence), READ_ONCE(*j->queue->timeline_ufo.value),
                           j->seqno);
        dump = true;
      }
    }
    const bool active = !list_empty(&gJobs.jobs);
    IOLockUnlock(gJobs.lock);
    IOLockUnlock(gJobs.fwccbLock);
    if (native_powervr_packet_enabled())
      for (uint32_t i = 0; i < nnotes; ++i)
        native_powervr_packet_hwr(notes[i].ctx_id, notes[i].job_ref, notes[i].dm,
                                  notes[i].fault_address, notes[i].pc_address,
                                  notes[i].reason, notes[i].flags);
    drainPackets();
    drainPtes(dump || nnotes != 0);
    if (nnotes != 0 && !gFirmwareDumpedAfterReset) {
      // A reset path without a firmware snapshot leaves the first broken context unidentified: capture once.
      gFirmwareDumpedAfterReset = true;
      native_powervr_dump_firmware("after a firmware reset", 1500);
    }
    list_for_each_entry_safe(j, n, &done, dev_link) {
      list_del(&j->dev_link);
      pvr_job_release(j);
    }
    if (dump) native_powervr_dump_firmware("job timeout", 1500);
    IOSleep(active ? 1 : 10);
  }
}

// One-time set-up, at the first file (after the firmware boot and the service publication).
int jobsInit(void)
{
  for (;;) {
    int32_t state = __atomic_load_n(&gJobsState, __ATOMIC_ACQUIRE);
    if (state == 2) return 0;
    if (state == 0 && __atomic_compare_exchange_n(&gJobsState, &state, 1, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) break;
    IOSleep(1);
  }
  const pvr_device_features *features = native_powervr_device_features();
  const pvr_device_quirks *quirks = native_powervr_device_quirks();
  const pvr_device_enhancements *enhancements = native_powervr_device_enhancements();
  native_powervr_fw_obj ctl, cmds;
  if (!native_powervr_kernel_fw_mem_ctx() || !features || !quirks || !enhancements || native_powervr_fwccb(&ctl, &cmds)) {
    native_powervr_log("powervr: jobs: firmware or device information not ready\n");
    __atomic_store_n(&gJobsState, 0, __ATOMIC_RELEASE);
    return -ENODEV;
  }
  memset(&gJobs, 0, sizeof(gJobs));
  gJobs.dev.features = *features;
  gJobs.dev.quirks = *quirks;
  gJobs.dev.enhancements = *enhancements;
  pvr_stream_create_musthave_masks(&gJobs.dev);
  pvr_context_device_init(&gJobs.dev);
  xa_init_flags(&gJobs.dev.free_list_ids, XA_FLAGS_ALLOC1);
  xa_init_flags(&gJobs.dev.job_ids, XA_FLAGS_ALLOC1);
  gJobs.lock = IOLockAlloc();
  gJobs.fwccbLock = IOLockAlloc();
  INIT_LIST_HEAD(&gJobs.jobs);
  INIT_LIST_HEAD(&gJobs.files);
  nanoseconds_to_absolutetime(uint64_t(kSchedTimeoutMs) * 1000000ULL, &gJobs.timeoutAbs);
  // The FWCCB through a write-combined alias as well (its control words are shared with the firmware line by line).
  native_powervr_invalidate(ctl.cpu, ctl.size);
  native_powervr_invalidate(cmds.cpu, cmds.size);
  if (gJobs.lock && gJobs.fwccbLock && wcMap(ctl.phys, ctl.size, gJobs.fwccbCtlMap) && wcMap(cmds.phys, cmds.size, gJobs.fwccbMap)) {
    gJobs.fwccbCtl = reinterpret_cast<rogue_fwif_ccb_ctl *>(gJobs.fwccbCtlMap.cpu);
    gJobs.fwccb = reinterpret_cast<rogue_fwif_fwccb_cmd *>(gJobs.fwccbMap.cpu);
    gJobs.fwccbSlots = cmds.size / u32(sizeof(rogue_fwif_fwccb_cmd));
  }
  thread_t thread = nullptr;
  if (!gJobs.fwccbCtl || kernel_thread_start(completionThread, nullptr, &thread) != KERN_SUCCESS) {
    native_powervr_log("powervr: jobs: set-up failed\n");
    __atomic_store_n(&gJobsState, 0, __ATOMIC_RELEASE);   // the locks and maps of this attempt stay allocated
    return -ENOMEM;
  }
  thread_deallocate(thread);
  native_powervr_log("powervr: jobs: ready (FWCCB %u slots)\n", gJobs.fwccbSlots);
  __atomic_store_n(&gJobsState, 2, __ATOMIC_RELEASE);
  return 0;
}

}  // namespace
static_assert(offsetof(native_powervr_jobs_file, pf) == 0, "pvr_file first");

// ---- native_powervr_jobs.h ----
// pvr_drm_driver_open()
extern "C" struct native_powervr_jobs_file *native_powervr_jobs_file_create(void)
{
  if (jobsInit()) return nullptr;
  native_powervr_jobs_file *f = static_cast<native_powervr_jobs_file *>(kzalloc(sizeof(*f), GFP_KERNEL));
  if (!f) return nullptr;
  f->pf.file = &f->df;
  f->pf.pvr_dev = &gJobs.dev;
  INIT_LIST_HEAD(&f->pf.contexts);
  xa_init_flags(&f->pf.ctx_handles, XA_FLAGS_ALLOC1);
  xa_init_flags(&f->pf.free_list_handles, XA_FLAGS_ALLOC1);
  xa_init_flags(&f->pf.hwrt_handles, XA_FLAGS_ALLOC1);
  xa_init_flags(&f->pf.vm_ctx_handles, XA_FLAGS_ALLOC1);
  INIT_LIST_HEAD(&f->jobs);
  f->nextFence = 1;
  IOLockLock(gJobs.lock);
  list_add_tail(&f->link, &gJobs.files);
  IOLockUnlock(gJobs.lock);
  return f;
}

// pvr_drm_driver_postclose(). The user-client layer has destroyed the contexts, datasets and free lists; the Linux
// clean-up still runs for anything left. Queued jobs keep their objects until they complete; the file waits for them
// up to MAX_DEADLINE_MS and then leaves the rest to the completion thread.
extern "C" void native_powervr_jobs_file_destroy(struct native_powervr_jobs_file *file)
{
  if (!file) return;
  pvr_destroy_contexts_for_file(&file->pf);
  pvr_destroy_free_lists_for_file(&file->pf);
  pvr_destroy_hwrt_datasets_for_file(&file->pf);
  bool empty = false;
  for (uint32_t ms = 0; ms < kMaxDeadlineMs; ms++) {
    IOLockLock(gJobs.lock);
    empty = list_empty(&file->jobs);
    IOLockUnlock(gJobs.lock);
    if (empty) break;
    IOSleep(1);
  }
  IOLockLock(gJobs.lock);
  uint32_t left = 0;
  while (!list_empty(&file->jobs)) {
    Job *j = list_first_entry(&file->jobs, Job, file_link);
    list_del(&j->file_link);
    j->file = nullptr;
    left++;
  }
  list_del(&file->link);
  IOLockUnlock(gJobs.lock);
  if (left) native_powervr_log("powervr: jobs: file closed with %u jobs still queued\n", left);
  xa_destroy(&file->pf.ctx_handles);
  xa_destroy(&file->pf.free_list_handles);
  xa_destroy(&file->pf.hwrt_handles);
  xa_destroy(&file->pf.vm_ctx_handles);
  kfree(file);
}

// pvr_ioctl_create_context() -> pvr_context_create()
extern "C" int native_powervr_context_create(struct native_powervr_jobs_file *file, struct native_powervr_vm_context *vm_ctx,
                                             const struct drm_pvr_ioctl_create_context_args *args,
                                             const void *static_context_state, struct native_powervr_context **out)
{
  if (!file || !vm_ctx || !args || !out || (args->static_context_state_len && !static_context_state)) return -EINVAL;
  *out = nullptr;
  native_powervr_context *w = static_cast<native_powervr_context *>(kzalloc(sizeof(*w), GFP_KERNEL));
  if (!w) return -ENOMEM;
  struct drm_pvr_ioctl_create_context_args a = *args;
  a.static_context_state = u64(uintptr_t(static_context_state));   // read by memdup_user() in pvr_context.c
  a.handle = 0;
  // pvr_vm_context_lookup(pvr_file, args->vm_context_handle) finds the resolved VM context.
  int err = xaInsert(&file->pf.vm_ctx_handles, a.vm_context_handle, vm_ctx);
  if (!err) {
    err = pvr_context_create(&file->pf, &a);
    native_powervr_xa_erase(&file->pf.vm_ctx_handles, a.vm_context_handle);
  }
  if (err) {
    kfree(w);
    return err;
  }
  w->file = file;
  w->handle = a.handle;   // in this file's pvr_file, which holds the reference the user-client handle stands for
  xa_lock(&file->pf.ctx_handles);
  w->ctx = xa_load(&file->pf.ctx_handles, a.handle);
  xa_unlock(&file->pf.ctx_handles);
  *out = w;
  return 0;
}

// pvr_ioctl_destroy_context() -> pvr_context_destroy()
extern "C" void native_powervr_context_destroy(struct native_powervr_context *ctx)
{
  if (!ctx) return;
  const int err = pvr_context_destroy(&ctx->file->pf, ctx->handle);
  if (err) native_powervr_log("powervr: jobs: context %u destroy: %d\n", ctx->handle, err);
  kfree(ctx);
}

// pvr_ioctl_create_free_list() -> pvr_free_list_create(); the handle is the user-client layer's.
extern "C" int native_powervr_free_list_create(struct native_powervr_jobs_file *file, struct native_powervr_vm_context *vm_ctx,
                                               const struct drm_pvr_ioctl_create_free_list_args *args,
                                               struct native_powervr_free_list **out)
{
  if (!file || !vm_ctx || !args || !out) return -EINVAL;
  *out = nullptr;
  struct drm_pvr_ioctl_create_free_list_args a = *args;
  int err = xaInsert(&file->pf.vm_ctx_handles, a.vm_context_handle, vm_ctx);
  if (err) return err;
  struct pvr_free_list *free_list = pvr_free_list_create(&file->pf, &a);
  native_powervr_xa_erase(&file->pf.vm_ctx_handles, a.vm_context_handle);
  if (IS_ERR(free_list)) return int(PTR_ERR(free_list));
  *out = reinterpret_cast<native_powervr_free_list *>(free_list);
  return 0;
}
extern "C" void native_powervr_free_list_put(struct native_powervr_free_list *free_list)
{
  pvr_free_list_put(reinterpret_cast<pvr_free_list *>(free_list));
}

// pvr_ioctl_create_hwrt_dataset() -> pvr_hwrt_dataset_create(); pvr_free_list_lookup() finds the resolved free lists
// under args->free_list_handles.
extern "C" int native_powervr_hwrt_dataset_create(struct native_powervr_jobs_file *file,
                                                  const struct drm_pvr_ioctl_create_hwrt_dataset_args *args,
                                                  struct native_powervr_free_list *const free_lists[2],
                                                  struct native_powervr_hwrt_dataset **out)
{
  static_assert(ARRAY_SIZE(args->free_list_handles) == 2, "two free lists");
  if (!file || !args || !free_lists || !free_lists[0] || !free_lists[1] || !out) return -EINVAL;
  *out = nullptr;
  struct drm_pvr_ioctl_create_hwrt_dataset_args a = *args;
  const bool same = a.free_list_handles[0] == a.free_list_handles[1];
  if (same && free_lists[0] != free_lists[1]) return -EINVAL;
  int err = xaInsert(&file->pf.free_list_handles, a.free_list_handles[0], free_lists[0]);
  if (err) return err;
  if (!same) {
    err = xaInsert(&file->pf.free_list_handles, a.free_list_handles[1], free_lists[1]);
    if (err) {
      native_powervr_xa_erase(&file->pf.free_list_handles, a.free_list_handles[0]);
      return err;
    }
  }
  struct pvr_hwrt_dataset *hwrt = pvr_hwrt_dataset_create(&file->pf, &a);
  native_powervr_xa_erase(&file->pf.free_list_handles, a.free_list_handles[0]);
  if (!same) native_powervr_xa_erase(&file->pf.free_list_handles, a.free_list_handles[1]);
  if (IS_ERR(hwrt)) return int(PTR_ERR(hwrt));
  *out = reinterpret_cast<native_powervr_hwrt_dataset *>(hwrt);
  return 0;
}
extern "C" void native_powervr_hwrt_dataset_put(struct native_powervr_hwrt_dataset *hwrt)
{
  pvr_hwrt_dataset_put(reinterpret_cast<pvr_hwrt_dataset *>(hwrt));
}

// pvr_submit_jobs()
extern "C" int native_powervr_submit_jobs(struct native_powervr_jobs_file *file, const struct native_powervr_job_submit *jobs,
                                          uint32_t count, uint64_t *fences, native_powervr_pte_phase pte_phase, void *pte_cookie)
{
  if (!file || !jobs || !fences || !count || count > NativePowerVRAbiMaxJobs) return -EINVAL;
  struct PerfScope {
    uint64_t start;
    PerfScope() : start(perfBegin(PerfSubmit)) {}
    ~PerfScope() { perfEnd(PerfSubmit, start); }
  } perfScope;
  struct StageScope {
    NativeSubmitSample sample={};
    StageScope() {
      if (!__atomic_load_n(&gSubmitStageEnabled,__ATOMIC_ACQUIRE)) return;
      char name[20]={}; proc_selfname(name,sizeof(name));
      if (strcmp(name,"backboardd")) return;
      sample.pid=static_cast<unsigned>(proc_selfpid()); sample.tid=thread_tid(current_thread()); sample.first=nowAbs();
    }
    uint64_t begin() const {return sample.first ? nowAbs() : 0;}
    void end(unsigned stage,uint64_t start) {if(start) sample.ticks[stage]+=nowAbs()-start;}
    ~StageScope() {if(sample.first) {sample.last=nowAbs();sample.ticks[NativeSubmitTotal]=sample.last-sample.first;native_submit_stage_record(&gSubmitStage,sample);}}
  } stage;
  Job *js[NativePowerVRAbiMaxJobs] = {};
  int err = 0;
  uint64_t stageStart=stage.begin();
  // pvr_job_data_init(): every job is created (and checked) before any is queued.
  for (uint32_t i = 0; i < count && !err; i++) err = create_job(file, &jobs[i], &js[i]);
  stage.end(NativeSubmitCreate,stageStart);
  if (err) {
    for (uint32_t i = 0; i < count; i++)
      if (js[i]) pvr_job_release(js[i]);
    return err;
  }
  // All referenced contexts/queues/HWRTs and BO/maps have been retained. The
  // UserClient callback drops its connection lock for the pmap walks. No jobs
  // lock has been acquired, no CCCB writes/kicks have occurred.
  native_powervr_pte_snapshot *pte_snapshots[2];
  native_powervr_pte_record *pte_records[2];
  uint32_t pte_count = 0;
  uint64_t pte_submission = 0;
  for (uint32_t i = 0; i < count; ++i) {
    if (!js[i]->pte_requested || !js[i]->pre_pte) continue;
    if (!pte_submission) pte_submission = __atomic_add_fetch(&gPteSubmission, 1ULL, __ATOMIC_RELAXED);
    js[i]->pre_pte->submission = pte_submission;
    js[i]->pre_pte->index = i;
    pte_snapshots[pte_count] = js[i]->pte;
    pte_records[pte_count++] = js[i]->pre_pte;
  }
  stageStart=stage.begin();
  if (pte_count && pte_phase) err = pte_phase(pte_cookie, pte_snapshots, pte_records, pte_count);
  stage.end(NativeSubmitPte,stageStart);
  if (err) {
    for (uint32_t i = 0; i < count; ++i) pvr_job_release(js[i]);
    return err;
  }
  // pvr_jobs_link_geom_frag()
  for (uint32_t i = 0; i + 1 < count; i++) {
    if (!can_combine_jobs(js[i], js[i + 1])) continue;
    js[i]->paired = js[i + 1];
    js[i + 1]->paired = js[i];
    i++;
  }
  // Space for the whole call in every queue it uses (the CCCB fence of Linux, taken for all jobs at once so that all
  // are queued or none is), with the largest dependency count a job can have.
  uint64_t deadline;
  nanoseconds_to_absolutetime(uint64_t(kSchedTimeoutMs) * 1000000ULL, &deadline);
  deadline += nowAbs();
  const uint64_t lockPerf = perfBegin(PerfLock);
  stageStart=stage.begin();
  IOLockLock(gJobs.lock);
  stage.end(NativeSubmitLock,stageStart);
  perfEnd(PerfLock, lockPerf);
  const uint64_t spacePerf = perfBegin(PerfSpace);
  stageStart=stage.begin();
  for (;;) {
    pvr_queue *queues[NativePowerVRAbiMaxJobs];
    u32 need[NativePowerVRAbiMaxJobs];
    uint32_t nq = 0;
    for (uint32_t i = 0; i < count && !err; i++) {
      if (js[i]->queue->killed) {
        err = -EINVAL;   // the context was destroyed meanwhile
        break;
      }
      uint32_t q = 0;
      while (q < nq && queues[q] != js[i]->queue) q++;
      if (q == nq) {
        queues[nq] = js[i]->queue;
        need[nq++] = 0;
      }
      need[q] += job_cmds_size(js[i], kMaxUfosPerJob);
    }
    bool fits = true;
    for (uint32_t q = 0; q < nq && !err; q++) {
      if (!pvr_cccb_cmdseq_can_fit(&queues[q]->cccb, need[q])) err = -E2BIG;
      else if (!pvr_cccb_cmdseq_fits(&queues[q]->cccb, need[q])) fits = false;
    }
    if (err || fits) break;
    if (nowAbs() > deadline) {
      err = -ETIMEDOUT;
      break;
    }
    IOLockUnlock(gJobs.lock);
    IOSleep(1);
    // Split the space loop around lock reacquisition: relock is not queue wait.
    stage.end(NativeSubmitSpace,stageStart);
    const uint64_t relockStart=stage.begin();
    IOLockLock(gJobs.lock);
    stage.end(NativeSubmitRelock,relockStart);
    stageStart=stage.begin();
  }
  stage.end(NativeSubmitSpace,stageStart);
  perfEnd(PerfSpace, spacePerf);
  if (!err && !reserveTracks(count)) err = -ENOMEM;
  const uint64_t boPerf = perfBegin(PerfBo);
  stageStart=stage.begin();
  for (uint32_t i = 0; i < count && !err; i++) err = native_powervr_hosts_sync(js[i]->hosts);
  stage.end(NativeSubmitBo,stageStart);
  perfEnd(PerfBo, boPerf);
  if (err) {
    IOLockUnlock(gJobs.lock);
    for (uint32_t i = 0; i < count; i++) pvr_job_release(js[i]);
    return err;
  }
  // Nothing fails from here on (push_jobs()). Fences increase in array order and never read as the tunnel word.
  for (uint32_t i = 0; i < count; i++) {
    if (uint32_t(file->nextFence) == NativePowerVRTunnelMagic) file->nextFence++;
    js[i]->fence = file->nextFence++;
  }
  const uint64_t kickPerf = perfBegin(PerfKick);
  stageStart=stage.begin();
  for (uint32_t i = 0; i < count; i++) {
    Job *j = js[i];
    if (j->type == DRM_PVR_JOB_TYPE_FRAGMENT && j->paired) continue;   // written with its geometry job
    run_job(j, j->hwrt ? trackFor(j->hwrt) : nullptr, stage.sample.first ? &stage.sample.ticks[NativeSubmitKccb] : nullptr);
  }
  stage.end(NativeSubmitKick,stageStart);
  perfEnd(PerfKick, kickPerf);
  const uint64_t kicked = nowAbs();
  for (uint32_t i = 0; i < count; i++) {
    Job *j = js[i];
    j->paired = nullptr;
    j->kicked = kicked;
    j->file = file;
    list_add_tail(&j->dev_link, &gJobs.jobs);
    list_add_tail(&j->file_link, &file->jobs);
    fences[i] = j->fence;
  }
  IOLockUnlock(gJobs.lock);
  stage.sample.success=true; // This reports submission, not asynchronous GPU completion.
  return 0;
}

// NativePowerVRMethodWaitFence
extern "C" int native_powervr_fence_wait(struct native_powervr_jobs_file *file, uint64_t fence, uint64_t timeout_ns)
{
  if (!file || !fence || uint32_t(fence) == NativePowerVRTunnelMagic) return -EINVAL;
  // Mode 5 only, on the observed backboardd path. All snapshots are copied
  // under the existing locks and formatted after both locks are released.
  unsigned pid=0; uint64_t tid=0, started=0;
  if (__atomic_load_n(&gNativeWaitSeries.enabled,__ATOMIC_ACQUIRE) && gNativeWaitSeries.mode==5 && timeout_ns) {
    char name[20]={};proc_selfname(name,sizeof(name));
    if (!strcmp(name,"backboardd")) {pid=static_cast<unsigned>(proc_selfpid());tid=thread_tid(current_thread());started=nowAbs();}
  }
  struct Snapshot {
    uint64_t at=0; uint32_t valid=0,found=0,job=0,ctx=0,type=0,seq=0,ufo=0;
    uint32_t fwRead=0,fwWrite=0,fwActive=0,ready=0,latePending=0;
    uint32_t fwLockFail=0,jobsLockFail=0,fwPending=0,ufoPending=0;
  } snap;
  auto finish = [&](int result) {
    const uint64_t end=started?nowAbs():0;
    if (started && native_wait_reason_claim(&gNativeWaitReason,pid,tid,fence,reinterpret_cast<uintptr_t>(file),started,end)) {
      uint64_t startNs=0,endNs=0,sampleNs=0;
      absolutetime_to_nanoseconds(started,&startNs);absolutetime_to_nanoseconds(end,&endNs);
      if (snap.valid) absolutetime_to_nanoseconds(snap.at,&sampleNs);
      IOLog("a04br3 gpu fence reason: clock=mach_absolute_ns pid=%u tid=%llu fence=%llu start_ns=%llu end_ns=%llu result=%d sample_valid=%u sample_ns=%llu found=%u job=%u ctx=%u type=%u seq=%u ufo=%u fw_read=%u fw_write=%u fw_active=%u ready=%u late_pending=%u fw_lock_fail=%u jobs_lock_fail=%u fw_pending=%u ufo_pending=%u limit_busy=%u limit_suppressed=%u limit_evicted=%u\n",
            pid,(unsigned long long)tid,(unsigned long long)fence,(unsigned long long)startNs,(unsigned long long)endNs,result,
            snap.valid,(unsigned long long)sampleNs,snap.found,snap.job,snap.ctx,snap.type,snap.seq,snap.ufo,
            snap.fwRead,snap.fwWrite,snap.fwActive,snap.ready,snap.latePending,snap.fwLockFail,snap.jobsLockFail,
            snap.fwPending,snap.ufoPending,__atomic_load_n(&gNativeWaitReason.busy,__ATOMIC_RELAXED),
            __atomic_load_n(&gNativeWaitReason.suppressed,__ATOMIC_RELAXED),__atomic_load_n(&gNativeWaitReason.evicted,__ATOMIC_RELAXED));
    }
    return result;
  };
  uint64_t deadline = ~0ULL;
  if (timeout_ns < ~0ULL / 2) {
    uint64_t abs = 0;
    nanoseconds_to_absolutetime(timeout_ns, &abs);
    deadline = nowAbs() + abs;
  }
  for (;;) {
    int status = 1;
    bool ready; unsigned reason=0;
    if (!tryLockJobsAfterResets(&ready,started?&reason:nullptr)) {
      if (reason==1 && snap.fwLockFail!=~uint32_t(0)) ++snap.fwLockFail;
      if (reason==2 && snap.jobsLockFail!=~uint32_t(0)) ++snap.jobsLockFail;
      if (!timeout_ns || nowAbs() >= deadline) return finish(-ETIMEDOUT);
      IOSleep(1);
      continue;
    }
    if (started) {
      snap.at=nowAbs();snap.valid=1;snap.found=0;snap.ready=ready;
      snap.fwRead=READ_ONCE(gJobs.fwccbCtl->read_offset);snap.fwWrite=READ_ONCE(gJobs.fwccbCtl->write_offset);
      snap.fwActive=gJobs.fwccbActive;snap.latePending=0;
      if (!ready && snap.fwPending!=~uint32_t(0)) ++snap.fwPending;
    }
    if (fence >= file->nextFence) {
      status = -EINVAL;
    } else {
      Job *found = nullptr, *j;
      list_for_each_entry(j, &file->jobs, file_link) {
        if (j->fence == fence) {
          found = j;
          break;
        }
      }
      if (started && found) {
        snap.found=1;snap.job=found->id;snap.ctx=found->ctx->ctx_id;snap.type=static_cast<uint32_t>(found->type);
        snap.seq=found->seqno;snap.ufo=READ_ONCE(*found->queue->timeline_ufo.value);
        if (int32_t(snap.ufo-snap.seq)<0 && snap.ufoPending!=~uint32_t(0)) ++snap.ufoPending;
      }
      if (!found) status = wasFailed(file, fence) ? -EIO : 0;
      else if (found->failed || atomic_read(&found->ctx->faulty)) status = -EIO;
      else if (ready && queueReached(found->queue, found->seqno)) {
        complete_job_hosts(found);
        status = found->failed ? -EIO : 0;
      }
    }
    // This is the success publication boundary. Firmware notes published later
    // cannot revoke a success already returned to the caller.
    if (status == 0 && (!ready || fwccbPendingLocked())) { if (started) snap.latePending=1; status = 1; }
    unlockJobsAfterResets();
    if (status != 1) return finish(status);
    if (!timeout_ns || nowAbs() >= deadline) return finish(-ETIMEDOUT);
    IOSleep(1);
  }
}
