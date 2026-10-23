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
//  - The firmware is the 22.67.54.30 image (no 22.40 image is published). Linux refuses a BVNC mismatch; here it is
//    reported and booted anyway, to see whether this firmware runs on the 22.40 core.
//  - CPU access is through the cached kernel mapping with explicit clean/invalidate (flush_dcache) at each hand-over.
#include <IOKit/IOLib.h>
#include <IOKit/IOBufferMemoryDescriptor.h>
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

extern "C" vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern "C" void flush_dcache(vm_offset_t addr, unsigned count, int phys);

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

namespace {
constexpr uint64_t kRegBase = 0x13000000, kRegSize = 0x10000;
constexpr uint32_t kHeapLog2 = 24;                 // ROGUE_FW_HEAP_MIPS_SHIFT (16 MiB)
constexpr uint32_t kHeapSize = 1U << kHeapLog2;
constexpr uint32_t kHeapMask = kHeapSize - 1;
constexpr uint32_t kMipsHeapBase = 0xC0000000;     // ROGUE_FW_HEAP_MIPS_BASE
constexpr uint32_t kConfigGranularity = SZ_64K;    // PVR_ROGUE_FW_CONFIG_HEAP_GRANULARITY
constexpr uint32_t kConfigOffset = kHeapSize - 3 * kConfigGranularity;   // raw size - PVR_ROGUE_FW_CONFIG_HEAP_SIZE
constexpr uint32_t kReserved = SZ_1M;              // ROGUE_FW_HEAP_MIPS_RESERVED_SIZE
constexpr uint32_t kCoreClockHz = 26000000;        // package/A04BR3Gpu.cpp leaves the core on the 26 MHz crystal

volatile uint8_t *gRegs;
IOBufferMemoryDescriptor *gHeapMem;
uint8_t *gHeap;          // CPU view of the firmware heap
uint64_t gHeapPhys;
uint32_t gNext;          // bump allocator for objects outside the fixed placements
IOBufferMemoryDescriptor *gPtMem;
uint32_t *gPt;
uint64_t gPtPhys;

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
  uint32_t off = (gNext + align - 1) & ~(align - 1);
  if (off + size > kConfigOffset - kReserved) return false;
  gNext = off + size;
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
  // The heap and the MIPS page table (ROGUE_MIPSFW_MAX_NUM_PAGETABLE_PAGES x 4 KiB, 4 bytes per 4 KiB heap page).
  gHeapMem = IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task, kIODirectionInOut | kIOMemoryPhysicallyContiguous,
                                                              kHeapSize, 0x00000000FFFFF000ULL);
  gPtMem = IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task, kIODirectionInOut | kIOMemoryPhysicallyContiguous,
                                                            ROGUE_MIPSFW_MAX_NUM_PAGETABLE_PAGES * SZ_4K, 0x00000000FFFFF000ULL);
  if (!gRegs || !gHeapMem || !gPtMem) return fail(2, "memory");
  IOByteCount len = 0;
  gHeap = static_cast<uint8_t *>(gHeapMem->getBytesNoCopy());
  gHeapPhys = gHeapMem->getPhysicalSegment(0, &len);
  gPt = static_cast<uint32_t *>(gPtMem->getBytesNoCopy());
  gPtPhys = gPtMem->getPhysicalSegment(0, &len);
  memset(gHeap, 0, kHeapSize);
  memset(gPt, 0, ROGUE_MIPSFW_MAX_NUM_PAGETABLE_PAGES * SZ_4K);
  IOLog("powervr: heap %llx (16 MiB), MIPS page table %llx\n", gHeapPhys, gPtPhys);
  native_powervr_stage = 2;

  // pvr_vm_mips_map() for the whole heap: uncached (every Linux FW object is PVR_BO_FW_FLAGS_DEVICE_UNCACHED), RW.
  const uint32_t pteFlags = ROGUE_MIPSFW_ENTRYLO_DIRTY_EN | (ROGUE_MIPSFW_UNCACHED_CACHE_POLICY << ROGUE_MIPSFW_ENTRYLO_CACHE_POLICY_SHIFT) |
                            ROGUE_MIPSFW_ENTRYLO_VALID_EN | ROGUE_MIPSFW_ENTRYLO_GLOBAL_EN;
  for (uint32_t pfn = 0; pfn < kHeapSize / SZ_4K; pfn++) {
    const uint64_t pa = gHeapPhys + uint64_t(pfn) * SZ_4K;
    gPt[pfn] = (uint32_t(((pa >> ROGUE_MIPSFW_LOG2_PAGE_SIZE_4K) << ROGUE_MIPSFW_ENTRYLO_PFN_SHIFT)) & ROGUE_MIPSFW_ENTRYLO_PFN_MASK_ABOVE_32BIT) | pteFlags;
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
  const Obj code = fixed(0, (codeSize + SZ_4K - 1) & ~(SZ_4K - 1));
  const Obj data = fixed(priv->base_addr & kHeapMask, (dataSize + SZ_4K - 1) & ~(SZ_4K - 1));
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
      if (ph->vaddr >= e.base_addr && ph->vaddr + ph->memsz <= e.base_addr + e.max_size) {
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
  tc->log_type = ROGUE_FWIF_LOG_TYPE_TRACE | ROGUE_FWIF_LOG_TYPE_GROUP_MAIN | ROGUE_FWIF_LOG_TYPE_GROUP_POW |
                 ROGUE_FWIF_LOG_TYPE_GROUP_HWR | ROGUE_FWIF_LOG_TYPE_GROUP_MISC;
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
  (void)connCtl; (void)mmuSync;
  clean(gHeap, kHeapSize);
  clean(gPt, ROGUE_MIPSFW_MAX_NUM_PAGETABLE_PAGES * SZ_4K);
  native_powervr_stage = 5;

  // pvr_fw_start() for a MIPS core with pbe2_in_xe (GE8300), no xe_tpu2, no sys_bus_secure_reset.
  const uint64_t softResetMask = ROGUE_CR_SOFT_RESET__PBE2_XE__MASKFULL;
  wr64(ROGUE_CR_SOFT_RESET, softResetMask); (void)rd64(ROGUE_CR_SOFT_RESET);
  wr64(ROGUE_CR_SOFT_RESET, softResetMask ^ ROGUE_CR_SOFT_RESET_RASCALDUSTS_EN); (void)rd64(ROGUE_CR_SOFT_RESET);
  wr64(ROGUE_CR_SOFT_RESET, ROGUE_CR_SOFT_RESET_GARTEN_EN); (void)rd64(ROGUE_CR_SOFT_RESET);
  // rogue_slc_init(): SLC line 512 bits < 1024, so bypass the burst combiner; quirk 71242 is not set for 22.x.
  wr32(ROGUE_CR_SLC_CTRL_MISC, (rd32(ROGUE_CR_SLC_CTRL_MISC) & ROGUE_CR_SLC_CTRL_MISC_ENABLE_PSG_HAZARD_CHECK_EN) |
                               ROGUE_CR_SLC_CTRL_MISC_ADDR_DECODE_MODE_PVR_HASH1 | ROGUE_CR_SLC_CTRL_MISC_BYPASS_BURST_COMBINER_EN);
  // pvr_mips_wrapper_init()
  const uint64_t remap = ROGUE_MIPSFW_BOOT_REMAP_LOG2_SEGMENT_SIZE;
  wr32(ROGUE_CR_MIPS_WRAPPER_CONFIG, (ROGUE_MIPSFW_REGISTERS_VIRTUAL_BASE >> ROGUE_MIPSFW_WRAPPER_CONFIG_REGBANK_ADDR_ALIGN) |
                                     ROGUE_CR_MIPS_WRAPPER_CONFIG_BOOT_ISA_MODE_MICROMIPS);
  wr64(ROGUE_CR_MIPS_ADDR_REMAP1_CONFIG1, ROGUE_MIPSFW_BOOT_REMAP_PHYS_ADDR_IN | ROGUE_CR_MIPS_ADDR_REMAP1_CONFIG1_MODE_ENABLE_EN);
  wr64(ROGUE_CR_MIPS_ADDR_REMAP1_CONFIG2, (bootCodePhys & ~ROGUE_CR_MIPS_ADDR_REMAP1_CONFIG2_ADDR_OUT_CLRMSK) | remap);
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
  native_powervr_stage = started ? 7 : -7;
  return started;
}
}  // namespace

void native_powervr_boot(void)
{
  unsigned mode = 0;
  if (!PE_parse_boot_argn("a04br3-gpu", &mode, sizeof(mode)) || mode < 2) return;
  if (!a04br3_gpu_bvnc || a04br3_gpu_bvnc == ~0ULL) { IOLog("powervr: no core identified; firmware not booted\n"); return; }
  boot();
}
