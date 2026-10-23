// SPDX-License-Identifier: MIT
// NativePowerVR, step 2: boot the upstream (open source FWIF) PowerVR Rogue MIPS firmware on the a04br3 GE8300 and wait
// for FWIF initialisation. Boot argument a04br3-gpu=2 (after the power-on of package/A04BR3Gpu.cpp). No job submission yet.
//
// Written after Linux drivers/gpu/drm/imagination (torvalds/linux 734bef548a96e8774d6eef5dfa2026c6edc4e80b; pvr_fw.c,
// pvr_fw_mips.c, pvr_vm_mips.c, pvr_fw_startstop.c, pvr_ccb.c, pvr_fw_trace.c, "GPL-2.0-only OR MIT", used under MIT,
// Copyright Imagination Technologies Ltd.), whose FWIF, register and MIPS headers are used unchanged (package/powervr/linux).
// The sequence below follows pvr_fw_init(): FW image validation and layout, the MIPS page table over the 16 MiB firmware
// heap, the ELF segments into the code and data allocations, the boot data, KCCB/FWCCB, the FW structures (osinit,
// sysinit at the config heap offsets), pvr_fw_start() and the wait for sysinit->firmware_started.
//
// a04br3 differences, all because this is the boot of a kernel without the Linux object/VM layers:
//  - One physically contiguous 16 MiB allocation backs the whole firmware heap, and the MIPS page table maps it linearly
//    (Linux maps each FW object's pages as it is created). FW objects are carved from it by offset.
//  - Linux's pvr_mips_wrapper_init() refuses PHYS_BUS_WIDTH <= 32, which this firmware's device info gives: Linux has not
//    booted this combination. The register values below are Linux's, with the device info of this firmware applied
//    (SYS_BUS_SECURE_RESET, BRN 63553, 32-bit PFN mask).
//  - The firmware is the 22.67.54.30 image (no 22.40 image is published). Linux refuses a BVNC mismatch; here it is
//    reported and booted anyway, to see whether this firmware runs on the 22.40 core.
//  - CPU access is through the cached kernel mapping with explicit clean/invalidate (flush_dcache) at each hand-over.
#include <IOKit/IOLib.h>
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include <libkern/libkern.h>
#include <string.h>
#include "compat/native_powervr_linux_compat.h"
#include "linux/pvr_rogue_fwif.h"
#include "linux/pvr_rogue_mips.h"
#include "linux/pvr_rogue_cr_defs.h"
#include "linux/pvr_rogue_defs.h"
#include "linux/pvr_rogue_heap_config.h"
#include "linux/pvr_fw_info.h"
#include "linux/pvr_rogue_mmu_defs.h"
#include "compat/native_powervr_linux_kernel.h"
#include "stub/native_powervr_kernel.h"
extern "C" {   // pvr_mmu.h is C (compiled from kernel/pvr_mmu.c)
#include "kernel/pvr_mmu.h"
}
#include "native_powervr_internal.h"
#include <stdarg.h>
#undef min
#undef max

extern "C" vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern "C" void flush_dcache(vm_offset_t addr, unsigned count, int phys);
extern "C" vm_offset_t phystokv(uint64_t pa);

extern "C" {
// The 22.67.54.30 firmware, embedded by scripts/build-a04br3-board.py from imagination/linux-firmware (pinned SHA-256).
extern const uint8_t native_powervr_fw[];
extern const uint32_t native_powervr_fw_size;
extern volatile uint64_t a04br3_gpu_bvnc;
void native_powervr_boot(void);
volatile int native_powervr_stage;
}

#define ROGUE_FWIF_KCCB_NUMCMDS_LOG2_DEFAULT_A04 7   // ROGUE_FWIF_KCCB_NUMCMDS_LOG2_DEFAULT, pvr_fw.h
#define READ_ONCE_A04(x) (*reinterpret_cast<volatile __typeof__(x) *>(&(x)))

// struct pvr_fw_object of pvr_mmu.c's view (stub/native_powervr_kernel.h): an object in the firmware heap.
struct pvr_fw_object { native_powervr_fw_obj o; };

