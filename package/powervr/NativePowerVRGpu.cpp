// SPDX-License-Identifier: MIT
// NativePowerVR, the IOKit surface: the GPU service NativePowerVRGpu and its user client NativePowerVRUserClient, which
// serve the ABI of NativePowerVRABI.h (the Linux PowerVR uAPI, pvr_drm.h, over IOConnectCallStructMethod) to the Mesa
// PowerVR Vulkan driver (patches/mesa/pvr-darwin-winsys.patch). One connection is one Linux DRM file: it owns its handles
// (BOs, VM contexts, contexts, free lists, HWRT datasets) and its fences, and closing it frees them.
//
// Written after Linux drivers/gpu/drm/imagination (torvalds/linux 734bef548a96e8774d6eef5dfa2026c6edc4e80b; pvr_drv.c: the
// ioctls, the device queries, pvr_get_uobj()/pvr_set_uobj(); pvr_gem.c: buffer objects; pvr_vm.c: VM contexts, heaps,
// static data areas, map and unmap; pvr_fw.c: pvr_fw_get_device_info(); "GPL-2.0-only OR MIT", used under MIT, Copyright
// Imagination Technologies Ltd.). The device information comes from the unchanged pvr_device_info.c (kernel/). Contexts,
// free lists, HWRT datasets, submission and fences are the job layer's (native_powervr_jobs.h); this file resolves the
// handles, copies the user memory and calls it.
//
// Registration. scripts/build-a04br3-board.py puts this file into the board archive, which the kernel links with
// -force_load (configs/a04br3_defconfig CONFIG_XNU_EXTRA_LD_ARCHIVES), so its static initializer lands in the kernel's
// __mod_init_func and the kernel's own C++ start-up (libkern OSlibkernInit() -> OSRuntimeInitializeCPP(NULL)) registers the
// three classes, as for UniXNU's NativeAdreno (drivers/NativeAdreno/NativeAdrenoPublish.cpp: NativeAdrenoGpuPublished and
// AGXDeviceUserClient in libNativeAdreno.a, no IOKit personality). Their parents (IOService, IOUserClient,
// IOGeneralMemoryDescriptor) are kernel classes, so the initializer needs no late staging; KeyStoreProbe.cpp,
// HidInputProbe.cpp and build-native-adreno-accelerator.py stage only classes whose parent class lives in a kext loaded
// later. The service is published as NativeAdrenoPublish.cpp publishes its own: native_powervr_publish() creates it,
// attaches it to the platform expert and registers it once the firmware runs, so IOServiceMatching("NativePowerVRGpu")
// finds it.
//
// Differences from Linux, all because there is no DRM core, shmem, drm_gpuvm or dma_fence here:
//  - A BO is wired kernel memory (IOBufferMemoryDescriptor) below 4 GiB (PHYS_BUS_WIDTH 32 in this firmware's device
//    information), zeroed and cleaned to memory when it is created, as pvr_gem_object_create() zeroes it and syncs it for
//    the device. The kernel's view is cached (see native_powervr_bo_cpu()). The user mapping is a write-combined view of
//    the same physical pages (Linux maps write-combined, shmem_obj->map_wc): a physical-range descriptor whose default cache
//    mode is write-combine, because IOUserClient::mapClientMemory64() replaces the cache mode the driver returns with the
//    caller's map flags (the Turnip Darwin KMD patch passes kIOMapWriteCombineCache for that reason; the PowerVR winsys
//    passes kIOMapAnywhere only). The view keeps the BO's pages until its last mapping is gone, as an mmap() keeps a GEM
//    object alive on Linux, so no user mapping outlives its pages (the "BoFree hang" of NativeAdrenoPublish.cpp).
//  - The mmap "offset" of a BO is its memory type for clientMemoryForType(): the BO handle. Handles of all kinds count up
//    and are not reused in a connection, so a memory type never names a later BO; they skip NativePowerVRTunnelMagic so a
//    direct call's first word is never read as a tunnel header.
//  - A VM context keeps a sorted list of its mappings in place of drm_gpuvm. A map over existing mappings first unmaps
//    the overlap and keeps the parts outside it, as drm_gpuvm_sm_map() does with its unmap/remap steps; an unmap must name
//    exactly one mapping, as drm_gpuva_find() requires.
//  - User pointers in the uapi structures are read and written with copyin()/copyout() in the calling task, with the
//    semantics of copy_struct_from_user(), pvr_get_uobj() and pvr_set_uobj().
//  - DRM_IOCTL_GEM_CLOSE does not check drm_gem_close.pad, as drm_gem_close_ioctl() does not.
#include <IOKit/IOLib.h>
#include <IOKit/IOPlatformExpert.h>
#include <IOKit/IOService.h>
#include <IOKit/IOUserClient.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <kern/cpu_data.h>
#include <string.h>
#include <sys/errno.h>
#include "compat/native_powervr_linux_compat.h"
// include/linux/sizes.h and include/linux/math.h values that the compat shim leaves out (pvr_rogue_heap_config.h,
// pvr_rogue_fwif_client.h and the pvr_drv.c code below use them).
#ifndef SZ_2G
#define SZ_2G 0x80000000ULL
#endif
#ifndef SZ_4G
#define SZ_4G 0x100000000ULL
#endif
#ifndef SZ_16G
#define SZ_16G 0x400000000ULL
#endif
#ifndef SZ_128G
#define SZ_128G 0x2000000000ULL
#endif
#ifndef DIV_ROUND_UP
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#endif
#include "native_powervr_uapi.h"
#include "NativePowerVRABI.h"
#include "linux/pvr_fw_info.h"
#include "linux/pvr_rogue_defs.h"
#include "linux/pvr_rogue_heap_config.h"
#include "linux/pvr_rogue_fwif_client.h"
#include "stub/devinfo/pvr_device.h"
#include "native_powervr_internal.h"
#define NATIVE_POWERVR_JOBS_API __attribute__((weak))
#include "native_powervr_jobs.h"
#undef min
#undef max

extern "C" {
void flush_dcache(vm_offset_t addr, unsigned count, int phys);
vm_offset_t phystokv(uint64_t pa);
extern uint64_t gPhysBase, gPhysSize;
// The firmware image (scripts/build-a04br3-board.py) and the core's CORE_ID register (A04BR3Gpu.cpp).
extern const uint8_t native_powervr_fw[];
extern const uint32_t native_powervr_fw_size;
extern volatile uint64_t a04br3_gpu_bvnc;
int native_powervr_publish(void);
// The display hand-off of UniXNU drivers/NativeMobileDisplay/NativeGOPPresenter.cpp (the legacy display archive,
// build-a04br3-legacy-display.py). Weak: a kernel linked with the first-pass display stub has neither.
IOReturn native_gop_handoff_call(uint32_t op, task_t task, const void *in, size_t inSize, void *out, size_t outSize) __attribute__((weak));
IOMemoryDescriptor *native_gop_handoff_memory(void) __attribute__((weak));
IOReturn native_gop_handoff_acquire(IOService *owner) __attribute__((weak));
void native_gop_handoff_close(IOService *owner) __attribute__((weak));
IOReturn native_gop_handoff_call_owned(IOService *owner, uint32_t op, task_t task, const void *in, size_t inSize,
                                      void *out, size_t outSize) __attribute__((weak));
}

#ifndef ENOENT
#define ENOENT 2
#endif
#ifndef E2BIG
#define E2BIG 7
#endif
#ifndef ENOTTY
#define ENOTTY 25
#endif

