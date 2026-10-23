/* SPDX-License-Identifier: MIT */
/* The part of the Linux pvr_device / FW / CCB / power interfaces that the unchanged pvr_mmu.c uses, for NativePowerVR.
 * The functions are implemented by NativePowerVR.cpp. */
#ifndef NATIVE_POWERVR_KERNEL_H
#define NATIVE_POWERVR_KERNEL_H
#include "../compat/native_powervr_linux_compat.h"
#include "pvr_rogue_fwif.h"
#include "../compat/native_powervr_linux_kernel.h"
/* include/uapi/drm/pvr_drm.h */
#define DRM_PVR_BO_BYPASS_DEVICE_CACHE (1ULL << 0)
#define DRM_PVR_BO_PM_FW_PROTECT (1ULL << 1)
struct pvr_fw_object;
struct pvr_device {
	atomic_t mmu_flush_cache_flags;
	struct {
		bool initialised;
		struct { struct pvr_fw_object *mmucache_sync_obj; } mem;
	} fw_dev;
};
#ifdef __cplusplus
extern "C" {
#endif
int pvr_kccb_send_cmd(struct pvr_device *pvr_dev, struct rogue_fwif_kccb_cmd *cmd, u32 *kccb_slot);
int pvr_kccb_wait_for_completion(struct pvr_device *pvr_dev, u32 slot_nr, u32 timeout, u32 *rtn_out);
void pvr_fw_object_get_fw_addr(struct pvr_fw_object *fw_obj, u32 *fw_addr_out);
void pvr_device_lost(struct pvr_device *pvr_dev);
int pvr_power_reset(struct pvr_device *pvr_dev, bool hard_reset);
struct drm_device *native_powervr_drm(struct pvr_device *pvr_dev);
#define from_pvr_device(p) native_powervr_drm(p)
#ifdef __cplusplus
}
#endif
#endif