namespace {
constexpr uint64_t kRegBase = 0x13000000, kRegSize = 0x10000;
constexpr uint32_t kHeapLog2 = 24;                 // ROGUE_FW_HEAP_MIPS_SHIFT (16 MiB)
constexpr uint32_t kHeapSize = 1U << kHeapLog2;
constexpr uint32_t kHeapMask = kHeapSize - 1;
constexpr uint32_t kMipsHeapBase = 0xC0000000;     // ROGUE_FW_HEAP_MIPS_BASE
constexpr uint32_t kConfigGranularity = SZ_64K;    // PVR_ROGUE_FW_CONFIG_HEAP_GRANULARITY
constexpr uint32_t kConfigOffset = kHeapSize - 3 * kConfigGranularity;   // raw size - PVR_ROGUE_FW_CONFIG_HEAP_SIZE
constexpr uint32_t kReserved = SZ_1M;              // ROGUE_FW_HEAP_MIPS_RESERVED_SIZE
// The KCCB kick of the vendor DDK for this core (MediaTek m1.11ED5425693 services/server/devices/rgx/rgxfwutils.c,
// RGXSendCommandRaw(): RGXFWIF_DM_GP | RGX_CR_MTS_SCHEDULE_TASK_COUNTED). Linux's pvr_ccb.c writes the uncounted form for
// its newer META/RISC-V firmware; with this MIPS firmware the uncounted kick left the DM 4 work of a KICK unscheduled.
constexpr uint32_t kKccbKick = (PVR_FWIF_DM_GP & ~ROGUE_CR_MTS_SCHEDULE_DM_CLRMSK) | ROGUE_CR_MTS_SCHEDULE_TASK_COUNTED;
constexpr uint32_t kCoreClockHz = 26000000;        // package/A04BR3Gpu.cpp leaves the core on the 26 MHz crystal

volatile uint8_t *gRegs;
IOBufferMemoryDescriptor *gHeapMem;
uint8_t *gHeap;          // CPU view of the firmware heap
uint64_t gHeapPhys;
uint32_t gNext;          // bump allocator for objects outside the fixed placements
IOBufferMemoryDescriptor *gPtMem;
uint32_t *gPt;
uint64_t gPtPhys;
// The KCCB and the state the later parts (pvr_mmu.c, NativePowerVRQueue.cpp) use after boot.
rogue_fwif_ccb_ctl *gKctl;
rogue_fwif_kccb_cmd *gKccb;
uint32_t *gKccbRtn;
rogue_fwif_osdata *gOsdata;
bool gFwStarted;
uint32_t gKccbExpected;  // osdata->kccb_cmds_executed once every queued command has run (timed-out ones included)
IOLock *gLock;           // the heap bump allocator and the KCCB
pvr_device gPvr;         // what pvr_mmu.c reads of struct pvr_device
pvr_fw_object *gMmuSync; // fw_dev.mem.mmucache_sync_obj
uint32_t gFwMemCtx;      // FW address of the VM context's rogue_fwif_fwmemcontext
pvr_mmu_context *gMmuCtx;
rogue_fwif_tracebuf *gTc;     // trace control, thread 0 buffer and the FW state objects for the dumps after boot
uint32_t *gTrace0;
rogue_fwif_sysdata *gSysdata;
rogue_fwif_hwrinfobuf *gHwrInfo;
rogue_fwif_ccb_ctl *gFwccbCtl;
rogue_fwif_fwccb_cmd *gFwccb;

uint32_t rd32(uint32_t off) { uint32_t v = *reinterpret_cast<volatile uint32_t *>(gRegs + off); __asm__ volatile("dmb oshld" ::: "memory"); return v; }
uint64_t rd64(uint32_t off) { uint64_t v = *reinterpret_cast<volatile uint64_t *>(gRegs + off); __asm__ volatile("dmb oshld" ::: "memory"); return v; }
void wr32(uint32_t off, uint32_t v) { *reinterpret_cast<volatile uint32_t *>(gRegs + off) = v; __asm__ volatile("dsb oshst" ::: "memory"); }
void wr64(uint32_t off, uint64_t v) { *reinterpret_cast<volatile uint64_t *>(gRegs + off) = v; __asm__ volatile("dsb oshst" ::: "memory"); }

void clean(const void *p, size_t n) { flush_dcache(reinterpret_cast<vm_offset_t>(p), static_cast<unsigned>(n), 0); }

struct Obj { uint32_t off, size; void *cpu() const { return gHeap + off; } uint32_t fw() const { return (off & kHeapMask) | kMipsHeapBase; }
             uint64_t phys(uint32_t o = 0) const { return gHeapPhys + off + o; } };

// pvr_fw_object_create_and_map(): a zeroed, 64-byte aligned (4 KiB for page-sized ones) piece of the heap.
bool alloc(uint32_t size, Obj &obj, uint32_t align = 64)
{
  if (gLock) IOLockLock(gLock);
  uint32_t off = (gNext + align - 1) & ~(align - 1);
  if (off + size > kConfigOffset - kReserved) { if (gLock) IOLockUnlock(gLock); return false; }
  gNext = off + size;
  if (gLock) IOLockUnlock(gLock);
  obj = {off, size};
  memset(obj.cpu(), 0, size);
  return true;
}
Obj fixed(uint32_t off, uint32_t size) { Obj o{off, size}; memset(o.cpu(), 0, size); return o; }

const pvr_fw_info_header *gHeader;
const pvr_fw_layout_entry *gLayout;
const pvr_fw_layout_entry *entry(pvr_fw_section_id id)
{
  for (uint32_t i = 0; i < gHeader->layout_entry_num; i++) if (gLayout[i].id == id) return &gLayout[i];
  return nullptr;
}

void vmAndQueueBringUp(void);

bool fail(int stage, const char *what)
{
  native_powervr_stage = -stage;
  IOLog("powervr: stage %d failed: %s\n", stage, what);
  return false;
}

bool boot(void)
{
  // pvr_fw_validate()
  const uint8_t *fw = native_powervr_fw;
  const uint32_t size = native_powervr_fw_size;
  if (size < SZ_4K || size % SZ_4K) return fail(1, "firmware size");
  gHeader = reinterpret_cast<const pvr_fw_info_header *>(fw + size - SZ_4K);
  if (gHeader->info_version != PVR_FW_INFO_VERSION || gHeader->header_len != sizeof(pvr_fw_info_header) ||
      gHeader->layout_entry_size != sizeof(pvr_fw_layout_entry) || gHeader->layout_entry_num > PVR_FW_INFO_MAX_NUM_ENTRIES ||
      !(gHeader->flags & PVR_FW_FLAGS_OPEN_SOURCE))
    return fail(1, "firmware info header");
  gLayout = reinterpret_cast<const pvr_fw_layout_entry *>(fw + size - SZ_4K + gHeader->header_len);
  const uint64_t fwBvnc = gHeader->bvnc;
  IOLog("powervr: firmware %u.%u build %u for BVNC %u.%u.%u.%u, core BVNC %u.%u.%u.%u%s\n", gHeader->fw_version_major,
        gHeader->fw_version_minor, gHeader->fw_version_build, unsigned(fwBvnc >> 48), unsigned((fwBvnc >> 32) & 0xffff),
        unsigned((fwBvnc >> 16) & 0xffff), unsigned(fwBvnc & 0xffff), unsigned(a04br3_gpu_bvnc >> 48),
        unsigned((a04br3_gpu_bvnc >> 32) & 0xffff), unsigned((a04br3_gpu_bvnc >> 16) & 0xffff), unsigned(a04br3_gpu_bvnc & 0xffff),
        fwBvnc == a04br3_gpu_bvnc ? "" : " (mismatch, booting anyway)");
  native_powervr_stage = 1;

  gRegs = reinterpret_cast<volatile uint8_t *>(ml_io_map(kRegBase, kRegSize));
  gLock = IOLockAlloc();
  // The heap and the MIPS page table (ROGUE_MIPSFW_MAX_NUM_PAGETABLE_PAGES x 4 KiB, 4 bytes per 4 KiB heap page).
  gHeapMem = IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task, IOOptionBits(kIODirectionInOut) | IOOptionBits(kIOMemoryPhysicallyContiguous),
                                                              kHeapSize, 0x00000000FFFFF000ULL);
  gPtMem = IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task, IOOptionBits(kIODirectionInOut) | IOOptionBits(kIOMemoryPhysicallyContiguous),
                                                            ROGUE_MIPSFW_MAX_NUM_PAGETABLE_PAGES * SZ_4K, 0x00000000FFFFF000ULL);
  if (!gRegs || !gHeapMem || !gPtMem) return fail(2, "memory");
  IOByteCount len = 0;
  gHeap = static_cast<uint8_t *>(gHeapMem->getBytesNoCopy());
  gHeapPhys = gHeapMem->getPhysicalSegment(0, &len, kIOMemoryMapperNone);
  if (len < kHeapSize) return fail(2, "heap not contiguous");
  gPt = static_cast<uint32_t *>(gPtMem->getBytesNoCopy());
  gPtPhys = gPtMem->getPhysicalSegment(0, &len, kIOMemoryMapperNone);
  if (len < ROGUE_MIPSFW_MAX_NUM_PAGETABLE_PAGES * SZ_4K) return fail(2, "page table not contiguous");
  memset(gHeap, 0, kHeapSize);
  memset(gPt, 0, ROGUE_MIPSFW_MAX_NUM_PAGETABLE_PAGES * SZ_4K);
  IOLog("powervr: heap %llx (16 MiB), MIPS page table %llx\n", gHeapPhys, gPtPhys);
  native_powervr_stage = 2;

  // pvr_vm_mips_map() for the whole heap: uncached (every Linux FW object is PVR_BO_FW_FLAGS_DEVICE_UNCACHED), RW.
  const uint32_t pteFlags = ROGUE_MIPSFW_ENTRYLO_DIRTY_EN | (ROGUE_MIPSFW_UNCACHED_CACHE_POLICY << ROGUE_MIPSFW_ENTRYLO_CACHE_POLICY_SHIFT) |
                            ROGUE_MIPSFW_ENTRYLO_VALID_EN | ROGUE_MIPSFW_ENTRYLO_GLOBAL_EN;
  for (uint32_t pfn = 0; pfn < kHeapSize / SZ_4K; pfn++) {
    const uint64_t pa = gHeapPhys + uint64_t(pfn) * SZ_4K;
    // PHYS_BUS_WIDTH is 32 in this firmware's device info, so the 32-bit PFN mask (the heap is below 4 GiB anyway).
    gPt[pfn] = (uint32_t(((pa >> ROGUE_MIPSFW_LOG2_PAGE_SIZE_4K) << ROGUE_MIPSFW_ENTRYLO_PFN_SHIFT)) & ROGUE_MIPSFW_ENTRYLO_PFN_MASK) | pteFlags;
  }

  // pvr_fw_process(): code at heap offset 0, private data at its fixed address (MIPS has_fixed_data_addr).
  uint32_t codeSize = 0, dataSize = 0;
  for (uint32_t i = 0; i < gHeader->layout_entry_num; i++) {
    const pvr_fw_layout_entry &e = gLayout[i];
    if (e.type == FW_CODE && e.alloc_offset + e.alloc_size > codeSize) codeSize = e.alloc_offset + e.alloc_size;
    if (e.type == FW_DATA && e.alloc_offset + e.alloc_size > dataSize) dataSize = e.alloc_offset + e.alloc_size;
  }
  const pvr_fw_layout_entry *priv = entry(MIPS_PRIVATE_DATA);
  if (!priv || !codeSize || !dataSize) return fail(3, "layout");
  const Obj code = fixed(0, (codeSize + 0xFFFU) & ~0xFFFU);
  const Obj data = fixed(priv->base_addr & kHeapMask, (dataSize + 0xFFFU) & ~0xFFFU);
  gNext = data.off + data.size;
  if (code.size > data.off) return fail(3, "code overlaps data");
  // pvr_fw_process_elf_command_stream() with pvr_fw_find_mmu_segment()
  struct Elf32Ehdr { uint8_t ident[16]; uint16_t type, machine; uint32_t version, entry, phoff, shoff, flags; uint16_t ehsize, phentsize, phnum, shentsize, shnum, shstrndx; };
  struct Elf32Phdr { uint32_t type, offset, vaddr, paddr, filesz, memsz, flags, align; };
  const Elf32Ehdr *eh = reinterpret_cast<const Elf32Ehdr *>(fw);
  if (memcmp(eh->ident, "\x7f" "ELF", 4)) return fail(3, "not ELF");
  for (uint32_t i = 0; i < eh->phnum; i++) {
    const Elf32Phdr *ph = reinterpret_cast<const Elf32Phdr *>(fw + eh->phoff + i * eh->phentsize);
    if (ph->type != 1) continue;   // PT_LOAD
    uint8_t *dst = nullptr;
    for (uint32_t j = 0; j < gHeader->layout_entry_num && !dst; j++) {
      const pvr_fw_layout_entry &e = gLayout[j];
      if (ph->vaddr >= e.base_addr && ph->vaddr + ph->memsz <= e.base_addr + e.alloc_size) {   // pvr_fw_find_mmu_segment
        const uint32_t into = e.alloc_offset + (ph->vaddr - e.base_addr);
        if (e.type == FW_CODE) dst = static_cast<uint8_t *>(code.cpu()) + into;
        else if (e.type == FW_DATA) dst = static_cast<uint8_t *>(data.cpu()) + into;
      }
    }
    if (!dst) { IOLog("powervr: segment %x+%x not in any section\n", ph->vaddr, ph->memsz); return fail(3, "ELF segment"); }
    memcpy(dst, fw + ph->offset, ph->filesz);
    memset(dst + ph->filesz, 0, ph->memsz - ph->filesz);
  }
  native_powervr_stage = 3;

  // pvr_mips_fw_process(): the boot loader configuration in the boot data section.
  const pvr_fw_layout_entry *bootCode = entry(MIPS_BOOT_CODE), *bootData = entry(MIPS_BOOT_DATA),
                            *exc = entry(MIPS_EXCEPTIONS_CODE), *stack = entry(MIPS_STACK);
  if (!bootCode || !bootData || !exc || !stack) return fail(4, "MIPS sections");
  rogue_mipsfw_boot_data *bd = reinterpret_cast<rogue_mipsfw_boot_data *>(static_cast<uint8_t *>(data.cpu()) +
                                                                          bootData->alloc_offset + ROGUE_MIPSFW_BOOTLDR_CONF_OFFSET);
  bd->stack_phys_addr = data.phys(stack->alloc_offset);
  bd->reg_base = kRegBase;
  for (uint32_t p = 0; p < ROGUE_MIPSFW_MAX_NUM_PAGETABLE_PAGES; p++) bd->pt_phys_addr[p] = gPtPhys + p * SZ_4K;
  bd->pt_log2_page_size = ROGUE_MIPSFW_LOG2_PAGE_SIZE_4K;
  bd->pt_num_pages = ROGUE_MIPSFW_MAX_NUM_PAGETABLE_PAGES;
  bd->reserved1 = 0;
  bd->reserved2 = 0;
  const uint64_t bootCodePhys = code.phys(bootCode->alloc_offset), bootDataPhys = data.phys(bootData->alloc_offset),
                 excPhys = code.phys(exc->alloc_offset);

  // pvr_kccb_init()/pvr_fwccb_init() (pvr_ccb_init), the KCCB return slots and pvr_fw_create_structures().
  Obj kccbCtl, kccb, kccbRtn, fwccbCtl, fwccb, powerSync, hwrInfo, mmuSync, sysdataO, fault, gpuUtil, runtimeCfg, traceCtl,
      trace[MAX_THREAD_NUM], osdataO;
  const uint32_t kccbCmds = 1U << ROGUE_FWIF_KCCB_NUMCMDS_LOG2_DEFAULT_A04, fwccbCmds = 1U << 5;
  if (!alloc(sizeof(rogue_fwif_ccb_ctl), kccbCtl) || !alloc(kccbCmds * sizeof(rogue_fwif_kccb_cmd), kccb) ||
      !alloc(kccbCmds * sizeof(uint32_t), kccbRtn) || !alloc(sizeof(rogue_fwif_ccb_ctl), fwccbCtl) ||
      !alloc(fwccbCmds * sizeof(rogue_fwif_fwccb_cmd), fwccb) || !alloc(sizeof(uint32_t), powerSync) ||
      !alloc(sizeof(rogue_fwif_hwrinfobuf), hwrInfo) || !alloc(sizeof(uint32_t), mmuSync) ||
      !alloc(sizeof(rogue_fwif_sysdata), sysdataO) || !alloc(SZ_4K, fault, SZ_4K) ||
      !alloc(sizeof(rogue_fwif_gpu_util_fwcb), gpuUtil) || !alloc(sizeof(rogue_fwif_runtime_cfg), runtimeCfg) ||
      !alloc(sizeof(rogue_fwif_tracebuf), traceCtl) || !alloc(sizeof(rogue_fwif_osdata), osdataO))
    return fail(5, "FW objects");
  for (uint32_t t = 0; t < MAX_THREAD_NUM; t++)
    if (!alloc(ROGUE_FW_TRACE_BUF_DEFAULT_SIZE_IN_DWORDS * sizeof(uint32_t), trace[t])) return fail(5, "trace buffers");
  const Obj connCtl = fixed(kConfigOffset + 0, sizeof(rogue_fwif_connection_ctl));
  const Obj osinitO = fixed(kConfigOffset + kConfigGranularity, sizeof(rogue_fwif_osinit));
  const Obj sysinitO = fixed(kConfigOffset + 2 * kConfigGranularity, sizeof(rogue_fwif_sysinit));

  auto *kctl = static_cast<rogue_fwif_ccb_ctl *>(kccbCtl.cpu());
  kctl->write_offset = 0; kctl->read_offset = 0; kctl->wrap_mask = kccbCmds - 1; kctl->cmd_size = sizeof(rogue_fwif_kccb_cmd);
  auto *fctl = static_cast<rogue_fwif_ccb_ctl *>(fwccbCtl.cpu());
  fctl->write_offset = 0; fctl->read_offset = 0; fctl->wrap_mask = fwccbCmds - 1; fctl->cmd_size = sizeof(rogue_fwif_fwccb_cmd);
  for (uint32_t i = 0; i < SZ_4K / 4; i++) static_cast<uint32_t *>(fault.cpu())[i] = 0xdeadbee0;   // fw_fault_page_init
  static_cast<rogue_fwif_gpu_util_fwcb *>(gpuUtil.cpu())->last_word = PVR_FWIF_GPU_UTIL_STATE_IDLE;
  auto *rt = static_cast<rogue_fwif_runtime_cfg *>(runtimeCfg.cpu());
  rt->core_clock_speed = kCoreClockHz; rt->active_pm_latency_ms = 0; rt->active_pm_latency_persistant = true;
  rt->default_dusts_num_init = 1;   // num_clusters
  rt->wdg_period_us = 0;
  auto *tc = static_cast<rogue_fwif_tracebuf *>(traceCtl.cpu());
  tc->tracebuf_size_in_dwords = ROGUE_FW_TRACE_BUF_DEFAULT_SIZE_IN_DWORDS;
  tc->tracebuf_flags = 0;
  // Every group for the bring-up. The MTS group logs a background/interrupt task pair about 700 times a second here, so
  // the 12000-word ring keeps about 5 s: dumpFirmware() reads the whole ring right after the tests.
  tc->log_type = ROGUE_FWIF_LOG_TYPE_TRACE | ROGUE_FWIF_LOG_TYPE_GROUP_MASK;
  for (uint32_t t = 0; t < MAX_THREAD_NUM; t++) {
    tc->tracebuf[t].trace_buffer_fw_addr = trace[t].fw();
    tc->tracebuf[t].trace_buffer = static_cast<u32 *>(trace[t].cpu());
    tc->tracebuf[t].trace_pointer = 0;
  }
  static_cast<rogue_fwif_osdata *>(osdataO.cpu())->power_sync_fw_addr = powerSync.fw();
  static_cast<rogue_fwif_sysdata *>(sysdataO.cpu())->config_flags = 0;   // SLC >= 4 KiB: DM overlap stays on
  auto *os = static_cast<rogue_fwif_osinit *>(osinitO.cpu());
  os->kernel_ccbctl_fw_addr = kccbCtl.fw(); os->kernel_ccb_fw_addr = kccb.fw(); os->kernel_ccb_rtn_slots_fw_addr = kccbRtn.fw();
  os->firmware_ccbctl_fw_addr = fwccbCtl.fw(); os->firmware_ccb_fw_addr = fwccb.fw();
  os->work_est_firmware_ccbctl_fw_addr = 0; os->work_est_firmware_ccb_fw_addr = 0;
  os->rogue_fwif_hwr_info_buf_ctl_fw_addr = hwrInfo.fw(); os->fw_os_data_fw_addr = osdataO.fw();
  os->hwr_debug_dump_limit = 0;
  rogue_fwif_compchecks_bvnc_init(&os->rogue_comp_checks.hw_bvnc);
  rogue_fwif_compchecks_bvnc_init(&os->rogue_comp_checks.fw_bvnc);
  auto *sys = static_cast<rogue_fwif_sysinit *>(sysinitO.cpu());
  sys->fault_phys_addr = fault.phys();
  sys->pds_exec_base = ROGUE_PDSCODEDATA_HEAP_BASE;
  sys->usc_exec_base = ROGUE_USCCODE_HEAP_BASE;
  sys->runtime_cfg_fw_addr = runtimeCfg.fw();
  sys->trace_buf_ctl_fw_addr = traceCtl.fw();
  sys->fw_sys_data_fw_addr = sysdataO.fw();
  sys->gpu_util_fw_cb_ctl_fw_addr = gpuUtil.fw();
  sys->counter_dump_ctl.buffer_fw_addr = 0; sys->counter_dump_ctl.size_in_dwords = 0;
  sys->align_checks = 0; sys->filter_flags = 0; sys->hw_perf_filter = 0;
  sys->firmware_perf = FW_PERF_CONF_NONE;
  sys->initial_core_clock_speed = kCoreClockHz;
  sys->active_pm_latency_ms = 0;
  sys->gpio_validation_mode = ROGUE_FWIF_GPIO_VAL_OFF;
  sys->firmware_started = false;
  sys->marker_val = 1;
  (void)connCtl;
  static pvr_fw_object mmuSyncObj;
  mmuSyncObj.o = {mmuSync.cpu(), mmuSync.fw(), mmuSync.phys(), mmuSync.size};
  gMmuSync = &mmuSyncObj;   // pvr_fw.c: fw_mem->mmucache_sync_obj (PVR_SYNC_OBJ_SIZE, uncached)
  clean(gHeap, kHeapSize);
  clean(gPt, ROGUE_MIPSFW_MAX_NUM_PAGETABLE_PAGES * SZ_4K);
  native_powervr_stage = 5;

  // pvr_fw_start() with this firmware's device info: pbe2_in_xe, sys_bus_secure_reset, no xe_tpu2, BRN 63553.
  const uint64_t softResetMask = ROGUE_CR_SOFT_RESET__PBE2_XE__MASKFULL;
  wr32(ROGUE_CR_SYS_BUS_SECURE, 0); (void)rd32(ROGUE_CR_SYS_BUS_SECURE);
  wr64(ROGUE_CR_SOFT_RESET, softResetMask); (void)rd64(ROGUE_CR_SOFT_RESET);
  wr64(ROGUE_CR_SOFT_RESET, softResetMask ^ ROGUE_CR_SOFT_RESET_RASCALDUSTS_EN); (void)rd64(ROGUE_CR_SOFT_RESET);
  wr64(ROGUE_CR_SOFT_RESET, ROGUE_CR_SOFT_RESET_GARTEN_EN); (void)rd64(ROGUE_CR_SOFT_RESET);
  // rogue_slc_init(): SLC line 512 bits < 1024, so bypass the burst combiner; quirk 71242 is not set for 22.x.
  wr32(ROGUE_CR_SLC_CTRL_MISC, (rd32(ROGUE_CR_SLC_CTRL_MISC) & ROGUE_CR_SLC_CTRL_MISC_ENABLE_PSG_HAZARD_CHECK_EN) |
                               ROGUE_CR_SLC_CTRL_MISC_ADDR_DECODE_MODE_PVR_HASH1 | ROGUE_CR_SLC_CTRL_MISC_BYPASS_BURST_COMBINER_EN);
  // pvr_mips_wrapper_init()
  const uint64_t remap = ROGUE_MIPSFW_BOOT_REMAP_LOG2_SEGMENT_SIZE;
  // PHYS_BUS_WIDTH is 32 here: the register bank is decoded at the GPU's physical register address, as the vendor DDK
  // does for a bus of 32 bits or less (m1.11ED5425693 rgxstartstop.c RGXInitMipsProcWrapper(): RGXAcquireGPURegsAddr()
  // >> RGXMIPSFW_WRAPPER_CONFIG_REGBANK_ADDR_ALIGN). Linux only has the > 32-bit form (ROGUE_MIPSFW_REGISTERS_VIRTUAL_BASE)
  // and refuses narrower buses; with that form the firmware's register accesses missed the bank (timer read 0, MTS tasks
  // never retired, DM kicks never dispatched).
  static_assert((kRegBase & 0x7FFFFULL) == 0, "the register bank must be 512 KiB aligned (rgxinit.c RGXSetPowerParams)");
  wr64(ROGUE_CR_MIPS_WRAPPER_CONFIG, (kRegBase >> ROGUE_MIPSFW_WRAPPER_CONFIG_REGBANK_ADDR_ALIGN) |
                                     ROGUE_CR_MIPS_WRAPPER_CONFIG_BOOT_ISA_MODE_MICROMIPS);
  wr64(ROGUE_CR_MIPS_ADDR_REMAP1_CONFIG1, ROGUE_MIPSFW_BOOT_REMAP_PHYS_ADDR_IN | ROGUE_CR_MIPS_ADDR_REMAP1_CONFIG1_MODE_ENABLE_EN);
  wr64(ROGUE_CR_MIPS_ADDR_REMAP1_CONFIG2, (bootCodePhys & ~ROGUE_CR_MIPS_ADDR_REMAP1_CONFIG2_ADDR_OUT_CLRMSK) | remap);
  // BRN 63553 (set in this firmware's device info): remap 5 to the boot code as well, against unmapped accesses to 0.
  wr64(ROGUE_CR_MIPS_ADDR_REMAP5_CONFIG1, ROGUE_CR_MIPS_ADDR_REMAP5_CONFIG1_MODE_ENABLE_EN);
  wr64(ROGUE_CR_MIPS_ADDR_REMAP5_CONFIG2, (bootCodePhys & ~ROGUE_CR_MIPS_ADDR_REMAP5_CONFIG2_ADDR_OUT_CLRMSK) | remap);
  wr64(ROGUE_CR_MIPS_ADDR_REMAP2_CONFIG1, ROGUE_MIPSFW_DATA_REMAP_PHYS_ADDR_IN | ROGUE_CR_MIPS_ADDR_REMAP2_CONFIG1_MODE_ENABLE_EN);
  wr64(ROGUE_CR_MIPS_ADDR_REMAP2_CONFIG2, (bootDataPhys & ~ROGUE_CR_MIPS_ADDR_REMAP2_CONFIG2_ADDR_OUT_CLRMSK) | remap);
  wr64(ROGUE_CR_MIPS_ADDR_REMAP3_CONFIG1, ROGUE_MIPSFW_CODE_REMAP_PHYS_ADDR_IN | ROGUE_CR_MIPS_ADDR_REMAP3_CONFIG1_MODE_ENABLE_EN);
  wr64(ROGUE_CR_MIPS_ADDR_REMAP3_CONFIG2, (excPhys & ~ROGUE_CR_MIPS_ADDR_REMAP3_CONFIG2_ADDR_OUT_CLRMSK) | remap);
  wr64(ROGUE_CR_MTS_GARTEN_WRAPPER_CONFIG, ROGUE_CR_MTS_GARTEN_WRAPPER_CONFIG_IDLE_CTRL_META);
  wr32(ROGUE_CR_MIPS_DEBUG_CONFIG, 0);
  // rogue_axi_ace_list_init()
  wr64(ROGUE_CR_AXI_ACE_LITE_CONFIGURATION,
       (3ULL << ROGUE_CR_AXI_ACE_LITE_CONFIGURATION_AWDOMAIN_NON_SNOOPING_SHIFT) |
       (3ULL << ROGUE_CR_AXI_ACE_LITE_CONFIGURATION_ARDOMAIN_NON_SNOOPING_SHIFT) |
       (2ULL << ROGUE_CR_AXI_ACE_LITE_CONFIGURATION_ARDOMAIN_CACHE_MAINTENANCE_SHIFT) |
       (2ULL << ROGUE_CR_AXI_ACE_LITE_CONFIGURATION_AWDOMAIN_COHERENT_SHIFT) |
       (2ULL << ROGUE_CR_AXI_ACE_LITE_CONFIGURATION_ARDOMAIN_COHERENT_SHIFT) |
       (2ULL << ROGUE_CR_AXI_ACE_LITE_CONFIGURATION_AWCACHE_COHERENT_SHIFT) |
       (2ULL << ROGUE_CR_AXI_ACE_LITE_CONFIGURATION_ARCACHE_COHERENT_SHIFT) |
       (2ULL << ROGUE_CR_AXI_ACE_LITE_CONFIGURATION_ARCACHE_CACHE_MAINTENANCE_SHIFT));
  // The MTS thread associations stay as reset leaves them (the vendor DDK RGXStart() writes none; the firmware sets them).
  IODelay(3);
  wr64(ROGUE_CR_SOFT_RESET, 0); (void)rd64(ROGUE_CR_SOFT_RESET);
  IODelay(3);
  native_powervr_stage = 6;
  IOLog("powervr: MIPS released (boot code %llx, data %llx, exceptions %llx)\n", bootCodePhys, bootDataPhys, excPhys);

  // pvr_wait_for_fw_boot(), with a longer limit for the 26 MHz core.
  bool started = false;
  for (unsigned ms = 0; ms < 10000 && !started; ms += 10) {
    clean(&sys->firmware_started, sizeof(sys->firmware_started));
    started = READ_ONCE_A04(sys->firmware_started);
    if (!started) IOSleep(10);
  }
  clean(gHeap + traceCtl.off, traceCtl.size);
  clean(trace[0].cpu(), 64 * 4);
  const uint32_t *t0 = static_cast<const uint32_t *>(trace[0].cpu());
  IOLog("powervr: firmware %s; MIPS exception status %llx wrapper status %llx; trace pointer %u: %x %x %x %x %x %x %x %x\n",
        started ? "STARTED" : "did not start", rd64(ROGUE_CR_MIPS_EXCEPTION_STATUS), rd64(ROGUE_CR_MIPS_WRAPPER_STATUS),
        tc->tracebuf[0].trace_pointer, t0[0], t0[1], t0[2], t0[3], t0[4], t0[5], t0[6], t0[7]);
  if (tc->tracebuf[0].assert_buf.line_num)
    IOLog("powervr: firmware assert %.64s:%u %.64s\n", tc->tracebuf[0].assert_buf.path, tc->tracebuf[0].assert_buf.line_num,
          tc->tracebuf[0].assert_buf.info);
  clean(os, sizeof(*os));
  IOLog("powervr: compchecks hw_bvnc %llx fw_bvnc %llx fw_processor_version %x updated %u\n",
        static_cast<unsigned long long>(os->rogue_comp_checks.hw_bvnc.bvnc), static_cast<unsigned long long>(os->rogue_comp_checks.fw_bvnc.bvnc),
        os->rogue_comp_checks.fw_processor_version, unsigned(os->rogue_comp_checks.updated));
  if (!started) {
    // pvr_fw_stop()'s end state: everything back into soft reset, so the MIPS stops mastering the bus.
    wr64(ROGUE_CR_SOFT_RESET, softResetMask); (void)rd64(ROGUE_CR_SOFT_RESET);
  }
  if (started) {
    // First command (pvr_kccb_send_cmd_reserved_powered() and pvr_kccb_wait_for_completion()): a health check in KCCB
    // slot 0 with its return slot cleared, the write offset advanced, then the MTS kick for the GP data master.
    auto *cmds = static_cast<rogue_fwif_kccb_cmd *>(kccb.cpu());
    auto *rtn = static_cast<uint32_t *>(kccbRtn.cpu());
    clean(kctl, sizeof(*kctl));
    const uint32_t slot = kctl->write_offset;
    memset(&cmds[slot], 0, sizeof(cmds[slot]));
    cmds[slot].cmd_type = ROGUE_FWIF_KCCB_CMD_HEALTH_CHECK;
    rtn[slot] = ROGUE_FWIF_KCCB_RTN_SLOT_NO_RESPONSE;
    clean(&cmds[slot], sizeof(cmds[slot]));
    clean(&rtn[slot], sizeof(rtn[slot]));
    __asm__ volatile("dsb sy" ::: "memory");
    kctl->write_offset = (slot + 1) & kctl->wrap_mask;
    clean(kctl, sizeof(*kctl));
    __asm__ volatile("dsb sy" ::: "memory");
    // The host side of the connection (DDK native KM: eConnectionOsState = ACTIVE once its initialisation is done; Linux
    // leaves it OFFLINE). The firmware reported its side ACTIVE and OS 0 active, but did not take the first kick.
    auto *conn = static_cast<rogue_fwif_connection_ctl *>(connCtl.cpu());
    conn->connection_os_state = ROGUE_FW_CONNECTION_OS_ACTIVE;
    clean(conn, sizeof(*conn));
    __asm__ volatile("dsb sy" ::: "memory");
    uint32_t result = 0;
    auto mtsState = [&](const char *when) {
      IOLog("powervr: MTS %s: schedule_enable %x bgctx %x intctx %x assoc bg %x/%x int %x/%x counted %llx gpu_int %x event %llx os0_event %x mips_irq %x\n",
            when, rd32(ROGUE_CR_MTS_SCHEDULE_ENABLE), rd32(ROGUE_CR_MTS_BGCTX), rd32(ROGUE_CR_MTS_INTCTX),
            rd32(ROGUE_CR_MTS_BGCTX_THREAD0_DM_ASSOC), rd32(ROGUE_CR_MTS_BGCTX_THREAD1_DM_ASSOC),
            rd32(ROGUE_CR_MTS_INTCTX_THREAD0_DM_ASSOC), rd32(ROGUE_CR_MTS_INTCTX_THREAD1_DM_ASSOC),
            rd64(ROGUE_CR_MTS_BGCTX_COUNTED_SCHEDULE), rd32(ROGUE_CR_MTS_GPU_INT_STATUS), rd64(ROGUE_CR_EVENT_STATUS),
            rd32(ROGUE_CR_IRQ_OS0_EVENT_STATUS), rd32(ROGUE_CR_MIPS_WRAPPER_IRQ_STATUS));
    };
    mtsState("before kick");
    wr32(ROGUE_CR_MTS_SCHEDULE, kKccbKick);
    for (unsigned ms = 0; ms < 5000 && !(result & ROGUE_FWIF_KCCB_RTN_SLOT_CMD_EXECUTED); ms += 10) {
      IOSleep(10);
      clean(&rtn[slot], sizeof(rtn[slot]));
      result = READ_ONCE_A04(rtn[slot]);
    }
    clean(kctl, sizeof(*kctl));
    mtsState("after kick");
    clean(kctl, sizeof(*kctl));
    clean(gHeap + traceCtl.off, traceCtl.size);
    IOLog("powervr: KCCB health check %s (return 0x%x, read offset %u write offset %u), trace pointer %u\n",
          (result & ROGUE_FWIF_KCCB_RTN_SLOT_CMD_EXECUTED) ? "EXECUTED" : "not executed", result, kctl->read_offset,
          kctl->write_offset, tc->tracebuf[0].trace_pointer);
    // The firmware's state for the host: power state, OS 0 runtime flags, faults, the connection control, FWCCB and the
    // whole trace of thread 0 (decode with scripts/decode-powervr-trace.py, the format table of pvr_rogue_fwif_sf.h).
    auto *sd = static_cast<rogue_fwif_sysdata *>(sysdataO.cpu());
    auto *cc = conn;
    clean(sd, sizeof(*sd)); clean(cc, sizeof(*cc)); clean(fctl, sizeof(*fctl));
    IOLog("powervr: sysdata pow_state %u os0 state %u fl_ok %u faults %u; connection fw %u os %u; FWCCB read %u write %u\n",
          unsigned(sd->pow_state), unsigned(sd->os_runtime_flags_mirror[0].os_state), unsigned(sd->os_runtime_flags_mirror[0].fl_ok),
          sd->fw_faults, unsigned(cc->connection_fw_state), unsigned(cc->connection_os_state), fctl->read_offset, fctl->write_offset);
    const uint32_t words = tc->tracebuf[0].trace_pointer < 512 ? tc->tracebuf[0].trace_pointer : 512;
    clean(trace[0].cpu(), words * 4);
    for (uint32_t w = 0; w < words; w += 8)
      IOLog("powervr trace %03u: %08x %08x %08x %08x %08x %08x %08x %08x\n", w, t0[w], t0[w + 1], t0[w + 2], t0[w + 3], t0[w + 4],
            t0[w + 5], t0[w + 6], t0[w + 7]);
    // Linux's watchdog counts commands through osdata->kccb_cmds_executed (pvr_watchdog_kccb_stalled()).
    auto *od = static_cast<rogue_fwif_osdata *>(osdataO.cpu());
    clean(od, sizeof(*od));
    IOLog("powervr: osdata kccb_cmds_executed %u\n", od->kccb_cmds_executed);
    if (od->kccb_cmds_executed >= 1) result |= ROGUE_FWIF_KCCB_RTN_SLOT_CMD_EXECUTED;
    auto *hw = static_cast<rogue_fwif_hwrinfobuf *>(hwrInfo.cpu());
    clean(hw, sizeof(*hw));
    IOLog("powervr: MIPS exception status %llx wrapper status %llx; HWR counter %u; fault0 data %x %.64s:%u %.64s\n",
          rd64(ROGUE_CR_MIPS_EXCEPTION_STATUS), rd64(ROGUE_CR_MIPS_WRAPPER_STATUS), hw->hwr_counter, sd->fault_info[0].data,
          sd->fault_info[0].fault_buf.path, sd->fault_info[0].fault_buf.line_num, sd->fault_info[0].fault_buf.info);
    if (tc->tracebuf[0].assert_buf.line_num)
      IOLog("powervr: firmware assert %.64s:%u %.64s\n", tc->tracebuf[0].assert_buf.path, tc->tracebuf[0].assert_buf.line_num,
            tc->tracebuf[0].assert_buf.info);
    if (!(result & ROGUE_FWIF_KCCB_RTN_SLOT_CMD_EXECUTED)) started = false;
    // Hand the KCCB to native_powervr_kccb_submit() for every later command.
    gKctl = kctl; gKccb = cmds; gKccbRtn = rtn; gOsdata = od;
    gTc = tc; gTrace0 = static_cast<uint32_t *>(trace[0].cpu()); gSysdata = sd; gHwrInfo = hw; gFwccbCtl = fctl;
    gFwccb = static_cast<rogue_fwif_fwccb_cmd *>(fwccb.cpu());
  }
  gFwStarted = started;
  native_powervr_stage = started ? 8 : -8;
  if (started) vmAndQueueBringUp();
  return started;
}
}  // namespace