namespace {
// pvr_mmu.h: PVR_DEVICE_PAGE_SIZE is the CPU page (4 KiB in the Linux shim of this port, compat/native_powervr_linux_kernel.h),
// PVR_PAGE_TABLE_ADDR_SPACE_SIZE SZ_1T, PVR_PAGE_TABLE_ADDR_BITS 40.
constexpr uint64_t kDevicePageSize = SZ_4K;
constexpr uint64_t kDevicePageMask = ~(kDevicePageSize - 1);
constexpr uint64_t kPageTableAddrSpaceSize = 1ULL << 40;
constexpr uint64_t kPageTableAddrBits = 40;
constexpr uint64_t kPageTableAddrMask = kPageTableAddrSpaceSize - 1;
// pvr_free_list.c
constexpr uint32_t kFreeListMinPages = 50;          // FREE_LIST_MIN_PAGES
constexpr uint32_t kFreeListMinPagesBrn66011 = 40;  // FREE_LIST_MIN_PAGES_BRN66011
constexpr uint32_t kFreeListMinPagesRoguexe = 25;   // FREE_LIST_MIN_PAGES_ROGUEXE
// A resource bound of this port, not Linux's (memdup_user() bounds only by kmalloc): the static context state and the
// command stream of a job are copied into kernel memory up to this size.
constexpr uint32_t kMaxUserStream = SZ_64K;
// BO memory: below 4 GiB (PHYS_BUS_WIDTH 32), page aligned (the mask also sets the alignment, IOBufferMemoryDescriptor).
constexpr uint64_t kBoPhysicalMask = 0x00000000FFFFF000ULL;

#define PVR_UOBJ_MIN_SIZE(type, last) (offsetof(type, last) + sizeof(((type *)nullptr)->last))   // pvr_drv.h

// ---- Device information (pvr_fw_get_device_info(), pvr_device_info.c) ----
native_powervr_device_info gDev;
uint64_t gGpuId;              // pvr_gpu_id_to_packed_bvnc(&pvr_dev->gpu_id)
uint64_t gDeviceAddrBits;     // virtual_address_space_bits: the drm_gpuvm range of a VM context
bool gDevLoaded;

int loadDeviceInfo()
{
  const uint32_t size = native_powervr_fw_size;
  if (size < SZ_4K + sizeof(pvr_fw_info_header)) return -EINVAL;
  const auto *header = reinterpret_cast<const pvr_fw_info_header *>(native_powervr_fw + size - SZ_4K);
  // pvr_fw_validate() has run in NativePowerVR.cpp before the firmware booted; the device information must lie inside the image.
  if (header->info_version != PVR_FW_INFO_VERSION || header->device_info_size < sizeof(pvr_fw_device_info_header) ||
      header->device_info_size > size - SZ_4K)
    return -EINVAL;
  const auto *dih = reinterpret_cast<const pvr_fw_device_info_header *>(native_powervr_fw + (size - SZ_4K) - header->device_info_size);
  const uint64_t words = dih->brn_mask_size + dih->ern_mask_size + dih->feature_mask_size + dih->feature_param_size;
  if (dih->brn_mask_size > 0xffff || dih->ern_mask_size > 0xffff || dih->feature_mask_size > 0xffff || dih->feature_param_size > 0xffff ||
      sizeof(*dih) + words * 8 > header->device_info_size)
    return -EINVAL;
  // pvr_fw_get_device_info()
  const u64 *dev_info = reinterpret_cast<const u64 *>(dih + 1);
  pvr_device_info_set_quirks(&gDev, dev_info, static_cast<u32>(dih->brn_mask_size));
  dev_info += dih->brn_mask_size;
  pvr_device_info_set_enhancements(&gDev, dev_info, static_cast<u32>(dih->ern_mask_size));
  dev_info += dih->ern_mask_size;
  int err = pvr_device_info_set_features(&gDev, dev_info, static_cast<u32>(dih->feature_mask_size),
                                         static_cast<u32>(dih->feature_param_size));
  if (err) return err;
  // pvr_load_gpu_id(): CORE_ID with PBVNC_COREID_REG, B 63:48, V 47:32, N 31:16, C 15:0, repacked by PVR_PACKED_BVNC().
  const uint64_t id = a04br3_gpu_bvnc;
  gGpuId = ((id >> 48 & 0xffff) << 48) | ((id >> 32 & 0xffff) << 32) | ((id >> 16 & 0xffff) << 16) | (id & 0xffff);
  // pvr_vm_create_context(): the device must have virtual_address_space_bits == PVR_PAGE_TABLE_ADDR_BITS.
  gDeviceAddrBits = 0;
  if (PVR_FEATURE_VALUE(&gDev, virtual_address_space_bits, &gDeviceAddrBits) || gDeviceAddrBits != kPageTableAddrBits) gDeviceAddrBits = 0;
  gDevLoaded = true;
  return 0;
}

// ---- pvr_drv.c: the values of DRM_PVR_DEV_QUERY_GPU_INFO_GET and _RUNTIME_INFO_GET ----
uint32_t rogue_get_common_store_partition_space_size(const native_powervr_device_info *pvr_dev)
{
  uint32_t max_partitions = 0, tile_size_x = 0, tile_size_y = 0;
  PVR_FEATURE_VALUE(pvr_dev, tile_size_x, &tile_size_x);
  PVR_FEATURE_VALUE(pvr_dev, tile_size_y, &tile_size_y);
  PVR_FEATURE_VALUE(pvr_dev, max_partitions, &max_partitions);
  if (tile_size_x == 16 && tile_size_y == 16) {
    uint32_t usc_min_output_registers_per_pix = 0;
    PVR_FEATURE_VALUE(pvr_dev, usc_min_output_registers_per_pix, &usc_min_output_registers_per_pix);
    return tile_size_x * tile_size_y * max_partitions * usc_min_output_registers_per_pix;
  }
  return max_partitions * 1024;
}
uint32_t rogue_get_common_store_alloc_region_size(const native_powervr_device_info *pvr_dev)
{
  uint32_t common_store_size_in_dwords = 512 * 4 * 4;
  PVR_FEATURE_VALUE(pvr_dev, common_store_size_in_dwords, &common_store_size_in_dwords);
  const uint32_t alloc_region_size = common_store_size_in_dwords - (256U * 4U) - rogue_get_common_store_partition_space_size(pvr_dev);
  if (PVR_HAS_QUIRK(pvr_dev, 44079)) {
    const uint32_t common_store_split_point = (768U * 4U * 4U);
    const uint32_t split = common_store_split_point - (256U * 4U);
    return split < alloc_region_size ? split : alloc_region_size;
  }
  return alloc_region_size;
}
uint32_t rogue_get_num_phantoms(const native_powervr_device_info *pvr_dev)
{
  uint32_t num_clusters = 1;
  PVR_FEATURE_VALUE(pvr_dev, num_clusters, &num_clusters);
  return ROGUE_REQ_NUM_PHANTOMS(num_clusters);
}
uint32_t rogue_get_max_coeffs(const native_powervr_device_info *pvr_dev)
{
  uint32_t max_coeff_additional_portion = ROGUE_MAX_VERTEX_SHARED_REGISTERS;
  uint32_t pending_allocation_shared_regs = 2U * 1024U;
  uint32_t pending_allocation_coeff_regs = 0U;
  const uint32_t num_phantoms = rogue_get_num_phantoms(pvr_dev);
  uint32_t tiles_in_flight = 0;
  PVR_FEATURE_VALUE(pvr_dev, isp_max_tiles_in_flight, &tiles_in_flight);
  uint32_t max_coeff_pixel_portion = DIV_ROUND_UP(tiles_in_flight, num_phantoms);
  max_coeff_pixel_portion *= ROGUE_MAX_PIXEL_SHARED_REGISTERS;
  // Compute tasks on cores with BRN48492 and without compute overlap may lock up without two additional lines of coeffs.
  if (PVR_HAS_QUIRK(pvr_dev, 48492) && !PVR_HAS_FEATURE(pvr_dev, compute_overlap)) pending_allocation_coeff_regs = 2U * 1024U;
  if (PVR_HAS_ENHANCEMENT(pvr_dev, 38748)) pending_allocation_shared_regs = 0;
  if (PVR_HAS_ENHANCEMENT(pvr_dev, 38020)) max_coeff_additional_portion += ROGUE_MAX_COMPUTE_SHARED_REGISTERS;
  return rogue_get_common_store_alloc_region_size(pvr_dev) + pending_allocation_coeff_regs -
         (max_coeff_pixel_portion + max_coeff_additional_portion + pending_allocation_shared_regs);
}
uint32_t rogue_get_cdm_max_local_mem_size_regs(const native_powervr_device_info *pvr_dev)
{
  uint32_t available_coeffs_in_dwords = rogue_get_max_coeffs(pvr_dev);
  if (PVR_HAS_QUIRK(pvr_dev, 48492) && PVR_HAS_FEATURE(pvr_dev, roguexe) && !PVR_HAS_FEATURE(pvr_dev, compute_overlap)) {
    // Driver must not use the 2 reserved lines.
    available_coeffs_in_dwords -= ROGUE_CSRM_LINE_SIZE_IN_DWORDS * 2;
  }
  const uint32_t limit = static_cast<uint32_t>(ROGUE_MAX_PER_KERNEL_LOCAL_MEM_SIZE_REGS);
  return available_coeffs_in_dwords < limit ? available_coeffs_in_dwords : limit;
}
// pvr_free_list.c pvr_get_free_list_min_pages()
uint32_t get_free_list_min_pages(const native_powervr_device_info *pvr_dev)
{
  if (PVR_HAS_FEATURE(pvr_dev, roguexe)) return PVR_HAS_QUIRK(pvr_dev, 66011) ? kFreeListMinPagesBrn66011 : kFreeListMinPagesRoguexe;
  return kFreeListMinPages;
}
// pvr_device.c
bool has_uapi_quirk(const native_powervr_device_info *pvr_dev, uint32_t quirk)
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
bool has_uapi_enhancement(const native_powervr_device_info *pvr_dev, uint32_t enhancement)
{
  switch (enhancement) {
  case 35421: return PVR_HAS_ENHANCEMENT(pvr_dev, 35421);
  case 42064: return PVR_HAS_ENHANCEMENT(pvr_dev, 42064);
  default: return false;
  }
}

// ---- pvr_vm.c: heaps and static data areas ----
// Static data areas are determined by firmware.
const drm_pvr_static_data_area kStaticDataAreas[] = {
  {DRM_PVR_STATIC_DATA_AREA_FENCE, DRM_PVR_HEAP_GENERAL, 128, 0},
  {DRM_PVR_STATIC_DATA_AREA_YUV_CSC, DRM_PVR_HEAP_GENERAL, 1024, 128},
  {DRM_PVR_STATIC_DATA_AREA_VDM_SYNC, DRM_PVR_HEAP_PDS_CODE_DATA, 128, 0},
  {DRM_PVR_STATIC_DATA_AREA_EOT, DRM_PVR_HEAP_PDS_CODE_DATA, 128, 128},
  {DRM_PVR_STATIC_DATA_AREA_VDM_SYNC, DRM_PVR_HEAP_USC_CODE, 128, 0},
};
static_assert(offsetof(drm_pvr_static_data_area, area_usage) == 0 && offsetof(drm_pvr_static_data_area, location_heap_id) == 2 &&
              offsetof(drm_pvr_static_data_area, size) == 4 && offsetof(drm_pvr_static_data_area, offset) == 8,
              "kStaticDataAreas initialiser order");
// PVR_DEVICE_PAGE_SHIFT
const drm_pvr_heap kHeaps[DRM_PVR_HEAP_COUNT] = {
  {ROGUE_GENERAL_HEAP_BASE, ROGUE_GENERAL_HEAP_SIZE, 0, 12},
  {ROGUE_PDSCODEDATA_HEAP_BASE, ROGUE_PDSCODEDATA_HEAP_SIZE, 0, 12},
  {ROGUE_USCCODE_HEAP_BASE, ROGUE_USCCODE_HEAP_SIZE, 0, 12},
  {ROGUE_RGNHDR_HEAP_BASE, ROGUE_RGNHDR_HEAP_SIZE, 0, 12},
  {ROGUE_VISTEST_HEAP_BASE, ROGUE_VISTEST_HEAP_SIZE, 0, 12},
  {ROGUE_TRANSFER_FRAG_HEAP_BASE, ROGUE_TRANSFER_FRAG_HEAP_SIZE, 0, 12},
};
static_assert(DRM_PVR_HEAP_GENERAL == 0 && DRM_PVR_HEAP_PDS_CODE_DATA == 1 && DRM_PVR_HEAP_USC_CODE == 2 && DRM_PVR_HEAP_RGNHDR == 3 &&
              DRM_PVR_HEAP_VIS_TEST == 4 && DRM_PVR_HEAP_TRANSFER_FRAG == 5 && DRM_PVR_HEAP_COUNT == 6, "kHeaps order");
static_assert(SZ_4K == 1u << 12, "page_size_log2");
// pvr_find_heap_containing()
bool find_heap_containing(uint64_t start, uint64_t size)
{
  uint64_t end;
  if (__builtin_add_overflow(start, size - 1, &end)) return false;
  for (uint32_t heap_id = 0; heap_id < DRM_PVR_HEAP_COUNT; heap_id++) {
    // Filter heaps that present only with an associated quirk.
    if (heap_id == DRM_PVR_HEAP_RGNHDR && !PVR_HAS_QUIRK(&gDev, 63142)) continue;
    if (kHeaps[heap_id].base <= start && end < kHeaps[heap_id].base + kHeaps[heap_id].size) return true;
  }
  return false;
}
// pvr_device_addr_and_size_are_valid() with drm_gpuvm_range_valid() over [0, 1 << device_addr_bits).
bool device_addr_and_size_are_valid(uint64_t device_addr, uint64_t size)
{
  uint64_t end;
  if (__builtin_add_overflow(device_addr, size, &end)) return false;
  return (device_addr & ~kPageTableAddrMask) == 0 && (device_addr & ~kDevicePageMask) == 0 && gDeviceAddrBits &&
         end <= (1ULL << gDeviceAddrBits) && size != 0 && (size & ~kDevicePageMask) == 0 && end <= kPageTableAddrSpaceSize;
}

// ---- User memory (include/linux/uaccess.h, pvr_drv.c) ----
int copy_from_user(void *dst, uint64_t src, size_t n) { return n && copyin(src, dst, n) ? -EFAULT : 0; }
int copy_to_user(uint64_t dst, const void *src, size_t n) { return n && copyout(src, dst, n) ? -EFAULT : 0; }
const uint8_t kZero[256] = {};
int clear_user(uint64_t dst, size_t n)
{
  while (n) {
    const size_t chunk = n < sizeof(kZero) ? n : sizeof(kZero);
    if (copy_to_user(dst, kZero, chunk)) return -EFAULT;
    dst += chunk;
    n -= chunk;
  }
  return 0;
}
// check_zeroed_user(): 1 when all n bytes are zero, 0 when not, -EFAULT.
int check_zeroed_user(uint64_t src, size_t n)
{
  uint8_t buf[256];
  while (n) {
    const size_t chunk = n < sizeof(buf) ? n : sizeof(buf);
    if (copy_from_user(buf, src, chunk)) return -EFAULT;
    for (size_t i = 0; i < chunk; i++) if (buf[i]) return 0;
    src += chunk;
    n -= chunk;
  }
  return 1;
}
int copy_struct_from_user(void *dst, size_t ksize, uint64_t src, size_t usize)
{
  const size_t size = ksize < usize ? ksize : usize;
  const size_t rest = (ksize > usize ? ksize : usize) - size;
  if (usize < ksize) {
    memset(static_cast<uint8_t *>(dst) + size, 0, rest);
  } else if (usize > ksize) {
    const int ret = check_zeroed_user(src + ksize, rest);
    if (ret <= 0) return ret ? ret : -E2BIG;
  }
  return copy_from_user(dst, src, size);
}
int pvr_get_uobj(uint64_t usr_ptr, uint32_t usr_stride, uint32_t min_stride, uint32_t obj_size, void *out)
{
  if (usr_stride < min_stride) return -EINVAL;
  return copy_struct_from_user(out, obj_size, usr_ptr, usr_stride);
}
int pvr_set_uobj(uint64_t usr_ptr, uint32_t usr_stride, uint32_t min_stride, uint32_t obj_size, const void *in)
{
  if (usr_stride < min_stride) return -EINVAL;
  if (copy_to_user(usr_ptr, in, usr_stride < obj_size ? usr_stride : obj_size)) return -EFAULT;
  if (usr_stride > obj_size && clear_user(usr_ptr + obj_size, usr_stride - obj_size)) return -EFAULT;
  return 0;
}
int pvr_set_uobj_array(const drm_pvr_obj_array *out, uint32_t min_stride, uint32_t obj_size, const void *in)
{
  if (out->stride < min_stride) return -EINVAL;
  if (!out->count) return 0;
  if (obj_size == out->stride) return copy_to_user(out->array, in, static_cast<size_t>(obj_size) * out->count);
  const uint32_t cpy_elem_size = out->stride < obj_size ? out->stride : obj_size;
  uint64_t out_ptr = out->array;
  const uint8_t *in_ptr = static_cast<const uint8_t *>(in);
  for (uint32_t i = 0; i < out->count; i++) {
    if (copy_to_user(out_ptr, in_ptr, cpy_elem_size)) return -EFAULT;
    if (out->stride > obj_size && clear_user(out_ptr + cpy_elem_size, out->stride - obj_size)) return -EFAULT;
    out_ptr += out->stride;
    in_ptr += obj_size;
  }
  return 0;
}
#define PVR_UOBJ_GET(obj, usr_size, usr_ptr) pvr_get_uobj(usr_ptr, usr_size, PVR_UOBJ_MIN_SIZE_OF(obj), sizeof(obj), &(obj))
#define PVR_UOBJ_SET(usr_ptr, usr_size, obj) pvr_set_uobj(usr_ptr, usr_size, PVR_UOBJ_MIN_SIZE_OF(obj), sizeof(obj), &(obj))
// PVR_UOBJ_MIN_SIZE(): the _Generic table of pvr_drv.h.
constexpr uint32_t uobjMinSize(const drm_pvr_dev_query_gpu_info &) { return PVR_UOBJ_MIN_SIZE(drm_pvr_dev_query_gpu_info, num_phantoms); }
constexpr uint32_t uobjMinSize(const drm_pvr_dev_query_runtime_info &) { return PVR_UOBJ_MIN_SIZE(drm_pvr_dev_query_runtime_info, cdm_max_local_mem_size_regs); }
constexpr uint32_t uobjMinSize(const drm_pvr_dev_query_quirks &) { return PVR_UOBJ_MIN_SIZE(drm_pvr_dev_query_quirks, _padding_c); }
constexpr uint32_t uobjMinSize(const drm_pvr_dev_query_enhancements &) { return PVR_UOBJ_MIN_SIZE(drm_pvr_dev_query_enhancements, _padding_c); }
constexpr uint32_t uobjMinSize(const drm_pvr_heap &) { return PVR_UOBJ_MIN_SIZE(drm_pvr_heap, page_size_log2); }
constexpr uint32_t uobjMinSize(const drm_pvr_dev_query_heap_info &) { return PVR_UOBJ_MIN_SIZE(drm_pvr_dev_query_heap_info, heaps); }
constexpr uint32_t uobjMinSize(const drm_pvr_static_data_area &) { return PVR_UOBJ_MIN_SIZE(drm_pvr_static_data_area, offset); }
constexpr uint32_t uobjMinSize(const drm_pvr_dev_query_static_data_areas &) { return PVR_UOBJ_MIN_SIZE(drm_pvr_dev_query_static_data_areas, static_data_areas); }
#define PVR_UOBJ_MIN_SIZE_OF(obj) uobjMinSize(obj)

IOReturn ioReturnOf(int err)
{
  // NativePowerVRABI.h: the user side maps these back to EINVAL, ENOMEM, ENOTTY, EPERM, EBUSY, ETIMEDOUT, the rest to EIO.
  switch (err) {
  case 0: return kIOReturnSuccess;
  case -EINVAL: return kIOReturnBadArgument;
  case -ENOMEM: return kIOReturnNoMemory;
  case -ENOTTY: return kIOReturnUnsupported;
  case -EPERM: return kIOReturnNotPermitted;
  case -EBUSY: return kIOReturnBusy;
  case -ETIMEDOUT: return kIOReturnTimeout;
  case -ENOENT: return kIOReturnNotFound;
  case -EFAULT: return kIOReturnVMError;
  case -E2BIG: return kIOReturnOverrun;
  case -ENOSPC: return kIOReturnNoSpace;
  case -ENODEV: return kIOReturnNoDevice;
  default: return kIOReturnIOError;
  }
}

void *kzallocBytes(size_t n) { return n ? IOMallocZero(n) : nullptr; }
void kfreeBytes(void *p, size_t n) { if (p) IOFree(p, n); }

// ---- Handle tables (the xarrays of struct pvr_file and the GEM handles of drm_file) ----
struct HandleTable {
  uint32_t *handles;   // ascending
  void **objects;
  uint32_t count, capacity, last;
};
int handleInsert(HandleTable &t, void *object, uint32_t *handle_out)
{
  uint32_t handle = t.last + 1;
  if (handle == NativePowerVRTunnelMagic) handle++;
  if (handle == 0 || handle < t.last) return -EBUSY;   // xa_alloc() with xa_limit_32b: no id left
  if (t.count == t.capacity) {
    const uint32_t capacity = t.capacity ? t.capacity * 2 : 16;
    if (capacity < t.capacity) return -ENOMEM;
    auto *handles = static_cast<uint32_t *>(kzallocBytes(sizeof(uint32_t) * capacity));
    auto *objects = static_cast<void **>(kzallocBytes(sizeof(void *) * capacity));
    if (!handles || !objects) {
      kfreeBytes(handles, sizeof(uint32_t) * capacity);
      kfreeBytes(objects, sizeof(void *) * capacity);
      return -ENOMEM;
    }
    if (t.count) {
      memcpy(handles, t.handles, sizeof(uint32_t) * t.count);
      memcpy(objects, t.objects, sizeof(void *) * t.count);
    }
    kfreeBytes(t.handles, sizeof(uint32_t) * t.capacity);
    kfreeBytes(t.objects, sizeof(void *) * t.capacity);
    t.handles = handles;
    t.objects = objects;
    t.capacity = capacity;
  }
  t.handles[t.count] = handle;
  t.objects[t.count] = object;
  t.count++;
  t.last = handle;
  *handle_out = handle;
  return 0;
}
int handleIndex(const HandleTable &t, uint32_t handle)
{
  uint32_t lo = 0, hi = t.count;
  while (lo < hi) {
    const uint32_t mid = lo + (hi - lo) / 2;
    if (t.handles[mid] == handle) return static_cast<int>(mid);
    if (t.handles[mid] < handle) lo = mid + 1; else hi = mid;
  }
  return -1;
}
void *handleLookup(const HandleTable &t, uint32_t handle)
{
  const int i = handle ? handleIndex(t, handle) : -1;
  return i < 0 ? nullptr : t.objects[i];
}
void *handleErase(HandleTable &t, uint32_t handle)
{
  const int i = handle ? handleIndex(t, handle) : -1;
  if (i < 0) return nullptr;
  void *object = t.objects[i];
  const uint32_t tail = t.count - static_cast<uint32_t>(i) - 1;
  memmove(&t.handles[i], &t.handles[i + 1], sizeof(uint32_t) * tail);
  memmove(&t.objects[i], &t.objects[i + 1], sizeof(void *) * tail);
  t.count--;
  return object;
}
// Takes the objects out of the table in handle order (for teardown); nullptr when empty.
void *handlePop(HandleTable &t)
{
  return t.count ? handleErase(t, t.handles[0]) : nullptr;
}
void handleFree(HandleTable &t)
{
  kfreeBytes(t.handles, sizeof(uint32_t) * t.capacity);
  kfreeBytes(t.objects, sizeof(void *) * t.capacity);
  t = {};
}

uint32_t refGet(uint32_t *refs) { return __atomic_add_fetch(refs, 1, __ATOMIC_RELAXED); }
bool refPut(uint32_t *refs) { return __atomic_sub_fetch(refs, 1, __ATOMIC_ACQ_REL) == 0; }
}  // namespace

