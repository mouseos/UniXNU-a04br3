// SPDX-License-Identifier: MIT
// NativePowerVR, step 3: the first job through a firmware context. Creates a compute context in the kernel VM context
// (pvr_context_create() for DRM_PVR_CTX_TYPE_COMPUTE with its pvr_queue_create()/pvr_cccb_init()), writes a command
// without work followed by a fence update into its client CCB (pvr_queue_submit_job_to_cccb()), kicks it through the
// KCCB (pvr_cccb_send_kccb_kick()) and waits for the firmware to write the fence (the timeline UFO).
//
// Written after Linux drivers/gpu/drm/imagination (torvalds/linux 734bef548a96e8774d6eef5dfa2026c6edc4e80b; pvr_context.c,
// pvr_queue.c, pvr_cccb.c, pvr_job.c, "GPL-2.0-only OR MIT", used under MIT, Copyright Imagination Technologies Ltd.),
// whose FWIF headers are used unchanged (package/powervr/linux). Firmware objects, cache maintenance and the KCCB come
// from NativePowerVR.cpp through native_powervr_internal.h.
//
// a04br3 differences, all because there is no DRM file, scheduler or user space here:
//  - Linux has no NULL job (DRM_PVR_JOB_TYPE_NULL appears only in a uapi comment; pvr_job.c sets fw_ccb_cmd_type to
//    GEOM/FRAG/CDM/TQ_3D only). The job command written where pvr_queue_submit_job_to_cccb() writes job->cmd is
//    ROGUE_FWIF_CCB_CMD_TYPE_NULL with no payload (the FWIF's command for a kick without work), followed, as in Linux,
//    by ROGUE_FWIF_CCB_CMD_TYPE_UPDATE on the queue's timeline UFO. NATIVE_POWERVR_QUEUE_UPDATE_ONLY=1 leaves the NULL
//    command out and sends the UPDATE alone.
//    How this firmware treats a NULL command on a CDM context is not shown by any Linux or Mesa source; it is what
//    this selftest finds out.
//  - init_compute_fw_objs() copies the static compute context state (the CDM context switch PDS programs) from user
//    space and refuses an empty stream. A NULL command runs nothing on the CDM, so there is nothing to context switch
//    and the state stays zero.
//  - No drm_sched: the job has no dependencies (no FENCE_PR command), its done fence is seqno 1 on a fresh timeline,
//    and completion is found by polling the UFO (Linux: pvr_queue_signal_done_fences() on a FWCCB event).
//  - Nothing is freed: the objects stay in the firmware heap, so the context stays valid for the firmware (Linux tears
//    it down with ROGUE_FWIF_CLEANUP_FWCOMMONCONTEXT in pvr_queue_cleanup_fw_context()).
//  - Every Linux FW object is PVR_BO_FW_FLAGS_DEVICE_UNCACHED and the CCCB is mapped uncached on the CPU too; here the
//    CPU view is cached, so each hand-over gets an explicit native_powervr_clean()/native_powervr_invalidate().
#include <IOKit/IOLib.h>
#include <string.h>
#include <sys/errno.h>
#include "compat/native_powervr_linux_compat.h"
#include "linux/pvr_rogue_fwif.h"
#include "linux/pvr_rogue_fwif_shared.h"
#include "native_powervr_internal.h"

#ifndef NATIVE_POWERVR_QUEUE_UPDATE_ONLY
#define NATIVE_POWERVR_QUEUE_UPDATE_ONLY 0
#endif

// The layouts the firmware reads, checked against the values of pvr_rogue_fwif_check.h / pvr_rogue_fwif_shared_check.h
// (the whole check headers also cover host-pointer structures that are not used here).
static_assert(sizeof(rogue_fwif_ufo) == 8 && offsetof(rogue_fwif_ufo, value) == 4, "rogue_fwif_ufo");
static_assert(sizeof(rogue_fwif_cccb_ctl) == 32 && offsetof(rogue_fwif_cccb_ctl, wrap_mask) == 12, "rogue_fwif_cccb_ctl");
static_assert(sizeof(rogue_fwif_ccb_cmd_header) == 40, "rogue_fwif_ccb_cmd_header");
static_assert(sizeof(rogue_fwif_fwcommoncontext) == 152 && offsetof(rogue_fwif_fwcommoncontext, dm) == 60 &&
              offsetof(rogue_fwif_fwcommoncontext, max_deadline_ms) == 104 &&
              offsetof(rogue_fwif_fwcommoncontext, fw_mem_context_fw_addr) == 136, "rogue_fwif_fwcommoncontext");
