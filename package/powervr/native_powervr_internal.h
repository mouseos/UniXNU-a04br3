/* SPDX-License-Identifier: MIT */
/* Interfaces between the NativePowerVR parts (NativePowerVR.cpp: firmware boot, FW heap, KCCB; the Linux pvr_mmu.c port;
 * NativePowerVRQueue.cpp: contexts, CCCB, job kicks). All of them run after native_powervr_boot() has started the firmware. */
#ifndef NATIVE_POWERVR_INTERNAL_H
#define NATIVE_POWERVR_INTERNAL_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
struct rogue_fwif_kccb_cmd;
/* An object in the 16 MiB firmware heap (Linux pvr_fw_object): CPU address (cached, kernel), firmware (MIPS) address,
 * physical address. The heap is zeroed at boot. */
struct native_powervr_fw_obj { void *cpu; uint32_t fw_addr; uint64_t phys; uint32_t size; };
int native_powervr_fw_alloc(uint32_t size, uint32_t align, struct native_powervr_fw_obj *out);   /* 0 or -ENOMEM */
/* CPU cache maintenance for memory the GPU or firmware reads (clean) or writes (clean+invalidate). */
void native_powervr_clean(const void *p, size_t n);
void native_powervr_invalidate(const void *p, size_t n);
/* pvr_kccb_send_cmd() + pvr_kccb_wait_for_completion(): queue one KCCB command, kick the firmware and wait until it has
 * executed it (osdata->kccb_cmds_executed advances). Returns 0, -ETIMEDOUT or -ENODEV (firmware not started). */
int native_powervr_kccb_submit(const struct rogue_fwif_kccb_cmd *cmd, uint32_t timeout_ms);
void native_powervr_fw_free(const struct native_powervr_fw_obj *obj);
/* GPU VM contexts (pvr_vm.c without drm_gpuvm): page tables (Linux pvr_mmu.c) and the firmware memory context. */
struct native_powervr_vm;
struct native_powervr_sg { uint64_t phys; uint64_t size; };   /* physically contiguous runs, multiples of 4 KiB */
struct native_powervr_vm *native_powervr_vm_create(void);
void native_powervr_vm_destroy(struct native_powervr_vm *vm);
uint32_t native_powervr_vm_fw_mem_ctx(struct native_powervr_vm *vm);   /* FW address of its rogue_fwif_fwmemcontext */
uint64_t native_powervr_vm_page_table_root(struct native_powervr_vm *vm);
/* pvr_mmu_map() of [offset, offset + size) of the runs at device_addr; flags are DRM_PVR_BO_* (pvr_drm.h). Flushes the MMU
 * caches through the firmware before returning. */
int native_powervr_vm_map_sg(struct native_powervr_vm *vm, uint64_t device_addr, const struct native_powervr_sg *sg,
                             uint32_t nents, uint64_t offset, uint64_t size, uint64_t flags);
int native_powervr_vm_unmap(struct native_powervr_vm *vm, uint64_t device_addr, uint64_t size);
/* The kernel GPU VM context (pvr_vm_create_context(is_userspace_context = false) equivalent), created after boot. */
uint32_t native_powervr_kernel_fw_mem_ctx(void);   /* FW address of its rogue_fwif_fwmemcontext, 0 if none */
/* Map physically contiguous memory into the kernel VM context at device_addr (pvr_mmu_map()). */
int native_powervr_vm_map(uint64_t device_addr, uint64_t phys, uint64_t size, int uncached);
/* NativePowerVRQueue.cpp: create a compute context in the kernel VM context and run one NULL command with a fence
 * update through it (the pvr_context / pvr_cccb / pvr_queue path). Returns 0 when the firmware wrote the fence. */
int native_powervr_queue_selftest(void);
/* The last last_words words of the firmware trace (thread 0) as "powervr trace" rows, then the firmware state. */
void native_powervr_dump_firmware(const char *when, uint32_t last_words);
/* Log through IOLog with the powervr: prefix convention. */
void native_powervr_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
#ifdef __cplusplus
}
#endif
#endif