// ---- Buffer objects (pvr_gem.c) ----

// The user's view of a BO: its physical runs, write-combined by default (the buffer cache mode of an IOMemoryDescriptor is
// what a mapping uses when the map flags give none), owning the BO's pages until the last mapping and reference are gone.
class NativePowerVRBoView : public IOGeneralMemoryDescriptor {
  OSDeclareDefaultStructors(NativePowerVRBoView)
  IOBufferMemoryDescriptor *backing_;
public:
  static NativePowerVRBoView *withBacking(IOBufferMemoryDescriptor *backing, const native_powervr_sg *sg, uint32_t nents, uint64_t size);
  void free() APPLE_KEXT_OVERRIDE;
};
OSDefineMetaClassAndStructors(NativePowerVRBoView, IOGeneralMemoryDescriptor)

NativePowerVRBoView *NativePowerVRBoView::withBacking(IOBufferMemoryDescriptor *backing, const native_powervr_sg *sg, uint32_t nents, uint64_t size)
{
  auto *ranges = static_cast<IOAddressRange *>(kzallocBytes(sizeof(IOAddressRange) * nents));
  if (!ranges) return nullptr;
  uint64_t left = size;
  uint32_t used = 0;
  for (uint32_t i = 0; i < nents && left; i++, used++) {
    ranges[i].address = sg[i].phys;
    ranges[i].length = sg[i].size < left ? sg[i].size : left;
    left -= ranges[i].length;
  }
  NativePowerVRBoView *view = new NativePowerVRBoView;
  // IOMemoryDescriptor::withAddressRanges(ranges, n, options, TASK_NULL) with the write-combine buffer cache mode; the ranges
  // are copied.
  const IOOptionBits options = static_cast<IOOptionBits>(kIOMemoryTypePhysical64) | static_cast<IOOptionBits>(kIODirectionInOut) |
                               (static_cast<IOOptionBits>(kIOWriteCombineCache) << kIOMemoryBufferCacheShift);
  if (view && (left || !view->initWithOptions(ranges, used, 0, nullptr, options, nullptr))) {
    view->release();
    view = nullptr;
  }
  kfreeBytes(ranges, sizeof(IOAddressRange) * nents);
  if (!view) return nullptr;
  backing->retain();
  view->backing_ = backing;
  return view;
}
void NativePowerVRBoView::free()
{
  if (backing_) {
    backing_->release();
    backing_ = nullptr;
  }
  IOGeneralMemoryDescriptor::free();
}

struct native_powervr_bo {
  uint32_t refs;
  uint64_t size, flags;
  IOBufferMemoryDescriptor *md;   // the pages, wired, below 4 GiB
  void *cpu;                      // the kernel's (cached) view
  native_powervr_sg *sg;          // physical runs of the whole BO
  uint32_t nents, sgCapacity;
  NativePowerVRBoView *view;      // created at the first user mapping (under the owning connection's lock)
  // A host import (NativePowerVRMethodImportHostMemory): the caller's own pages, pinned (prepare()) until the BO goes. Such a BO
  // has no md, no kernel view (cpu is null, so the job layer refuses it as a firmware object) and no user view.
  IOMemoryDescriptor *host;
  bool hostPrepared;
};

namespace {
void boDestroy(native_powervr_bo *bo)
{
  if (bo->view) bo->view->release();   // the pages live on while user mappings of the view do
  if (bo->md) bo->md->release();
  if (bo->host) {
    // complete() unwires the pages; a failure leaves them wired, which only leaks them.
    if (bo->hostPrepared && bo->host->complete() != kIOReturnSuccess)
      IOLog("powervr: host import of 0x%llx bytes not released\n", static_cast<unsigned long long>(bo->size));
    bo->host->release();
  }
  kfreeBytes(bo->sg, sizeof(native_powervr_sg) * bo->sgCapacity);
  kfreeBytes(bo, sizeof(*bo));
}
// pvr_gem_object_create(): flags are checked by the caller.
int boCreate(uint64_t size, uint64_t flags, native_powervr_bo **out)
{
  auto *bo = static_cast<native_powervr_bo *>(kzallocBytes(sizeof(native_powervr_bo)));
  if (!bo) return -ENOMEM;
  bo->refs = 1;
  bo->size = size;
  bo->flags = flags;
  bo->md = IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task, kIODirectionInOut, size, kBoPhysicalMask);
  if (!bo->md || bo->md->getLength() < size) { boDestroy(bo); return -ENOMEM; }
  bo->cpu = bo->md->getBytesNoCopy();
  // The physical runs (pvr_gem_object_get_pages_sgt()), adjacent pages merged.
  const uint64_t pages = size / kDevicePageSize;
  bo->sgCapacity = pages > 0xffffffffULL ? 0 : static_cast<uint32_t>(pages);
  bo->sg = static_cast<native_powervr_sg *>(kzallocBytes(sizeof(native_powervr_sg) * bo->sgCapacity));
  if (!bo->cpu || !bo->sg) { boDestroy(bo); return -ENOMEM; }
  for (uint64_t offset = 0; offset < size;) {
    IOByteCount length = 0;
    const uint64_t phys = bo->md->getPhysicalSegment(offset, &length, kIOMemoryMapperNone);
    if (!phys || !length || (phys & (kDevicePageSize - 1)) || (length & (kDevicePageSize - 1)) || (phys + length) > (1ULL << 32)) {
      boDestroy(bo);
      return -ENOMEM;
    }
    if (length > size - offset) length = size - offset;
    if (bo->nents && bo->sg[bo->nents - 1].phys + bo->sg[bo->nents - 1].size == phys) {
      bo->sg[bo->nents - 1].size += length;
    } else {
      if (bo->nents == bo->sgCapacity) { boDestroy(bo); return -ENOMEM; }
      bo->sg[bo->nents++] = {phys, length};
    }
    offset += length;
  }
  // pvr_gem_object_zero() and dma_sync_sgtable_for_device(): the buffer is zeroed (KMA_ZERO); write it back and drop it
  // from the CPU caches, so neither the GPU nor the write-combined user view sees anything else.
  memset(bo->cpu, 0, size);
  __asm__ volatile("dsb sy" ::: "memory");
  for (uint32_t i = 0; i < bo->nents; i++)
    for (uint64_t done = 0; done < bo->sg[i].size; done += SZ_1G) {
      const uint64_t n = bo->sg[i].size - done < SZ_1G ? bo->sg[i].size - done : SZ_1G;
      flush_dcache(static_cast<vm_offset_t>(bo->sg[i].phys + done), static_cast<unsigned>(n), 1);
    }
  __asm__ volatile("dsb sy" ::: "memory");
  *out = bo;
  return 0;
}