static_assert(sizeof(rogue_fwif_static_computecontext_state) == 56, "rogue_fwif_static_computecontext_state");
static_assert(sizeof(rogue_fwif_compute_ctx_state) == 4, "rogue_fwif_compute_ctx_state");
static_assert(sizeof(rogue_fwif_kccb_cmd_kick_data) == 32 && offsetof(rogue_fwif_kccb_cmd_kick_data, client_wrap_mask_update) == 8,
              "rogue_fwif_kccb_cmd_kick_data");

namespace {
// pvr_queue.c
constexpr uint32_t kCtxComputeCccbSizeLog2 = 15;   // CTX_COMPUTE_CCCB_SIZE_LOG2
constexpr uint32_t kMaxDeadlineMs = 30000;         // MAX_DEADLINE_MS
// pvr_context.h enum pvr_context_priority: remap_priority(DRM_PVR_CTX_PRIORITY_NORMAL) gives PVR_CTX_PRIORITY_MEDIUM.
constexpr uint32_t kPvrCtxPriorityMedium = 1;
// Linux allocates ctx_id (pvr_dev->ctx_ids) and job ids (pvr_dev->job_ids) from XA_FLAGS_ALLOC1 xarrays: the first is 1.
constexpr uint32_t kCtxId = 1;
#if !NATIVE_POWERVR_QUEUE_UPDATE_ONLY
constexpr uint32_t kJobId = 1;
#endif
// pvr_queue_fence seqnos start at 1 on a fresh fence context (dma_fence_context_alloc + atomic_inc_return).
constexpr uint32_t kDoneSeqno = 1;
constexpr uint32_t kPollMs = 2000;
constexpr uint32_t kKccbTimeoutMs = 1000;
// Every Linux FW object is its own GEM object, so page aligned.
constexpr uint32_t kFwObjAlign = SZ_4K;

// pvr_cccb_get_size_of_cmd_with_hdr()
constexpr uint32_t cmd_size_with_hdr(uint32_t cmd_size)
{
  return static_cast<uint32_t>(sizeof(rogue_fwif_ccb_cmd_header)) + ALIGN(cmd_size, 8U);
}
constexpr uint32_t kPaddingCommandSize = static_cast<uint32_t>(sizeof(rogue_fwif_ccb_cmd_header));   // PADDING_COMMAND_SIZE

// struct pvr_cccb
struct Cccb {
  native_powervr_fw_obj ctrl_obj, cccb_obj;
  rogue_fwif_cccb_ctl *ctrl;
  uint8_t *cccb;
  uint32_t ctrl_fw_addr, cccb_fw_addr;
  uint32_t size, write_offset, wrap_mask;
};

// get_ccb_space()
uint32_t get_ccb_space(uint32_t w_off, uint32_t r_off, uint32_t ccb_size)
{
  return ((r_off - w_off) + (ccb_size - 1)) & (ccb_size - 1);
}

int fw_object_create(uint32_t size, native_powervr_fw_obj &obj)
{
  // pvr_fw_object_create(): zeroed (pvr_gem objects are cleared at creation).
  const int err = native_powervr_fw_alloc(size, kFwObjAlign, &obj);
  if (err) return err;
  memset(obj.cpu, 0, size);
  return 0;
}

// pvr_cccb_init() with cccb_ctrl_init()
int cccb_init(Cccb &c, uint32_t size_log2)
{
  c.size = 1U << size_log2;
  c.write_offset = 0;
  c.wrap_mask = c.size - 1;
  int err = fw_object_create(static_cast<uint32_t>(sizeof(rogue_fwif_cccb_ctl)), c.ctrl_obj);
  if (err) return err;
  c.ctrl = static_cast<rogue_fwif_cccb_ctl *>(c.ctrl_obj.cpu);
  c.ctrl->write_offset = 0;
  c.ctrl->read_offset = 0;
  c.ctrl->dep_offset = 0;
  c.ctrl->wrap_mask = c.wrap_mask;
  err = fw_object_create(c.size, c.cccb_obj);
  if (err) return err;
  c.cccb = static_cast<uint8_t *>(c.cccb_obj.cpu);
  c.ctrl_fw_addr = c.ctrl_obj.fw_addr;   // pvr_fw_object_get_fw_addr()
  c.cccb_fw_addr = c.cccb_obj.fw_addr;
  native_powervr_clean(c.ctrl, sizeof(*c.ctrl));
  native_powervr_clean(c.cccb, c.size);
  return 0;
}

// pvr_cccb_cmdseq_fits()
bool cccb_cmdseq_fits(const Cccb &c, uint32_t size)
{
  native_powervr_invalidate(c.ctrl, sizeof(*c.ctrl));
  const uint32_t read_offset = *reinterpret_cast<volatile const uint32_t *>(&c.ctrl->read_offset);   // READ_ONCE
  const uint32_t remaining = c.size - c.write_offset;
  // Always ensure we have enough room for a padding command at the end of the CCCB.
  if (size + kPaddingCommandSize > remaining) size += remaining;
  return get_ccb_space(c.write_offset, read_offset, c.size) >= size;
}

// pvr_cccb_write_command_with_header(); the caller checked the space with cccb_cmdseq_fits().
void cccb_write_command_with_header(Cccb &c, uint32_t cmd_type, uint32_t cmd_size, const void *cmd_data,
                                    uint32_t ext_job_ref, uint32_t int_job_ref)
{
  const uint32_t sz_with_hdr = cmd_size_with_hdr(cmd_size);
  rogue_fwif_ccb_cmd_header cmd_header;
  memset(&cmd_header, 0, sizeof(cmd_header));
  cmd_header.cmd_type = cmd_type;
  cmd_header.cmd_size = ALIGN(cmd_size, 8U);
  cmd_header.ext_job_ref = ext_job_ref;
  cmd_header.int_job_ref = int_job_ref;
  const uint32_t remaining = c.size - c.write_offset;
  // Always ensure we have enough room for a padding command at the end of the CCCB.
  if (remaining < sz_with_hdr + kPaddingCommandSize) {
    // Command would need to wrap, so we need to pad the remainder of the CCCB.
    rogue_fwif_ccb_cmd_header pad_cmd;
    memset(&pad_cmd, 0, sizeof(pad_cmd));
    pad_cmd.cmd_type = ROGUE_FWIF_CCB_CMD_TYPE_PADDING;
    pad_cmd.cmd_size = remaining - static_cast<uint32_t>(sizeof(pad_cmd));
    memcpy(&c.cccb[c.write_offset], &pad_cmd, sizeof(pad_cmd));
    native_powervr_clean(&c.cccb[c.write_offset], sizeof(pad_cmd));
    c.write_offset = 0;
  }
  memcpy(&c.cccb[c.write_offset], &cmd_header, sizeof(cmd_header));
  if (cmd_size) memcpy(&c.cccb[c.write_offset + sizeof(cmd_header)], cmd_data, cmd_size);
  native_powervr_clean(&c.cccb[c.write_offset], sz_with_hdr);
  // write_offset stays below size: a wrap was padded above, and size - write_offset >= sz_with_hdr + padding here.
  c.write_offset += sz_with_hdr;
}

// get_dm_type(DRM_PVR_JOB_TYPE_COMPUTE)
constexpr uint32_t kComputeDm = PVR_FWIF_DM_CDM;
}  // namespace