extern "C" kern_return_t kernel_thread_start(void (*)(void *, wait_result_t), void *, thread_t *);
extern "C" void thread_deallocate(thread_t);
static void bootThread(void *, wait_result_t) { boot(); }

void native_powervr_boot(void)
{
  unsigned mode = 0;
  if (!PE_parse_boot_argn("a04br3-gpu", &mode, sizeof(mode)) || mode < 2) return;
  if (!a04br3_gpu_bvnc || a04br3_gpu_bvnc == ~0ULL) { IOLog("powervr: no core identified; firmware not booted\n"); return; }
  // Its own thread: the boot waits up to 10 s for the firmware and must not hold up unixnu_board_init().
  thread_t thread = nullptr;
  if (kernel_thread_start(bootThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}

// ---- The kernel side after firmware boot: the Linux API pvr_mmu.c needs, the KCCB for later commands, the VM context ----
// Allocation (include/linux/slab.h): IOMalloc with the size kept in front, as IOFree needs it.
extern "C" void *kmalloc(size_t size, gfp_t flags)
{
  if (size > (1U << 30)) return nullptr;
  auto *p = static_cast<uint64_t *>(IOMalloc(size + 16));
  if (!p) return nullptr;
  p[0] = size;
  if (flags & __GFP_ZERO) memset(p + 2, 0, size);
  return p + 2;
}
extern "C" void *kzalloc(size_t size, gfp_t flags) { return kmalloc(size, flags | __GFP_ZERO); }
extern "C" void *kcalloc(size_t n, size_t size, gfp_t flags)
{
  size_t total = 0;
  if (__builtin_mul_overflow(n, size, &total)) return nullptr;
  return kzalloc(total, flags);
}
extern "C" void kfree(const void *p)
{
  if (!p) return;
  auto *base = const_cast<uint64_t *>(static_cast<const uint64_t *>(p)) - 2;
  IOFree(base, base[0] + 16);
}

// Pages (include/linux/gfp.h, dma-mapping.h, vmalloc.h): one 4 KiB physically contiguous buffer below 4 GiB each (this
// firmware's device info gives PHYS_BUS_WIDTH 32), mapped once by the kernel. CPU writes reach the GPU through the DMA sync
// calls (clean to the point of coherency), as on Linux's non-coherent arm64 path.
struct page { IOBufferMemoryDescriptor *md; void *cpu; uint64_t phys; };
extern "C" struct page *alloc_page(gfp_t flags)
{
  (void)flags;
  auto *pg = static_cast<struct page *>(kzalloc(sizeof(struct page), GFP_KERNEL));
  if (!pg) return nullptr;
  pg->md = IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task, IOOptionBits(kIODirectionInOut) | IOOptionBits(kIOMemoryPhysicallyContiguous),
                                                            SZ_4K, 0x00000000FFFFF000ULL);
  if (!pg->md) { kfree(pg); return nullptr; }
  IOByteCount len = 0;
  pg->cpu = pg->md->getBytesNoCopy();
  pg->phys = pg->md->getPhysicalSegment(0, &len, kIOMemoryMapperNone);
  memset(pg->cpu, 0, SZ_4K);   // every caller in pvr_mmu.c asks for __GFP_ZERO
  return pg;
}
extern "C" void __free_page(struct page *pg)
{
  if (!pg) return;
  pg->md->release();
  kfree(pg);
}
extern "C" void *page_address(struct page *pg) { return pg->cpu; }
extern "C" void *vmap(struct page **pages, unsigned int count, unsigned long flags, pgprot_t prot)
{
  (void)flags; (void)prot;
  return count == 1 ? pages[0]->cpu : nullptr;   // pvr_mmu.c maps single backing pages only
}
extern "C" void vunmap(const void *addr) { (void)addr; }
extern "C" dma_addr_t dma_map_page(struct device *dev, struct page *pg, size_t offset, size_t size, enum dma_data_direction dir)
{
  (void)dev; (void)size; (void)dir;
  return pg->phys + offset;
}
extern "C" void dma_unmap_page(struct device *dev, dma_addr_t addr, size_t size, enum dma_data_direction dir) { (void)dev; (void)addr; (void)size; (void)dir; }
extern "C" void dma_sync_single_for_device(struct device *dev, dma_addr_t addr, size_t size, enum dma_data_direction dir)
{
  (void)dev; (void)dir;
  __asm__ volatile("dsb sy" ::: "memory");
  flush_dcache(static_cast<vm_offset_t>(addr), static_cast<unsigned>(size), 1);   // physical address
}
extern "C" void dma_sync_sg_for_device(struct device *dev, struct scatterlist *sg, int nents, enum dma_data_direction dir)
{
  for (int i = 0; i < nents; i++) dma_sync_single_for_device(dev, sg[i].dma_address, sg[i].dma_length, dir);
}

extern "C" void native_powervr_log(const char *fmt, ...)
{
  va_list ap;
  va_start(ap, fmt);
  IOLogv(fmt, ap);
  va_end(ap);
}
extern "C" void native_powervr_warn(const char *file, int line, const char *what) { IOLog("powervr: WARN %s:%d %s\n", file, line, what); }

// Firmware heap objects for the other parts (pvr_fw_object_create_and_map()).
extern "C" int native_powervr_fw_alloc(uint32_t size, uint32_t align, struct native_powervr_fw_obj *out)
{
  Obj o;
  if (!gHeap || !align || (align & (align - 1)) || !alloc(size, o, align < 64 ? 64 : align)) return -ENOMEM;
  *out = {o.cpu(), o.fw(), o.phys(), o.size};
  clean(o.cpu(), o.size);
  return 0;
}
extern "C" void native_powervr_clean(const void *p, size_t n) { __asm__ volatile("dsb sy" ::: "memory"); clean(p, n); }
extern "C" void native_powervr_invalidate(const void *p, size_t n) { clean(p, n); __asm__ volatile("dsb sy" ::: "memory"); }

// pvr_kccb_send_cmd() and pvr_kccb_wait_for_completion() in one step: the command in the next slot, its return slot
// cleared, the write offset advanced, the GP data master kicked through the MTS, then the wait. Completion is
// osdata->kccb_cmds_executed advancing past its value before the kick (the bring-up showed the firmware does not write
// the return slot on this core; Linux's watchdog uses the same counter) or the return slot reporting execution.
extern "C" int native_powervr_kccb_submit(const struct rogue_fwif_kccb_cmd *cmd, uint32_t timeout_ms)
{
  if (!gFwStarted || !gKctl) return -ENODEV;
  IOLockLock(gLock);
  clean(gKctl, sizeof(*gKctl));
  const uint32_t slot = gKctl->write_offset, next = (slot + 1) & gKctl->wrap_mask;
  if (next == READ_ONCE_A04(gKctl->read_offset)) { IOLockUnlock(gLock); return -EBUSY; }
  clean(gOsdata, sizeof(*gOsdata));
  // The target counts every command queued so far, so one that timed out and runs late is not taken for this one.
  const uint32_t executed = READ_ONCE_A04(gOsdata->kccb_cmds_executed);
  if (int32_t(gKccbExpected - executed) < 0 || !gKccbExpected) gKccbExpected = executed;
  const uint32_t target = ++gKccbExpected;
  gKccb[slot] = *cmd;
  clean(&gKccb[slot], sizeof(gKccb[slot]));
  __asm__ volatile("dsb sy" ::: "memory");
  gKctl->write_offset = next;
  clean(gKctl, sizeof(*gKctl));
  __asm__ volatile("dsb sy" ::: "memory");
  wr32(ROGUE_CR_MTS_SCHEDULE, kKccbKick);
  IOLockUnlock(gLock);
  // Wait outside the lock, sleeping (thread context only: callers are kernel threads).
  int err = -ETIMEDOUT;
  for (uint32_t ms = 0; ms <= timeout_ms; ms++) {
    clean(gOsdata, sizeof(*gOsdata));
    if (int32_t(READ_ONCE_A04(gOsdata->kccb_cmds_executed) - target) >= 0) { err = 0; break; }
    IOSleep(1);
  }
  clean(gKctl, sizeof(*gKctl));
  if (err)
    IOLog("powervr: KCCB command %x slot %u timed out (read %u write %u, executed %u, expected %u)\n", cmd->cmd_type, slot,
          gKctl->read_offset, gKctl->write_offset, gOsdata->kccb_cmds_executed, target);
  return err;
}

// What pvr_mmu.c calls of the device, FW and power layers (stub/native_powervr_kernel.h).
static uint32_t gLastSlot;
extern "C" int pvr_kccb_send_cmd(struct pvr_device *pvr_dev, struct rogue_fwif_kccb_cmd *cmd, u32 *kccb_slot)
{
  (void)pvr_dev;
  // Sent and waited for here; pvr_kccb_wait_for_completion() then returns the result.
  const int err = native_powervr_kccb_submit(cmd, 1000);
  if (kccb_slot) *kccb_slot = ++gLastSlot;
  return err;
}
extern "C" int pvr_kccb_wait_for_completion(struct pvr_device *pvr_dev, u32 slot_nr, u32 timeout, u32 *rtn_out)
{
  (void)pvr_dev; (void)slot_nr; (void)timeout;
  if (rtn_out) *rtn_out = ROGUE_FWIF_KCCB_RTN_SLOT_CMD_EXECUTED;
  return 0;
}
extern "C" void pvr_fw_object_get_fw_addr(struct pvr_fw_object *fw_obj, u32 *fw_addr_out) { *fw_addr_out = fw_obj->o.fw_addr; }
extern "C" void pvr_device_lost(struct pvr_device *pvr_dev) { (void)pvr_dev; IOLog("powervr: device lost\n"); gFwStarted = false; }
extern "C" int pvr_power_reset(struct pvr_device *pvr_dev, bool hard_reset)
{
  (void)pvr_dev;
  IOLog("powervr: power reset (%s) requested; not implemented\n", hard_reset ? "hard" : "soft");
  return -ENODEV;
}
static struct drm_device gDrm;
extern "C" struct drm_device *native_powervr_drm(struct pvr_device *pvr_dev) { (void)pvr_dev; return &gDrm; }

extern "C" uint32_t native_powervr_kernel_fw_mem_ctx(void) { return gFwMemCtx; }
extern "C" int native_powervr_vm_map(uint64_t device_addr, uint64_t phys, uint64_t size, int uncached)
{
  if (!gMmuCtx) return -ENODEV;
  struct scatterlist sg = {phys, static_cast<unsigned int>(size)};
  struct sg_table sgt = {&sg, 1, 1};
  dma_sync_sg_for_device(nullptr, &sg, 1, DMA_BIDIRECTIONAL);   // pvr_mmu_op_context_create() requires synced pages
  pvr_mmu_op_context *op = pvr_mmu_op_context_create(gMmuCtx, &sgt, device_addr, 0, size);
  if (IS_ERR(op)) return static_cast<int>(PTR_ERR(op));
  int err = pvr_mmu_map(op, size, uncached ? DRM_PVR_BO_BYPASS_DEVICE_CACHE : 0, device_addr);
  pvr_mmu_op_context_destroy(op);
  if (!err) err = pvr_mmu_flush_exec(&gPvr, true);
  return err;
}


namespace {
// The VM context the kernel uses (pvr_vm_create_context(pvr_dev, is_userspace_context = true): with a MIPS firmware
// Linux has no kernel VM context, and contexts always point at a user VM context's FW memory context). Then a check that
// the GPU page tables translate as built, the MMU cache flush through the firmware (ROGUE_FWIF_KCCB_CMD_MMUCACHE), and
// the queue self-test of NativePowerVRQueue.cpp.
// The thread 0 trace written since the pointer 'from' (ring of ROGUE_FW_TRACE_BUF_DEFAULT_SIZE_IN_DWORDS words), as
// "powervr trace" rows numbered from 0 for scripts/decode-powervr-trace.py, then the firmware state the host can see.
void dumpFirmware(const char *when, uint32_t from)
{
  if (!gTc) return;
  clean(gTc, sizeof(*gTc));
  const uint32_t ring = ROGUE_FW_TRACE_BUF_DEFAULT_SIZE_IN_DWORDS, to = gTc->tracebuf[0].trace_pointer;
  uint32_t n = (to + ring - from) % ring;
  if (n > ring - 8) { from = (to + 8) % ring; n = ring - 8; }
  clean(gTrace0, ring * 4);
  IOLog("powervr: firmware trace %s: %u words from %u\n", when, n, from);
  uint32_t row[8];
  for (uint32_t w = 0; w < n; w += 8) {
    for (uint32_t k = 0; k < 8; k++) row[k] = (w + k < n) ? gTrace0[(from + w + k) % ring] : 0;
    IOLog("powervr trace %03u: %08x %08x %08x %08x %08x %08x %08x %08x\n", w, row[0], row[1], row[2], row[3], row[4], row[5],
          row[6], row[7]);
  }
  clean(gSysdata, sizeof(*gSysdata)); clean(gHwrInfo, sizeof(*gHwrInfo)); clean(gFwccbCtl, sizeof(*gFwccbCtl));
  IOLog("powervr: %s: pow_state %u faults %u hwr %u; FWCCB read %u write %u; assert %.64s:%u %.64s; fault0 %.64s:%u\n", when,
        unsigned(gSysdata->pow_state), gSysdata->fw_faults, gHwrInfo->hwr_counter, gFwccbCtl->read_offset, gFwccbCtl->write_offset,
        gTc->tracebuf[0].assert_buf.path, gTc->tracebuf[0].assert_buf.line_num, gTc->tracebuf[0].assert_buf.info,
        gSysdata->fault_info[0].fault_buf.path, gSysdata->fault_info[0].fault_buf.line_num);
  IOLog("powervr: %s: MTS schedule_enable %x bgctx %x intctx %x assoc bg %x int %x counted %llx gpu_int %x event %llx\n", when,
        rd32(ROGUE_CR_MTS_SCHEDULE_ENABLE), rd32(ROGUE_CR_MTS_BGCTX), rd32(ROGUE_CR_MTS_INTCTX), rd32(ROGUE_CR_MTS_BGCTX_THREAD0_DM_ASSOC),
        rd32(ROGUE_CR_MTS_INTCTX_THREAD0_DM_ASSOC), rd64(ROGUE_CR_MTS_BGCTX_COUNTED_SCHEDULE), rd32(ROGUE_CR_MTS_GPU_INT_STATUS),
        rd64(ROGUE_CR_EVENT_STATUS));
  for (uint32_t i = gFwccbCtl->read_offset; i != gFwccbCtl->write_offset; i = (i + 1) & gFwccbCtl->wrap_mask) {
    clean(&gFwccb[i], sizeof(gFwccb[i]));
    const uint32_t *raw = reinterpret_cast<const uint32_t *>(&gFwccb[i]);
    IOLog("powervr: FWCCB %u: type %x data %x %x %x %x %x %x\n", i, gFwccb[i].cmd_type, raw[1], raw[2], raw[3], raw[4], raw[5], raw[6]);
  }
}

}  // namespace
extern "C" void native_powervr_dump_firmware(const char *when, uint32_t last_words)
{
  if (!gTc) return;
  clean(gTc, sizeof(*gTc));
  const uint32_t ring = ROGUE_FW_TRACE_BUF_DEFAULT_SIZE_IN_DWORDS;
  if (last_words > ring - 8) last_words = ring - 8;
  dumpFirmware(when, (gTc->tracebuf[0].trace_pointer + ring - last_words) % ring);
}
namespace {
void vmAndQueueBringUp(void)
{
  if (gTc) clean(gTc, sizeof(*gTc));
  const uint32_t traceStart = gTc ? gTc->tracebuf[0].trace_pointer : 0;
  gPvr.fw_dev.initialised = true;
  gPvr.fw_dev.mem.mmucache_sync_obj = gMmuSync;
  gMmuCtx = pvr_mmu_context_create(&gPvr);
  if (IS_ERR(gMmuCtx)) { IOLog("powervr: pvr_mmu_context_create failed %ld\n", PTR_ERR(gMmuCtx)); gMmuCtx = nullptr; return; }
  const dma_addr_t pc = pvr_mmu_get_root_table_dma_addr(gMmuCtx);
  Obj memCtx;
  if (!alloc(sizeof(rogue_fwif_fwmemcontext), memCtx)) { IOLog("powervr: no FW memory context\n"); return; }
  auto *fmc = static_cast<rogue_fwif_fwmemcontext *>(memCtx.cpu());   // pvr_vm.c fw_mem_context_init()
  fmc->pc_dev_paddr = pc;
  fmc->page_cat_base_reg_set = ROGUE_FW_BIF_INVALID_PCSET;
  clean(fmc, sizeof(*fmc));
  gFwMemCtx = memCtx.fw();
  IOLog("powervr: VM context page catalogue %llx, FW memory context %x\n", static_cast<unsigned long long>(pc), gFwMemCtx);

  // A 64 KiB buffer at the start of the general heap, mapped and then looked up through the three levels by hand.
  IOBufferMemoryDescriptor *buf = IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task, IOOptionBits(kIODirectionInOut) | IOOptionBits(kIOMemoryPhysicallyContiguous),
                                                                                   SZ_64K, 0x00000000FFFFF000ULL);
  if (!buf) return;
  IOByteCount len = 0;
  const uint64_t bufPhys = buf->getPhysicalSegment(0, &len, kIOMemoryMapperNone);
  const uint64_t va = ROGUE_GENERAL_HEAP_BASE;
  const int err = native_powervr_vm_map(va, bufPhys, SZ_64K, 0);
  // Walk: PC index va[39:30], PD index va[29:21], PT index va[20:12] (4 KiB device pages); entries hold the next table's
  // or the page's physical address >> 12 in bits [39:12] (pvr_rogue_mmu_defs.h ROGUE_MMUCTRL_*).
  auto entry = [](uint64_t tablePhys, uint32_t index, bool wide) -> uint64_t {
    const vm_offset_t kva = phystokv(tablePhys & ~0xFFFULL);   // the physmap (no lasting alias mapping)
    flush_dcache(kva, SZ_4K, 0);
    uint64_t v = wide ? reinterpret_cast<volatile uint64_t *>(kva)[index] : reinterpret_cast<volatile uint32_t *>(kva)[index];
    return v;
  };
  const uint64_t pce = entry(pc, uint32_t((va >> 30) & 0x3FF), false);
  const uint64_t pdPhys = (pce >> ROGUE_MMUCTRL_PC_DATA_PD_BASE_SHIFT) << ROGUE_MMUCTRL_PC_DATA_PD_BASE_ALIGNSHIFT;
  const uint64_t pde = pdPhys ? entry(pdPhys & ~0xFFFULL, uint32_t((va >> 21) & 0x1FF), true) : 0;
  const uint64_t ptPhys = pde & ~ROGUE_MMUCTRL_PD_DATA_PT_BASE_CLRMSK;
  const uint64_t pte = ptPhys ? entry(ptPhys, uint32_t((va >> 12) & 0x1FF), true) : 0;
  const uint64_t pagePhys = pte & ~ROGUE_MMUCTRL_PT_DATA_PAGE_CLRMSK;
  IOLog("powervr: map %llx -> %llx: %d; walk PCE %llx PDE %llx PTE %llx -> %llx %s; mmucache sync %x\n",
        static_cast<unsigned long long>(va), static_cast<unsigned long long>(bufPhys), err, static_cast<unsigned long long>(pce),
        static_cast<unsigned long long>(pde), static_cast<unsigned long long>(pte), static_cast<unsigned long long>(pagePhys),
        pagePhys == bufPhys ? "MATCH" : "MISMATCH", (clean(gMmuSync->o.cpu, 4), *static_cast<volatile uint32_t *>(gMmuSync->o.cpu)));
  const int q = native_powervr_queue_selftest();
  IOLog("powervr: queue self-test %d\n", q);
  (void)traceStart;
  native_powervr_dump_firmware("after the queue test", 1500);
  native_powervr_stage = q ? 9 : 10;
}
}  // namespace