// The pages of a host import, as UniXNU's NativeAdreno imports them (drivers/NativeAdreno/NativeAdrenoMemory.cpp importRange(),
// hardware-proven with IOSurfaces and the scanout mapping): a persistent memory entry first, so the owner may unmap its range while
// the GPU still holds the import, and a plain one where no memory entry can be made of the range.
IOMemoryDescriptor *hostRange(task_t task, uint64_t address, uint64_t size, IODirection direction)
{
  const IOOptionBits options = static_cast<IOOptionBits>(direction) | static_cast<IOOptionBits>(kIOMemoryMapperNone);
  IOMemoryDescriptor *range = IOMemoryDescriptor::withAddressRange(address, size, options | kIOMemoryPersistent, task);
  return range ? range : IOMemoryDescriptor::withAddressRange(address, size, options, task);
}
// A BO over [address, address + size) of task, pinned for writing. Read-only imports are refused: the current GPU mapping
// path has no per-BO write protection. Jobs snapshot these BOs and maintain their caches before submission and completion.
int boImportHost(task_t task, uint64_t address, uint64_t size, uint64_t flags, native_powervr_bo **out)
{
  auto *bo = static_cast<native_powervr_bo *>(kzallocBytes(sizeof(native_powervr_bo)));
  if (!bo) return -ENOMEM;
  bo->refs = 1;
  bo->size = size;
  bo->flags = flags;
  bo->host = hostRange(task, address, size, kIODirectionInOut);
  if (!bo->host) { boDestroy(bo); return -ENOMEM; }
  IOReturn status = bo->host->prepare();
  if (status != kIOReturnSuccess) { boDestroy(bo); return -EFAULT; }
  bo->hostPrepared = true;
  if (bo->host->getLength() != size) { boDestroy(bo); return -EINVAL; }
  const uint64_t pages = size / kDevicePageSize;
  bo->sgCapacity = pages > 0xffffffffULL ? 0 : static_cast<uint32_t>(pages);
  bo->sg = static_cast<native_powervr_sg *>(kzallocBytes(sizeof(native_powervr_sg) * bo->sgCapacity));
  if (!bo->sg) { boDestroy(bo); return -ENOMEM; }
  for (uint64_t offset = 0; offset < size;) {
    IOByteCount length = 0;
    const uint64_t phys = bo->host->getPhysicalSegment(offset, &length, kIOMemoryMapperNone);
    if (!phys || !length || (phys & (kDevicePageSize - 1))) { boDestroy(bo); return -EFAULT; }
    if (length > size - offset) length = size - offset;
    length &= kDevicePageMask;
    // The GPU's bus is 32 bits wide (PHYS_BUS_WIDTH 32): a page above 4 GiB cannot be mapped.
    if (!length || phys + length > (1ULL << 32)) { boDestroy(bo); return -ENOTTY; }
    if (bo->nents && bo->sg[bo->nents - 1].phys + bo->sg[bo->nents - 1].size == phys) {
      bo->sg[bo->nents - 1].size += length;
    } else {
      if (bo->nents == bo->sgCapacity) { boDestroy(bo); return -ENOMEM; }
      bo->sg[bo->nents++] = {phys, length};
    }
    offset += length;
  }
  *out = bo;
  return 0;
}
}  // namespace

extern "C" struct native_powervr_bo *native_powervr_bo_get(struct native_powervr_bo *bo)
{
  if (bo) refGet(&bo->refs);
  return bo;
}
extern "C" void native_powervr_bo_put(struct native_powervr_bo *bo)
{
  if (bo && refPut(&bo->refs)) boDestroy(bo);
}
extern "C" uint64_t native_powervr_bo_size(const struct native_powervr_bo *bo) { return bo ? bo->size : 0; }
extern "C" uint64_t native_powervr_bo_flags(const struct native_powervr_bo *bo) { return bo ? bo->flags : 0; }
extern "C" void *native_powervr_bo_cpu(const struct native_powervr_bo *bo) { return bo ? bo->cpu : nullptr; }
extern "C" const struct native_powervr_sg *native_powervr_bo_sg(const struct native_powervr_bo *bo, uint32_t *nents)
{
  if (nents) *nents = bo ? bo->nents : 0;
  return bo ? bo->sg : nullptr;
}

// ---- VM contexts (pvr_vm.c, drm_gpuvm replaced by a sorted mapping list) ----
namespace {
struct Mapping { uint64_t addr, range, offset; native_powervr_bo *bo; };   // drm_gpuva: va.addr, va.range, gem.offset, gem.obj
}
struct native_powervr_vm_context {
  uint32_t refs;
  IOLock *lock;               // vm_ctx->lock
  native_powervr_vm *vm;      // page tables and firmware memory context (native_powervr_internal.h)
  Mapping *maps;              // ascending, disjoint
  uint32_t count, capacity;
};

namespace {
int vmReserve(native_powervr_vm_context *ctx, uint32_t extra)
{
  if (ctx->count + extra <= ctx->capacity) return 0;
  uint32_t capacity = ctx->capacity ? ctx->capacity : 16;
  while (capacity < ctx->count + extra) {
    if (capacity > 0x7fffffffu) return -ENOMEM;
    capacity *= 2;
  }
  auto *maps = static_cast<Mapping *>(kzallocBytes(sizeof(Mapping) * capacity));
  if (!maps) return -ENOMEM;
  if (ctx->count) memcpy(maps, ctx->maps, sizeof(Mapping) * ctx->count);
  kfreeBytes(ctx->maps, sizeof(Mapping) * ctx->capacity);
  ctx->maps = maps;
  ctx->capacity = capacity;
  return 0;
}
void vmInsertAt(native_powervr_vm_context *ctx, uint32_t i, const Mapping &m)
{
  memmove(&ctx->maps[i + 1], &ctx->maps[i], sizeof(Mapping) * (ctx->count - i));
  ctx->maps[i] = m;
  ctx->count++;
}
void vmRemoveAt(native_powervr_vm_context *ctx, uint32_t i)
{
  memmove(&ctx->maps[i], &ctx->maps[i + 1], sizeof(Mapping) * (ctx->count - i - 1));
  ctx->count--;
}
// First mapping that ends after addr.
uint32_t vmFirstEndingAfter(const native_powervr_vm_context *ctx, uint64_t addr)
{
  uint32_t lo = 0, hi = ctx->count;
  while (lo < hi) {
    const uint32_t mid = lo + (hi - lo) / 2;
    if (ctx->maps[mid].addr + ctx->maps[mid].range <= addr) lo = mid + 1; else hi = mid;
  }
  return lo;
}
// pvr_vm_map() after pvr_vm_bind_op_map_init()'s checks, with vm_ctx->lock held: drm_gpuvm_sm_map() over the list.
int vmMapLocked(native_powervr_vm_context *ctx, native_powervr_bo *bo, uint64_t offset, uint64_t addr, uint64_t size)
{
  const uint64_t end = addr + size;
  int err = vmReserve(ctx, 2);   // at most one mapping is split in two, plus the new one
  if (err) return err;
  uint32_t i = vmFirstEndingAfter(ctx, addr);
  // Unmap and remap steps: every mapping that overlaps [addr, end) loses the overlap.
  while (i < ctx->count && ctx->maps[i].addr < end) {
    Mapping m = ctx->maps[i];
    const uint64_t mEnd = m.addr + m.range;
    const uint64_t from = m.addr > addr ? m.addr : addr, to = mEnd < end ? mEnd : end;
    err = native_powervr_vm_unmap(ctx->vm, from, to - from);
    if (err) return err;
    const bool keepPrev = m.addr < addr, keepNext = mEnd > end;
    if (keepPrev && keepNext) {
      ctx->maps[i].range = addr - m.addr;
      const Mapping next = {end, mEnd - end, m.offset + (end - m.addr), native_powervr_bo_get(m.bo)};
      vmInsertAt(ctx, i + 1, next);
      i += 2;
    } else if (keepPrev) {
      ctx->maps[i].range = addr - m.addr;
      i++;
    } else if (keepNext) {
      ctx->maps[i] = {end, mEnd - end, m.offset + (end - m.addr), m.bo};
      i++;
    } else {
      vmRemoveAt(ctx, i);
      native_powervr_bo_put(m.bo);
    }
  }
  // The map step (pvr_vm_gpuva_map(): pvr_mmu_map() with the BO's flags).
  err = native_powervr_vm_map_sg(ctx->vm, addr, bo->sg, bo->nents, offset, size, bo->flags);
  if (err) return err;
  vmInsertAt(ctx, vmFirstEndingAfter(ctx, addr), {addr, size, offset, native_powervr_bo_get(bo)});
  return 0;
}
void vmContextRelease(native_powervr_vm_context *ctx)
{
  // pvr_vm_context_release(): pvr_vm_unmap_all(), then the page tables and the firmware memory context.
  while (ctx->count) {
    const Mapping m = ctx->maps[0];
    if (native_powervr_vm_unmap(ctx->vm, m.addr, m.range)) IOLog("powervr: unmap of %llx+%llx failed at VM context release\n",
                                                                static_cast<unsigned long long>(m.addr), static_cast<unsigned long long>(m.range));
    vmRemoveAt(ctx, 0);
    native_powervr_bo_put(m.bo);
  }
  if (ctx->vm) native_powervr_vm_destroy(ctx->vm);
  kfreeBytes(ctx->maps, sizeof(Mapping) * ctx->capacity);
  if (ctx->lock) IOLockFree(ctx->lock);
  kfreeBytes(ctx, sizeof(*ctx));
}
// pvr_vm_create_context(pvr_dev, is_userspace_context = true)
int vmContextCreate(native_powervr_vm_context **out)
{
  if (!gDeviceAddrBits) return -EINVAL;   // "Device has unsupported virtual address space size"
  auto *ctx = static_cast<native_powervr_vm_context *>(kzallocBytes(sizeof(native_powervr_vm_context)));
  if (!ctx) return -ENOMEM;
  ctx->refs = 1;
  ctx->lock = IOLockAlloc();
  ctx->vm = ctx->lock ? native_powervr_vm_create() : nullptr;
  if (!ctx->vm) { vmContextRelease(ctx); return -ENOMEM; }
  *out = ctx;
  return 0;
}
}  // namespace