int native_powervr_queue_selftest(void)
{
  // pvr_context_create(): ctx->data, the CPU copy of the FW context, filled by the queues and init_fw_objs() and then
  // copied into its FW object (ctx_fw_data_init()).
  rogue_fwif_fwcomputecontext ctx_data;
  memset(&ctx_data, 0, sizeof(ctx_data));
  // pvr_vm_get_fw_mem_context(ctx->vm_ctx), here the kernel VM context.
  const uint32_t fw_mem_ctx = native_powervr_kernel_fw_mem_ctx();
  if (!fw_mem_ctx) {
    native_powervr_log("queue selftest: no kernel VM context\n");
    return -ENODEV;
  }

  // pvr_queue_create(ctx, DRM_PVR_JOB_TYPE_COMPUTE, ...): queue->ctx_offset = get_ctx_offset() = cdm_context.
  const uint32_t ctx_offset = static_cast<uint32_t>(offsetof(rogue_fwif_fwcomputecontext, cdm_context));
  Cccb cccb;
  memset(&cccb, 0, sizeof(cccb));
  native_powervr_fw_obj reg_state_obj, timeline_ufo_obj, ctx_fw_obj;
  int err = cccb_init(cccb, kCtxComputeCccbSizeLog2);
  // get_ctx_state_size(DRM_PVR_JOB_TYPE_COMPUTE); reg_state_init() writes nothing for compute queues.
  if (!err) err = fw_object_create(static_cast<uint32_t>(sizeof(rogue_fwif_compute_ctx_state)), reg_state_obj);
  if (err) {
    native_powervr_log("queue selftest: FW objects for the queue: %d\n", err);
    return err;
  }
  native_powervr_clean(reg_state_obj.cpu, reg_state_obj.size);

  // init_fw_context()
  rogue_fwif_fwcommoncontext *cctx_fw = &ctx_data.cdm_context;
  cctx_fw->ccbctl_fw_addr = cccb.ctrl_fw_addr;
  cctx_fw->ccb_fw_addr = cccb.cccb_fw_addr;
  cctx_fw->dm = kComputeDm;
  cctx_fw->priority = kPvrCtxPriorityMedium;
  cctx_fw->priority_seq_num = 0;
  cctx_fw->max_deadline_ms = kMaxDeadlineMs;
  cctx_fw->pid = 0;   // task_tgid_nr(current): the kernel
  cctx_fw->server_common_context_id = kCtxId;
  cctx_fw->fw_mem_context_fw_addr = fw_mem_ctx;
  cctx_fw->context_state_addr = reg_state_obj.fw_addr;
  // ccb_meta_dma_addr is for META firmware (the DMA view of the CCCB); Linux never sets it and this core runs MIPS.

  // The queue's timeline UFO (pvr_fw_object_create_and_map(sizeof(*queue->timeline_ufo.value))), value 0.
  err = fw_object_create(static_cast<uint32_t>(sizeof(uint32_t)), timeline_ufo_obj);
  if (err) {
    native_powervr_log("queue selftest: timeline UFO: %d\n", err);
    return err;
  }
  volatile uint32_t *timeline_ufo = static_cast<volatile uint32_t *>(timeline_ufo_obj.cpu);
  native_powervr_clean(timeline_ufo_obj.cpu, sizeof(uint32_t));

  // init_compute_fw_objs(): static_compute_context_state stays zero (see the header comment).
  // pvr_fw_object_create(ctx_size, ..., ctx_fw_data_init)
  err = fw_object_create(static_cast<uint32_t>(sizeof(ctx_data)), ctx_fw_obj);
  if (err) {
    native_powervr_log("queue selftest: FW context: %d\n", err);
    return err;
  }
  memcpy(ctx_fw_obj.cpu, &ctx_data, sizeof(ctx_data));
  native_powervr_clean(ctx_fw_obj.cpu, sizeof(ctx_data));
  const uint32_t ctx_fw_addr = ctx_fw_obj.fw_addr;   // pvr_context_get_fw_addr()
  native_powervr_log("queue selftest: compute context %x (mem ctx %x), CCCB %x ctl %x, state %x, UFO %x\n", ctx_fw_addr,
                     fw_mem_ctx, cccb.cccb_fw_addr, cccb.ctrl_fw_addr, reg_state_obj.fw_addr, timeline_ufo_obj.fw_addr);

  // pvr_queue_job_init(): the sequence must fit (pvr_cccb_cmdseq_can_fit) and, at run time, pvr_cccb_cmdseq_fits().
  rogue_fwif_ufo ufos[1];
  const uint32_t seq_size = (NATIVE_POWERVR_QUEUE_UPDATE_ONLY ? 0 : cmd_size_with_hdr(0)) +
                            cmd_size_with_hdr(static_cast<uint32_t>(sizeof(ufos[0])));
  if (!cccb_cmdseq_fits(cccb, seq_size)) {
    native_powervr_log("queue selftest: CCCB full\n");
    return -EBUSY;
  }

  // pvr_queue_submit_job_to_cccb(): no UFO-backed dependencies, so no FENCE_PR command; the job command; then the
  // update command to signal the job fence.
#if !NATIVE_POWERVR_QUEUE_UPDATE_ONLY
  cccb_write_command_with_header(cccb, ROGUE_FWIF_CCB_CMD_TYPE_NULL, 0, nullptr, kJobId, kJobId);
#endif
  ufos[0].addr = timeline_ufo_obj.fw_addr;
  ufos[0].value = kDoneSeqno;
  cccb_write_command_with_header(cccb, ROGUE_FWIF_CCB_CMD_TYPE_UPDATE, static_cast<uint32_t>(sizeof(ufos[0])), ufos, 0, 0);

  // pvr_cccb_send_kccb_kick() with fill_cmd_kick_data() (no HWRT, so no cleanup control).
  rogue_fwif_kccb_cmd cmd_kick;
  memset(&cmd_kick, 0, sizeof(cmd_kick));
  cmd_kick.cmd_type = ROGUE_FWIF_KCCB_CMD_KICK;
  rogue_fwif_kccb_cmd_kick_data &k = cmd_kick.cmd_data.cmd_kick_data;
  k.context_fw_addr = ctx_fw_addr + ctx_offset;
  k.client_woff_update = cccb.write_offset;
  k.client_wrap_mask_update = cccb.wrap_mask;
  k.num_cleanup_ctl = 0;
  // Make sure the writes to the CCCB are flushed before sending the KICK (wmb(); the cleans above did the cache side).
  __asm__ volatile("dsb sy" ::: "memory");
  err = native_powervr_kccb_submit(&cmd_kick, kKccbTimeoutMs);
  native_powervr_log("queue selftest: KICK (woff %u, wrap mask %x) %s (%d)\n", k.client_woff_update, k.client_wrap_mask_update,
                     err ? "not executed" : "executed", err);
  native_powervr_dump_firmware("right after the KICK", 1500);   // the MTS loop overwrites the ring within seconds

  // The job's done fence: the firmware writes the UFO value once it has processed the UPDATE command.
  uint32_t value = 0;
  uint32_t waited = 0;
  for (;;) {
    native_powervr_invalidate(timeline_ufo_obj.cpu, sizeof(uint32_t));
    value = *timeline_ufo;
    if (value >= kDoneSeqno || waited >= kPollMs) break;
    IOSleep(1);
    waited++;
  }
  native_powervr_invalidate(cccb.ctrl, sizeof(*cccb.ctrl));
  native_powervr_invalidate(ctx_fw_obj.cpu, sizeof(ctx_data));
  const auto *cctx_now = &static_cast<const rogue_fwif_fwcomputecontext *>(ctx_fw_obj.cpu)->cdm_context;
  native_powervr_log("queue selftest: fence %s (UFO %u after %u ms); CCCB ctl read %u write %u dep %u wrap %x, host write %u; "
                     "last failed UFO %x=%u, wait signal %llx\n",
                     value >= kDoneSeqno ? "SIGNALLED" : "not signalled", value, waited, cccb.ctrl->read_offset,
                     cccb.ctrl->write_offset, cccb.ctrl->dep_offset, cccb.ctrl->wrap_mask, cccb.write_offset,
                     cctx_now->last_failed_ufo.addr, cctx_now->last_failed_ufo.value,
                     static_cast<unsigned long long>(cctx_now->wait_signal_address));
  if (value >= kDoneSeqno) return 0;
  return err ? err : -ETIMEDOUT;
}