extern "C" struct native_powervr_vm_context *native_powervr_vm_context_get(struct native_powervr_vm_context *vm_ctx)
{
  if (vm_ctx) refGet(&vm_ctx->refs);
  return vm_ctx;
}
extern "C" void native_powervr_vm_context_put(struct native_powervr_vm_context *vm_ctx)
{
  if (vm_ctx && refPut(&vm_ctx->refs)) vmContextRelease(vm_ctx);
}
extern "C" struct native_powervr_vm *native_powervr_vm_context_vm(struct native_powervr_vm_context *vm_ctx) { return vm_ctx ? vm_ctx->vm : nullptr; }
extern "C" int native_powervr_vm_find_bo(struct native_powervr_vm_context *vm_ctx, uint64_t device_addr, struct native_powervr_bo_mapping *out)
{
  if (!vm_ctx || !out) return -ENOENT;
  IOLockLock(vm_ctx->lock);
  // drm_gpuva_find_first(&vm_ctx->gpuvm_mgr, device_addr, 1)
  const uint32_t i = vmFirstEndingAfter(vm_ctx, device_addr);
  const bool found = i < vm_ctx->count && vm_ctx->maps[i].addr <= device_addr;
  if (found) {
    const Mapping &m = vm_ctx->maps[i];
    *out = {native_powervr_bo_get(m.bo), m.addr, m.range, m.offset};
  }
  IOLockUnlock(vm_ctx->lock);
  return found ? 0 : -ENOENT;
}

// A job holds the exact imported pages it may access even if handles/mappings subsequently go away. The command streams do
// not enumerate shader resources, so conservatively cover every host BO mapped in the job's VM (including buffer aliases).
struct native_powervr_host_sync {
  native_powervr_bo **bos;
  uint32_t count, capacity;
};
extern "C" void native_powervr_hosts_put(struct native_powervr_host_sync *hosts)
{
  if (!hosts) return;
  for (uint32_t i = 0; i < hosts->count; i++) native_powervr_bo_put(hosts->bos[i]);
  kfreeBytes(hosts->bos, sizeof(*hosts->bos) * hosts->capacity);
  kfreeBytes(hosts, sizeof(*hosts));
}
extern "C" int native_powervr_vm_hosts_snapshot(struct native_powervr_vm_context *vm, struct native_powervr_host_sync **out)
{
  *out = nullptr;
  IOLockLock(vm->lock);
  uint32_t capacity = 0;
  for (uint32_t i = 0; i < vm->count; i++) if (vm->maps[i].bo->host) capacity++;
  if (!capacity) { IOLockUnlock(vm->lock); return 0; }
  auto *hosts = static_cast<native_powervr_host_sync *>(kzallocBytes(sizeof(native_powervr_host_sync)));
  if (hosts) {
    hosts->capacity = capacity;
    hosts->bos = static_cast<native_powervr_bo **>(kzallocBytes(sizeof(*hosts->bos) * capacity));
  }
  if (!hosts || !hosts->bos) {
    IOLockUnlock(vm->lock);
    native_powervr_hosts_put(hosts);
    return -ENOMEM;
  }
  for (uint32_t i = 0; i < vm->count; i++) {
    native_powervr_bo *bo = vm->maps[i].bo;
    if (!bo->host) continue;
    uint32_t n = 0;
    while (n < hosts->count && hosts->bos[n] != bo) n++;
    if (n == hosts->count) hosts->bos[hosts->count++] = native_powervr_bo_get(bo);
  }
  IOLockUnlock(vm->lock);
  *out = hosts;
  return 0;
}
extern "C" int native_powervr_hosts_sync(struct native_powervr_host_sync *hosts)
{
  if (!hosts) return 0;
  // NativeAdrenoMaintainPhysicalPages: explicit PoC operations, bypassing XNU's Apple coherent-I/O no-op. Keep preemption
  // disabled for at most 64 pages. Imported pages can contain the owner's unrelated dirty lines, so clean AND invalidate.
  for (uint32_t b = 0; b < hosts->count; b++) {
    const native_powervr_bo *bo = hosts->bos[b];
    for (uint32_t s = 0; s < bo->nents; s++) {
      const native_powervr_sg &span = bo->sg[s];
      for (uint64_t offset = 0; offset < span.size;) {
        disable_preemption();
        uint64_t ctr;
        __asm__ volatile("mrs %0, ctr_el0" : "=r"(ctr));
        const size_t line = size_t(4) << ((ctr >> 16) & 15);
        if (line > kDevicePageSize) { enable_preemption(); return -ENOTTY; }
        __asm__ volatile("dsb sy" ::: "memory");
        for (unsigned run = 0; run < 64 && offset < span.size; run++, offset += kDevicePageSize) {
          const uint64_t page = span.phys + offset;
          // The firmware scanout is outside the DRAM physmap and mapped uncached. Never call phystokv on device memory.
          if (page < gPhysBase || page - gPhysBase >= gPhysSize) continue;
          const vm_offset_t alias = phystokv(page);
          if (!alias) { __asm__ volatile("dsb sy" ::: "memory"); enable_preemption(); return -ENOTTY; }
          for (uintptr_t p = alias; p < alias + kDevicePageSize; p += line)
            __asm__ volatile("dc civac, %0" :: "r"(p) : "memory");
        }
        __asm__ volatile("dsb sy" ::: "memory");
        enable_preemption();
      }
    }
  }
  return 0;
}
extern "C" const struct pvr_device_features *native_powervr_device_features(void) { return gDevLoaded ? &gDev.features : nullptr; }
extern "C" const struct pvr_device_quirks *native_powervr_device_quirks(void) { return gDevLoaded ? &gDev.quirks : nullptr; }
extern "C" const struct pvr_device_enhancements *native_powervr_device_enhancements(void) { return gDevLoaded ? &gDev.enhancements : nullptr; }

// ---- The user client ----
class NativePowerVRUserClient : public IOUserClient {
  OSDeclareDefaultStructors(NativePowerVRUserClient)
  IOLock *lock_;
  bool closing_;
  // A connection of the Metal plugin that serves only the display hand-off (NativePowerVRMetal.cpp, open type
  // NativePowerVRMetalHandoffOpenType): no handles, no GPU work, the NGP methods and the scanout memory type only.
  bool handoffOnly_;
  native_powervr_jobs_file *jobs_;
  HandleTable bos_, vmCtxs_, ctxs_, freeLists_, hwrts_;

  IOReturn run(uint32_t method, const void *input, uint32_t inputSize, void *output, uint32_t outputSize);
  IOReturn handoff(uint32_t method, const void *input, uint32_t inputSize, void *output, uint32_t outputSize);
  int call(uint32_t method, void *args, void *reply);
  int devQuery(drm_pvr_ioctl_dev_query_args *args);
  int createBo(drm_pvr_ioctl_create_bo_args *args);
  int importHost(NativePowerVRAbiImportHost *args);
  int getBoMmapOffset(drm_pvr_ioctl_get_bo_mmap_offset_args *args);
  int gemClose(const drm_gem_close *args);
  int createVmContext(drm_pvr_ioctl_create_vm_context_args *args);
  int destroyVmContext(const drm_pvr_ioctl_destroy_vm_context_args *args);
  int vmMap(const drm_pvr_ioctl_vm_map_args *args);
  int vmUnmap(const drm_pvr_ioctl_vm_unmap_args *args);
  int createContext(drm_pvr_ioctl_create_context_args *args);
  int destroyContext(const drm_pvr_ioctl_destroy_context_args *args);
  int createFreeList(drm_pvr_ioctl_create_free_list_args *args);
  int destroyFreeList(const drm_pvr_ioctl_destroy_free_list_args *args);
  int createHwrtDataset(drm_pvr_ioctl_create_hwrt_dataset_args *args);
  int destroyHwrtDataset(const drm_pvr_ioctl_destroy_hwrt_dataset_args *args);
  int submitJobs(const drm_pvr_ioctl_submit_jobs_args *args, NativePowerVRAbiSubmitReply *reply);
  void teardown();
public:
  using IOUserClient::clientMemoryForType;
  bool open(bool handoffOnly);
  IOReturn clientClose() APPLE_KEXT_OVERRIDE;
  void stop(IOService *provider) APPLE_KEXT_OVERRIDE;
  void free() APPLE_KEXT_OVERRIDE;
  IOReturn clientMemoryForType(UInt32 type, IOOptionBits *options, IOMemoryDescriptor **memory) APPLE_KEXT_OVERRIDE;
  IOReturn externalMethod(uint32_t selector, IOExternalMethodArguments *arguments, IOExternalMethodDispatch *dispatch,
                          OSObject *target, void *reference) APPLE_KEXT_OVERRIDE;
};
OSDefineMetaClassAndStructors(NativePowerVRUserClient, IOUserClient)

class NativePowerVRGpu : public IOService {
  OSDeclareDefaultStructors(NativePowerVRGpu)
public:
  using IOService::newUserClient;
  IOReturn newUserClient(task_t owningTask, void *securityID, UInt32 type, OSDictionary *properties,
                         IOUserClient **handler) APPLE_KEXT_OVERRIDE;
};
OSDefineMetaClassAndStructors(NativePowerVRGpu, IOService)

namespace {
NativePowerVRGpu *gService;
}

// pvr_drm_driver_open()
bool NativePowerVRUserClient::open(bool handoffOnly)
{
  lock_ = IOLockAlloc();
  if (!lock_) return false;
  handoffOnly_ = handoffOnly;
  if (handoffOnly)
    return native_gop_handoff_acquire && native_gop_handoff_close && native_gop_handoff_call_owned &&
           native_gop_handoff_acquire(this) == kIOReturnSuccess;
  if (native_powervr_jobs_file_create) {
    jobs_ = native_powervr_jobs_file_create();
    if (!jobs_) return false;
  }
  return true;
}

// One connection on provider: this service's own (open type 0), or the Metal accelerator's (NativePowerVRMetal.cpp), which
// attaches its connections to itself so that the sandbox grant of the accelerator covers them.
static IOReturn newConnection(IOService *provider, task_t owningTask, void *securityID, UInt32 type, OSDictionary *properties,
                              bool handoffOnly, IOUserClient **handler)
{
  if (!handler) return kIOReturnBadArgument;
  *handler = nullptr;
  NativePowerVRUserClient *client = new NativePowerVRUserClient;
  if (!client) return kIOReturnNoMemory;
  if (!client->initWithTask(owningTask, securityID, type, properties) || !client->open(handoffOnly)) {
    client->release();
    return kIOReturnNoMemory;
  }
  if (!client->attach(provider)) {
    client->release();
    return kIOReturnError;
  }
  if (!client->start(provider)) {
    client->detach(provider);
    client->release();
    return kIOReturnError;
  }
  *handler = client;
  return kIOReturnSuccess;
}

IOReturn NativePowerVRGpu::newUserClient(task_t owningTask, void *securityID, UInt32 type, OSDictionary *properties,
                                         IOUserClient **handler)
{
  if (handler) *handler = nullptr;
  if (type != 0) return kIOReturnUnsupported;   // NativePowerVRABI.h: open type 0
  return newConnection(this, owningTask, securityID, type, properties, false, handler);
}

// For the Metal accelerator (NativePowerVRMetal.cpp): a GPU connection, or one that serves only the display hand-off. Refused
// until this service is published (the firmware runs).
extern "C" IOReturn native_powervr_new_connection(IOService *provider, task_t owningTask, void *securityID, UInt32 type,
                                                  OSDictionary *properties, bool handoffOnly, IOUserClient **handler)
{
  if (handler) *handler = nullptr;
  if (!gService || !provider) return kIOReturnNotReady;
  return newConnection(provider, owningTask, securityID, type, properties, handoffOnly, handler);
}
extern "C" bool native_powervr_published(void) { return gService != nullptr; }

// pvr_drm_driver_postclose(), after drm_gem_release(): the contexts first, so that no job runs on what is freed after them.
void NativePowerVRUserClient::teardown()
{
  while (void *ctx = handlePop(ctxs_)) native_powervr_context_destroy(static_cast<native_powervr_context *>(ctx));
  while (void *fl = handlePop(freeLists_)) native_powervr_free_list_put(static_cast<native_powervr_free_list *>(fl));
  while (void *hwrt = handlePop(hwrts_)) native_powervr_hwrt_dataset_put(static_cast<native_powervr_hwrt_dataset *>(hwrt));
  if (jobs_) native_powervr_jobs_file_destroy(jobs_);
  jobs_ = nullptr;
  while (void *vm = handlePop(vmCtxs_)) native_powervr_vm_context_put(static_cast<native_powervr_vm_context *>(vm));
  while (void *bo = handlePop(bos_)) native_powervr_bo_put(static_cast<native_powervr_bo *>(bo));
  handleFree(ctxs_);
  handleFree(freeLists_);
  handleFree(hwrts_);
  handleFree(vmCtxs_);
  handleFree(bos_);
}

IOReturn NativePowerVRUserClient::clientClose()
{
  // New calls are refused from here on; the objects go in free(), which runs once no call holds the connection any more
  // (every external method and mapping call holds a reference to it), so nothing is freed under a running call.
  if (lock_) {
    IOLockLock(lock_);
    closing_ = true;
    IOLockUnlock(lock_);
  }
  if (handoffOnly_ && native_gop_handoff_close) native_gop_handoff_close(this);
  terminate();
  return kIOReturnSuccess;
}

void NativePowerVRUserClient::stop(IOService *provider)
{
  if (lock_) { IOLockLock(lock_); closing_ = true; IOLockUnlock(lock_); }
  if (handoffOnly_ && native_gop_handoff_close) native_gop_handoff_close(this);
  IOUserClient::stop(provider);
}

void NativePowerVRUserClient::free()
{
  if (handoffOnly_ && native_gop_handoff_close) native_gop_handoff_close(this);
  if (lock_) {
    teardown();
    IOLockFree(lock_);
    lock_ = nullptr;
  }
  IOUserClient::free();
}

IOReturn NativePowerVRUserClient::clientMemoryForType(UInt32 type, IOOptionBits *options, IOMemoryDescriptor **memory)
{
  if (!options || !memory) return kIOReturnBadArgument;
  *memory = nullptr;
  if (!lock_) return kIOReturnNotReady;
  if (handoffOnly_) {
    // The scanout, writable, for the GPU copy of the hand-off (as NativeAdreno's NativeAdrenoScanoutType): device memory, never
    // cached by the CPU. Only in GPU hand-off mode (sheng-fb-gpu=1) does the presenter hand it out.
    if (type != NativePowerVRMetalScanoutType) return kIOReturnBadArgument;
    IOMemoryDescriptor *scanout = native_gop_handoff_memory ? native_gop_handoff_memory() : nullptr;
    if (!scanout) return kIOReturnNotReady;
    *memory = scanout;                             // already retained for the caller
    *options = kIOMapAnywhere | kIOMapInhibitCache;
    return kIOReturnSuccess;
  }
  IOReturn status = kIOReturnSuccess;
  IOLockLock(lock_);
  auto *bo = closing_ ? nullptr : static_cast<native_powervr_bo *>(handleLookup(bos_, type));
  if (!bo) {
    status = closing_ ? kIOReturnNotReady : kIOReturnNotFound;
  } else if (!(bo->flags & DRM_PVR_BO_ALLOW_CPU_USERSPACE_ACCESS)) {
    status = kIOReturnBadArgument;   // pvr_gem_mmap(): -EINVAL
  } else {
    if (!bo->view) bo->view = NativePowerVRBoView::withBacking(bo->md, bo->sg, bo->nents, bo->size);
    if (!bo->view) {
      status = kIOReturnNoMemory;
    } else {
      bo->view->retain();
      *memory = bo->view;
      // The cache bits are replaced by the caller's map flags (mapClientMemory64()); the view's own mode is write-combine.
      *options = kIOMapAnywhere | kIOMapWriteCombineCache;
    }
  }
  IOLockUnlock(lock_);
  return status;
}

IOReturn NativePowerVRUserClient::externalMethod(uint32_t selector, IOExternalMethodArguments *arguments, IOExternalMethodDispatch *,
                                                 OSObject *, void *)
{
  if (!arguments || arguments->scalarInputCount || arguments->scalarOutputCount || arguments->structureInputDescriptor ||
      arguments->structureOutputDescriptor || arguments->asyncReference)
    return kIOReturnBadArgument;
  const uint8_t *input = static_cast<const uint8_t *>(arguments->structureInput);
  uint32_t inputSize = input ? arguments->structureInputSize : 0;
  uint32_t method = selector;
  // The tunnel (NativeAdrenoPublish.cpp): {NativePowerVRTunnelMagic, method} before the method's input, on any selector.
  uint32_t header[2] = {};
  if (inputSize >= sizeof(header)) memcpy(header, input, sizeof(header));
  if (inputSize >= sizeof(header) && header[0] == NativePowerVRTunnelMagic) {
    if (header[1] == NativePowerVRTunnelProbe)
      return inputSize == sizeof(header) && !arguments->structureOutputSize ? kIOReturnSuccess : kIOReturnBadArgument;
    method = header[1];
    input += sizeof(header);
    inputSize -= static_cast<uint32_t>(sizeof(header));
  }
  return run(method, inputSize ? input : nullptr, inputSize, arguments->structureOutput, arguments->structureOutput ? arguments->structureOutputSize : 0);
}

// The display hand-off (UniXNU drivers/NativeMobileDisplay/NativeGOPPresenter.cpp handoffCall()): methods
// NativePowerVRMetalHandoffBase + NGP_METHOD_* forwarded unchanged; a wait sleeps, so no connection lock is held.
IOReturn NativePowerVRUserClient::handoff(uint32_t method, const void *input, uint32_t inputSize, void *output, uint32_t outputSize)
{
  if (method < NativePowerVRMetalHandoffBase || method > NativePowerVRMetalHandoffLast) return kIOReturnUnsupported;
  if (!native_gop_handoff_call_owned) return kIOReturnNotReady;
  IOLockLock(lock_);
  const bool closing = closing_;
  IOLockUnlock(lock_);
  if (closing) return kIOReturnNotReady;
  return native_gop_handoff_call_owned(this, method - NativePowerVRMetalHandoffBase, current_task(), input, inputSize, output, outputSize);
}

IOReturn NativePowerVRUserClient::run(uint32_t method, const void *input, uint32_t inputSize, void *output, uint32_t outputSize)
{
  if (lock_ && handoffOnly_) return handoff(method, input, inputSize, output, outputSize);
  // The records of NativePowerVRABI.h: in/out methods take and return the same struct, input-only methods return nothing.
  struct Shape { uint32_t method, input, output; };
  static const Shape kShapes[] = {
    {NativePowerVRMethodDevQuery, sizeof(drm_pvr_ioctl_dev_query_args), sizeof(drm_pvr_ioctl_dev_query_args)},
    {NativePowerVRMethodCreateBo, sizeof(drm_pvr_ioctl_create_bo_args), sizeof(drm_pvr_ioctl_create_bo_args)},
    {NativePowerVRMethodGetBoMmapOffset, sizeof(drm_pvr_ioctl_get_bo_mmap_offset_args), sizeof(drm_pvr_ioctl_get_bo_mmap_offset_args)},
    {NativePowerVRMethodCreateVmContext, sizeof(drm_pvr_ioctl_create_vm_context_args), sizeof(drm_pvr_ioctl_create_vm_context_args)},
    {NativePowerVRMethodDestroyVmContext, sizeof(drm_pvr_ioctl_destroy_vm_context_args), 0},
    {NativePowerVRMethodVmMap, sizeof(drm_pvr_ioctl_vm_map_args), 0},
    {NativePowerVRMethodVmUnmap, sizeof(drm_pvr_ioctl_vm_unmap_args), 0},
    {NativePowerVRMethodCreateContext, sizeof(drm_pvr_ioctl_create_context_args), sizeof(drm_pvr_ioctl_create_context_args)},
    {NativePowerVRMethodDestroyContext, sizeof(drm_pvr_ioctl_destroy_context_args), 0},
    {NativePowerVRMethodCreateFreeList, sizeof(drm_pvr_ioctl_create_free_list_args), sizeof(drm_pvr_ioctl_create_free_list_args)},
    {NativePowerVRMethodDestroyFreeList, sizeof(drm_pvr_ioctl_destroy_free_list_args), 0},
    {NativePowerVRMethodCreateHwrtDataset, sizeof(drm_pvr_ioctl_create_hwrt_dataset_args), sizeof(drm_pvr_ioctl_create_hwrt_dataset_args)},
    {NativePowerVRMethodDestroyHwrtDataset, sizeof(drm_pvr_ioctl_destroy_hwrt_dataset_args), 0},
    {NativePowerVRMethodSubmitJobs, sizeof(drm_pvr_ioctl_submit_jobs_args), sizeof(NativePowerVRAbiSubmitReply)},
    {NativePowerVRMethodGemClose, sizeof(drm_gem_close), 0},
    {NativePowerVRMethodWaitFence, sizeof(NativePowerVRAbiWaitFence), 0},
    {NativePowerVRMethodGetParams, 0, sizeof(NativePowerVRAbiParams)},
    {NativePowerVRMethodImportHostMemory, sizeof(NativePowerVRAbiImportHost), sizeof(NativePowerVRAbiImportHost)},
  };
  const Shape *shape = nullptr;
  for (const Shape &s : kShapes) if (s.method == method) shape = &s;
  if (!shape) return kIOReturnUnsupported;
  if (inputSize != shape->input || outputSize != shape->output || (shape->input && !input) || (shape->output && !output))
    return kIOReturnBadArgument;
  if (!lock_) return kIOReturnNotReady;

  if (method == NativePowerVRMethodGetParams) {
    const NativePowerVRAbiParams params = {NativePowerVRAbiVersion, NativePowerVRAbiMaxJobs};
    memcpy(output, &params, sizeof(params));
    return kIOReturnSuccess;
  }
  if (method == NativePowerVRMethodWaitFence) {
    // Outside the connection lock, so other threads of the connection submit and map while one waits.
    NativePowerVRAbiWaitFence wait;
    memcpy(&wait, input, sizeof(wait));
    IOLockLock(lock_);
    const bool closing = closing_;
    native_powervr_jobs_file *jobs = jobs_;
    IOLockUnlock(lock_);
    if (closing) return kIOReturnNotReady;
    if (!jobs || !native_powervr_fence_wait) return kIOReturnUnsupported;
    const int err = native_powervr_fence_wait(jobs, wait.fence, wait.timeout_ns);
    if (!err) return kIOReturnSuccess;
    if (err == -ETIMEDOUT) return kIOReturnTimeout;
    if (err == -EINVAL) return kIOReturnBadArgument;
    return kIOReturnIOError;   // the job failed or its context was lost
  }
  // The largest record is drm_pvr_ioctl_create_hwrt_dataset_args (136 bytes).
  uint64_t args[136 / 8] = {};
  static_assert(sizeof(drm_pvr_ioctl_create_hwrt_dataset_args) <= sizeof(args), "argument buffer");
  static_assert(sizeof(NativePowerVRAbiImportHost) <= sizeof(args), "argument buffer");
  memcpy(args, input, shape->input);
  NativePowerVRAbiSubmitReply reply = {};
  IOLockLock(lock_);
  const int err = closing_ ? -ENODEV : call(method, args, &reply);
  IOLockUnlock(lock_);
  if (closing_) return kIOReturnNotReady;
  if (err) return ioReturnOf(err);
  if (method == NativePowerVRMethodSubmitJobs) memcpy(output, &reply, sizeof(reply));
  else if (shape->output) memcpy(output, args, shape->output);
  return kIOReturnSuccess;
}

int NativePowerVRUserClient::call(uint32_t method, void *args, void *reply)
{
  switch (method) {
  case NativePowerVRMethodDevQuery: return devQuery(static_cast<drm_pvr_ioctl_dev_query_args *>(args));
  case NativePowerVRMethodCreateBo: return createBo(static_cast<drm_pvr_ioctl_create_bo_args *>(args));
  case NativePowerVRMethodGetBoMmapOffset: return getBoMmapOffset(static_cast<drm_pvr_ioctl_get_bo_mmap_offset_args *>(args));
  case NativePowerVRMethodCreateVmContext: return createVmContext(static_cast<drm_pvr_ioctl_create_vm_context_args *>(args));
  case NativePowerVRMethodDestroyVmContext: return destroyVmContext(static_cast<drm_pvr_ioctl_destroy_vm_context_args *>(args));
  case NativePowerVRMethodVmMap: return vmMap(static_cast<drm_pvr_ioctl_vm_map_args *>(args));
  case NativePowerVRMethodVmUnmap: return vmUnmap(static_cast<drm_pvr_ioctl_vm_unmap_args *>(args));
  case NativePowerVRMethodCreateContext: return createContext(static_cast<drm_pvr_ioctl_create_context_args *>(args));
  case NativePowerVRMethodDestroyContext: return destroyContext(static_cast<drm_pvr_ioctl_destroy_context_args *>(args));
  case NativePowerVRMethodCreateFreeList: return createFreeList(static_cast<drm_pvr_ioctl_create_free_list_args *>(args));
  case NativePowerVRMethodDestroyFreeList: return destroyFreeList(static_cast<drm_pvr_ioctl_destroy_free_list_args *>(args));
  case NativePowerVRMethodCreateHwrtDataset: return createHwrtDataset(static_cast<drm_pvr_ioctl_create_hwrt_dataset_args *>(args));
  case NativePowerVRMethodDestroyHwrtDataset: return destroyHwrtDataset(static_cast<drm_pvr_ioctl_destroy_hwrt_dataset_args *>(args));
  case NativePowerVRMethodSubmitJobs:
    return submitJobs(static_cast<drm_pvr_ioctl_submit_jobs_args *>(args), static_cast<NativePowerVRAbiSubmitReply *>(reply));
  case NativePowerVRMethodGemClose: return gemClose(static_cast<drm_gem_close *>(args));
  case NativePowerVRMethodImportHostMemory: return importHost(static_cast<NativePowerVRAbiImportHost *>(args));
  default: return -ENOTTY;
  }
}

// pvr_ioctl_dev_query() and its pvr_dev_query_*_get() / pvr_heap_info_get() / pvr_static_data_areas_get()
int NativePowerVRUserClient::devQuery(drm_pvr_ioctl_dev_query_args *args)
{
  const native_powervr_device_info *pvr_dev = &gDev;
  int err;
  switch (args->type) {
  case DRM_PVR_DEV_QUERY_GPU_INFO_GET: {
    drm_pvr_dev_query_gpu_info gpu_info = {};
    if (!args->pointer) { args->size = sizeof(gpu_info); return 0; }
    gpu_info.gpu_id = gGpuId;
    gpu_info.num_phantoms = rogue_get_num_phantoms(pvr_dev);
    err = PVR_UOBJ_SET(args->pointer, args->size, gpu_info);
    if (err < 0) return err;
    if (args->size > sizeof(gpu_info)) args->size = sizeof(gpu_info);
    return 0;
  }
  case DRM_PVR_DEV_QUERY_RUNTIME_INFO_GET: {
    drm_pvr_dev_query_runtime_info runtime_info = {};
    if (!args->pointer) { args->size = sizeof(runtime_info); return 0; }
    runtime_info.free_list_min_pages = get_free_list_min_pages(pvr_dev);
    runtime_info.free_list_max_pages = ROGUE_PM_MAX_FREELIST_SIZE / ROGUE_PM_PAGE_SIZE;
    runtime_info.common_store_alloc_region_size = rogue_get_common_store_alloc_region_size(pvr_dev);
    runtime_info.common_store_partition_space_size = rogue_get_common_store_partition_space_size(pvr_dev);
    runtime_info.max_coeffs = rogue_get_max_coeffs(pvr_dev);
    runtime_info.cdm_max_local_mem_size_regs = rogue_get_cdm_max_local_mem_size_regs(pvr_dev);
    err = PVR_UOBJ_SET(args->pointer, args->size, runtime_info);
    if (err < 0) return err;
    if (args->size > sizeof(runtime_info)) args->size = sizeof(runtime_info);
    return 0;
  }
  case DRM_PVR_DEV_QUERY_QUIRKS_GET: {
    static const uint32_t umd_quirks_musthave[] = {47217, 49927, 62269};
    static const uint32_t umd_quirks[] = {48545, 51764};
    drm_pvr_dev_query_quirks query;
    uint32_t out[ARRAY_SIZE(umd_quirks_musthave) + ARRAY_SIZE(umd_quirks)];
    uint16_t out_musthave_count = 0, out_count = 0;
    if (!args->pointer) { args->size = sizeof(query); return 0; }
    err = PVR_UOBJ_GET(query, args->size, args->pointer);
    if (err < 0) return err;
    if (query._padding_c) return -EINVAL;
    for (uint32_t q : umd_quirks_musthave)
      if (has_uapi_quirk(pvr_dev, q)) { out[out_count++] = q; out_musthave_count++; }
    for (uint32_t q : umd_quirks)
      if (has_uapi_quirk(pvr_dev, q)) out[out_count++] = q;
    if (query.quirks) {
      if (query.count < out_count) return -E2BIG;
      if (copy_to_user(query.quirks, out, out_count * sizeof(uint32_t))) return -EFAULT;
      query.musthave_count = out_musthave_count;
    }
    query.count = out_count;
    err = PVR_UOBJ_SET(args->pointer, args->size, query);
    if (err < 0) return err;
    if (args->size > sizeof(query)) args->size = sizeof(query);
    return 0;
  }
  case DRM_PVR_DEV_QUERY_ENHANCEMENTS_GET: {
    static const uint32_t umd_enhancements[] = {35421, 42064};
    drm_pvr_dev_query_enhancements query;
    uint32_t out[ARRAY_SIZE(umd_enhancements)];
    uint16_t out_idx = 0;
    if (!args->pointer) { args->size = sizeof(query); return 0; }
    err = PVR_UOBJ_GET(query, args->size, args->pointer);
    if (err < 0) return err;
    if (query._padding_a || query._padding_c) return -EINVAL;
    for (uint32_t e : umd_enhancements)
      if (has_uapi_enhancement(pvr_dev, e)) out[out_idx++] = e;
    if (query.enhancements) {
      if (query.count < out_idx) return -E2BIG;
      if (copy_to_user(query.enhancements, out, out_idx * sizeof(uint32_t))) return -EFAULT;
    }
    query.count = out_idx;
    err = PVR_UOBJ_SET(args->pointer, args->size, query);
    if (err < 0) return err;
    if (args->size > sizeof(query)) args->size = sizeof(query);
    return 0;
  }
  case DRM_PVR_DEV_QUERY_HEAP_INFO_GET: {
    drm_pvr_dev_query_heap_info query = {};
    if (!args->pointer) { args->size = sizeof(query); return 0; }
    err = PVR_UOBJ_GET(query, args->size, args->pointer);
    if (err < 0) return err;
    if (!query.heaps.array) {
      query.heaps.count = ARRAY_SIZE(kHeaps);
      query.heaps.stride = sizeof(drm_pvr_heap);
    } else {
      if (query.heaps.count > ARRAY_SIZE(kHeaps)) query.heaps.count = ARRAY_SIZE(kHeaps);
      // Region header heap is only present if BRN63142 is present.
      uint64_t dest = query.heaps.array;
      for (uint32_t i = 0; i < query.heaps.count; i++) {
        drm_pvr_heap heap = kHeaps[i];
        if (i == DRM_PVR_HEAP_RGNHDR && !PVR_HAS_QUIRK(pvr_dev, 63142)) heap.size = 0;
        err = PVR_UOBJ_SET(dest, query.heaps.stride, heap);
        if (err < 0) return err;
        dest += query.heaps.stride;
      }
    }
    err = PVR_UOBJ_SET(args->pointer, args->size, query);
    if (err < 0) return err;
    if (args->size > sizeof(query)) args->size = sizeof(query);
    return 0;
  }
  case DRM_PVR_DEV_QUERY_STATIC_DATA_AREAS_GET: {
    drm_pvr_dev_query_static_data_areas query = {};
    if (!args->pointer) { args->size = sizeof(query); return 0; }
    err = PVR_UOBJ_GET(query, args->size, args->pointer);
    if (err < 0) return err;
    if (!query.static_data_areas.array) {
      query.static_data_areas.count = ARRAY_SIZE(kStaticDataAreas);
      query.static_data_areas.stride = sizeof(drm_pvr_static_data_area);
    } else {
      if (query.static_data_areas.count > ARRAY_SIZE(kStaticDataAreas)) query.static_data_areas.count = ARRAY_SIZE(kStaticDataAreas);
      err = pvr_set_uobj_array(&query.static_data_areas, uobjMinSize(kStaticDataAreas[0]), sizeof(kStaticDataAreas[0]), kStaticDataAreas);
      if (err < 0) return err;
    }
    err = PVR_UOBJ_SET(args->pointer, args->size, query);
    if (err < 0) return err;
    if (args->size > sizeof(query)) args->size = sizeof(query);
    return 0;
  }
  default:
    return -EINVAL;
  }
}

// pvr_ioctl_create_bo()
int NativePowerVRUserClient::createBo(drm_pvr_ioctl_create_bo_args *args)
{
  // All padding fields must be zeroed.
  if (args->_padding_c != 0) return -EINVAL;
  // Zero-sized allocations, reserved (kernel-only) flags and sizes that are not device pages are refused.
  if (args->size > SIZE_MAX || args->size == 0 || (args->flags & ~static_cast<uint64_t>(DRM_PVR_BO_FLAGS_MASK)) ||
      (args->size & (kDevicePageSize - 1)))
    return -EINVAL;
  // pvr_gem_object_flags_validate(): PM/FW-protected memory cannot be mapped to user space.
  const uint64_t invalid = DRM_PVR_BO_PM_FW_PROTECT | DRM_PVR_BO_ALLOW_CPU_USERSPACE_ACCESS;
  if ((args->flags & invalid) == invalid) return -EINVAL;
  native_powervr_bo *bo = nullptr;
  int err = boCreate(args->size, args->flags, &bo);
  if (err) return err;
  // pvr_gem_object_into_handle(): &args->handle is not modified unless it succeeds.
  uint32_t handle = 0;
  err = handleInsert(bos_, bo, &handle);
  if (err) { native_powervr_bo_put(bo); return err; }
  args->handle = handle;
  return 0;
}

// NativePowerVRMethodImportHostMemory (NativePowerVRABI.h): a BO over pages of the calling task; the handle is a BO handle.
int NativePowerVRUserClient::importHost(NativePowerVRAbiImportHost *args)
{
  if (args->handle || args->read_only || !args->address || !args->size || ((args->address | args->size) & (kDevicePageSize - 1)) ||
      args->address + args->size < args->address || args->size > SIZE_MAX ||
      (args->flags & ~static_cast<uint64_t>(DRM_PVR_BO_BYPASS_DEVICE_CACHE)))
    return -EINVAL;
  native_powervr_bo *bo = nullptr;
  int err = boImportHost(current_task(), args->address, args->size, args->flags, &bo);
  if (err) return err;
  uint32_t handle = 0;
  err = handleInsert(bos_, bo, &handle);
  if (err) { native_powervr_bo_put(bo); return err; }
  args->handle = handle;
  args->read_only = 0;   // read-only backing is rejected by boImportHost()
  return 0;
}

// pvr_ioctl_get_bo_mmap_offset(): the "offset" is the memory type of clientMemoryForType(), the handle.
int NativePowerVRUserClient::getBoMmapOffset(drm_pvr_ioctl_get_bo_mmap_offset_args *args)
{
  if (args->_padding_4 != 0) return -EINVAL;
  if (!handleLookup(bos_, args->handle)) return -ENOENT;
  args->offset = args->handle;
  return 0;
}

// drm_gem_close_ioctl() -> drm_gem_handle_delete(): the handle's reference goes; mappings keep the BO.
int NativePowerVRUserClient::gemClose(const drm_gem_close *args)
{
  auto *bo = static_cast<native_powervr_bo *>(handleErase(bos_, args->handle));
  if (!bo) return -EINVAL;
  native_powervr_bo_put(bo);
  return 0;
}

// pvr_ioctl_create_vm_context()
int NativePowerVRUserClient::createVmContext(drm_pvr_ioctl_create_vm_context_args *args)
{
  if (args->_padding_4) return -EINVAL;
  native_powervr_vm_context *vm_ctx = nullptr;
  int err = vmContextCreate(&vm_ctx);
  if (err) return err;
  uint32_t handle = 0;
  err = handleInsert(vmCtxs_, vm_ctx, &handle);
  if (err) { native_powervr_vm_context_put(vm_ctx); return err; }
  args->handle = handle;
  return 0;
}

// pvr_ioctl_destroy_vm_context()
int NativePowerVRUserClient::destroyVmContext(const drm_pvr_ioctl_destroy_vm_context_args *args)
{
  if (args->_padding_4) return -EINVAL;
  auto *vm_ctx = static_cast<native_powervr_vm_context *>(handleErase(vmCtxs_, args->handle));
  if (!vm_ctx) return -EINVAL;
  native_powervr_vm_context_put(vm_ctx);
  return 0;
}

// pvr_ioctl_vm_map() -> pvr_vm_map() -> pvr_vm_bind_op_map_init() + pvr_vm_bind_op_exec()
int NativePowerVRUserClient::vmMap(const drm_pvr_ioctl_vm_map_args *args)
{
  uint64_t offset_plus_size;
  if (args->_padding_14) return -EINVAL;
  if (args->flags != 0 || __builtin_add_overflow(args->offset, args->size, &offset_plus_size) ||
      !find_heap_containing(args->device_addr, args->size))
    return -EINVAL;
  auto *vm_ctx = static_cast<native_powervr_vm_context *>(handleLookup(vmCtxs_, args->vm_context_handle));
  if (!vm_ctx) return -EINVAL;
  auto *bo = static_cast<native_powervr_bo *>(handleLookup(bos_, args->handle));
  if (!bo) return -ENOENT;
  if (args->offset >= bo->size || offset_plus_size > bo->size) return -EINVAL;
  // pvr_vm_bind_op_map_init()
  if (!device_addr_and_size_are_valid(args->device_addr, args->size) || (args->offset & ~kDevicePageMask) ||
      (args->size & ~kDevicePageMask))
    return -EINVAL;
  IOLockLock(vm_ctx->lock);
  const int err = vmMapLocked(vm_ctx, bo, args->offset, args->device_addr, args->size);
  IOLockUnlock(vm_ctx->lock);
  return err;
}

// pvr_ioctl_vm_unmap() -> pvr_vm_unmap(): drm_gpuva_find() needs the exact mapping.
int NativePowerVRUserClient::vmUnmap(const drm_pvr_ioctl_vm_unmap_args *args)
{
  if (args->_padding_4) return -EINVAL;
  auto *vm_ctx = static_cast<native_powervr_vm_context *>(handleLookup(vmCtxs_, args->vm_context_handle));
  if (!vm_ctx) return -EINVAL;
  IOLockLock(vm_ctx->lock);
  int err = -ENOENT;
  const uint32_t i = vmFirstEndingAfter(vm_ctx, args->device_addr);
  if (i < vm_ctx->count && vm_ctx->maps[i].addr == args->device_addr && vm_ctx->maps[i].range == args->size) {
    const Mapping m = vm_ctx->maps[i];
    err = native_powervr_vm_unmap(vm_ctx->vm, m.addr, m.range);
    if (!err) {
      vmRemoveAt(vm_ctx, i);
      native_powervr_bo_put(m.bo);
    }
  }
  IOLockUnlock(vm_ctx->lock);
  return err;
}

namespace {
// memdup_user() of a stream the job layer reads (pvr_context.c process_static_context_state(), pvr_job.c cmd_stream).
int dupUserStream(uint64_t pointer, uint32_t length, void **out)
{
  *out = nullptr;
  if (!length) return 0;
  if (length > kMaxUserStream) return -EINVAL;
  void *copy = kzallocBytes(length);
  if (!copy) return -ENOMEM;
  const int err = copy_from_user(copy, pointer, length);
  if (err) { kfreeBytes(copy, length); return err; }
  *out = copy;
  return 0;
}
}  // namespace

// pvr_ioctl_create_context() -> pvr_context_create()
int NativePowerVRUserClient::createContext(drm_pvr_ioctl_create_context_args *args)
{
  if (!jobs_ || !native_powervr_context_create || !native_powervr_context_destroy) return -ENOTTY;
  auto *vm_ctx = static_cast<native_powervr_vm_context *>(handleLookup(vmCtxs_, args->vm_context_handle));
  if (!vm_ctx) return -EINVAL;
  void *state = nullptr;
  int err = dupUserStream(args->static_context_state, args->static_context_state_len, &state);
  if (err) return err;
  native_powervr_context *ctx = nullptr;
  err = native_powervr_context_create(jobs_, vm_ctx, args, state, &ctx);
  kfreeBytes(state, args->static_context_state_len);
  if (err) return err;
  uint32_t handle = 0;
  err = handleInsert(ctxs_, ctx, &handle);
  if (err) { native_powervr_context_destroy(ctx); return err; }
  args->handle = handle;
  return 0;
}

// pvr_ioctl_destroy_context() -> pvr_context_destroy()
int NativePowerVRUserClient::destroyContext(const drm_pvr_ioctl_destroy_context_args *args)
{
  if (args->_padding_4) return -EINVAL;
  if (!native_powervr_context_destroy) return -ENOTTY;
  auto *ctx = static_cast<native_powervr_context *>(handleErase(ctxs_, args->handle));
  if (!ctx) return -EINVAL;
  native_powervr_context_destroy(ctx);
  return 0;
}

// pvr_ioctl_create_free_list() -> pvr_free_list_create()
int NativePowerVRUserClient::createFreeList(drm_pvr_ioctl_create_free_list_args *args)
{
  if (!jobs_ || !native_powervr_free_list_create || !native_powervr_free_list_put) return -ENOTTY;
  auto *vm_ctx = static_cast<native_powervr_vm_context *>(handleLookup(vmCtxs_, args->vm_context_handle));
  if (!vm_ctx) return -EINVAL;
  native_powervr_free_list *free_list = nullptr;
  int err = native_powervr_free_list_create(jobs_, vm_ctx, args, &free_list);
  if (err) return err;
  uint32_t handle = 0;
  err = handleInsert(freeLists_, free_list, &handle);
  if (err) { native_powervr_free_list_put(free_list); return err; }
  args->handle = handle;
  return 0;
}

// pvr_ioctl_destroy_free_list()
int NativePowerVRUserClient::destroyFreeList(const drm_pvr_ioctl_destroy_free_list_args *args)
{
  if (args->_padding_4) return -EINVAL;
  if (!native_powervr_free_list_put) return -ENOTTY;
  auto *free_list = static_cast<native_powervr_free_list *>(handleErase(freeLists_, args->handle));
  if (!free_list) return -EINVAL;
  native_powervr_free_list_put(free_list);
  return 0;
}

// pvr_ioctl_create_hwrt_dataset() -> pvr_hwrt_dataset_create(); hwrt_init_kernel_structure() looks the free lists up.
int NativePowerVRUserClient::createHwrtDataset(drm_pvr_ioctl_create_hwrt_dataset_args *args)
{
  if (!jobs_ || !native_powervr_hwrt_dataset_create || !native_powervr_hwrt_dataset_put) return -ENOTTY;
  native_powervr_free_list *free_lists[ARRAY_SIZE(args->free_list_handles)];
  static_assert(ARRAY_SIZE(args->free_list_handles) == 2, "free_list_handles");
  for (uint32_t i = 0; i < ARRAY_SIZE(args->free_list_handles); i++) {
    free_lists[i] = static_cast<native_powervr_free_list *>(handleLookup(freeLists_, args->free_list_handles[i]));
    if (!free_lists[i]) return -EINVAL;
  }
  native_powervr_hwrt_dataset *hwrt = nullptr;
  int err = native_powervr_hwrt_dataset_create(jobs_, args, free_lists, &hwrt);
  if (err) return err;
  uint32_t handle = 0;
  err = handleInsert(hwrts_, hwrt, &handle);
  if (err) { native_powervr_hwrt_dataset_put(hwrt); return err; }
  args->handle = handle;
  return 0;
}

// pvr_ioctl_destroy_hwrt_dataset()
int NativePowerVRUserClient::destroyHwrtDataset(const drm_pvr_ioctl_destroy_hwrt_dataset_args *args)
{
  if (args->_padding_4) return -EINVAL;
  if (!native_powervr_hwrt_dataset_put) return -ENOTTY;
  auto *hwrt = static_cast<native_powervr_hwrt_dataset *>(handleErase(hwrts_, args->handle));
  if (!hwrt) return -EINVAL;
  native_powervr_hwrt_dataset_put(hwrt);
  return 0;
}

// pvr_ioctl_submit_jobs() -> pvr_submit_jobs(), the parts before the job layer: the job array (PVR_UOBJ_GET_ARRAY()), the
// ABI rules of version 1, create_job()'s first checks and lookups, and the command streams.
int NativePowerVRUserClient::submitJobs(const drm_pvr_ioctl_submit_jobs_args *args, NativePowerVRAbiSubmitReply *reply)
{
  if (!jobs_ || !native_powervr_submit_jobs) return -ENOTTY;
  if (!args->jobs.count || args->jobs.count > NativePowerVRAbiMaxJobs || args->jobs.stride != sizeof(drm_pvr_job)) return -EINVAL;
  const uint32_t count = args->jobs.count;
  drm_pvr_job job_args[NativePowerVRAbiMaxJobs];
  native_powervr_job_submit jobs[NativePowerVRAbiMaxJobs] = {};
  void *streams[NativePowerVRAbiMaxJobs] = {};
  int err = copy_from_user(job_args, args->jobs.array, sizeof(drm_pvr_job) * count);
  for (uint32_t i = 0; !err && i < count; i++) {
    const drm_pvr_job &job = job_args[i];
    if (job.sync_ops.count) { err = -EINVAL; break; }   // no kernel sync objects in ABI version 1
    if (!job.cmd_stream || !job.cmd_stream_len) { err = -EINVAL; break; }
    if (job.type != DRM_PVR_JOB_TYPE_GEOMETRY && job.type != DRM_PVR_JOB_TYPE_FRAGMENT && (job.hwrt.set_handle || job.hwrt.data_index)) {
      err = -EINVAL;
      break;
    }
    jobs[i].args = &job_args[i];
    jobs[i].ctx = static_cast<native_powervr_context *>(handleLookup(ctxs_, job.context_handle));
    if (!jobs[i].ctx) { err = -EINVAL; break; }
    if (job.hwrt.set_handle) {
      jobs[i].hwrt = static_cast<native_powervr_hwrt_dataset *>(handleLookup(hwrts_, job.hwrt.set_handle));
      if (!jobs[i].hwrt) { err = -EINVAL; break; }
    }
    err = dupUserStream(job.cmd_stream, job.cmd_stream_len, &streams[i]);
    jobs[i].cmd_stream = streams[i];
  }
  uint64_t fences[NativePowerVRAbiMaxJobs] = {};
  if (!err) err = native_powervr_submit_jobs(jobs_, jobs, count, fences);
  for (uint32_t i = 0; i < count; i++) kfreeBytes(streams[i], job_args[i].cmd_stream_len);
  if (err) return err;
  for (uint32_t i = 0; i < count; i++) reply->fence[i] = fences[i];
  return 0;
}

// Publication (NativeAdrenoPublish.cpp native_adreno_publish()): once, after the firmware runs (NativePowerVR.cpp has
// created the kernel VM context); thread context.
extern "C" int native_powervr_publish(void)
{
  if (gService) return 0;
  if (!native_powervr_kernel_fw_mem_ctx()) return -ENODEV;
  int err = loadDeviceInfo();
  if (err) {
    IOLog("powervr: device information of the firmware image unusable (%d); service not published\n", err);
    return err;
  }
  IOLog("powervr: device info: gpu_id %llx, phantoms %u, quirks 47217:%d 48545:%d 49927:%d 51764:%d 62269:%d 63142:%d, "
        "enhancements 35421:%d 42064:%d, VA bits %llu, free list min pages %u, max coeffs %u, CDM local mem %u%s\n",
        static_cast<unsigned long long>(gGpuId), rogue_get_num_phantoms(&gDev), gDev.quirks.has_brn47217, gDev.quirks.has_brn48545,
        gDev.quirks.has_brn49927, gDev.quirks.has_brn51764, gDev.quirks.has_brn62269, gDev.quirks.has_brn63142,
        gDev.enhancements.has_ern35421, gDev.enhancements.has_ern42064, static_cast<unsigned long long>(gDeviceAddrBits),
        get_free_list_min_pages(&gDev), rogue_get_max_coeffs(&gDev), rogue_get_cdm_max_local_mem_size_regs(&gDev),
        native_powervr_submit_jobs ? "" : "; no job layer linked");
  NativePowerVRGpu *service = new NativePowerVRGpu;
  if (!service || !service->init() || !service->attach(IOService::getPlatform())) {
    if (service) service->release();
    return -ENOMEM;
  }
  service->setProperty("IOUserClientClass", "NativePowerVRUserClient");
  gService = service;
  service->registerService();
  IOLog("powervr: published %s (ABI %u)\n", NativePowerVRServiceName, NativePowerVRAbiVersion);
  return 0;
}
