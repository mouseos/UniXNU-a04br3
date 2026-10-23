/* SPDX-License-Identifier: MIT */
/* The part of the Linux pvr_device / FW / CCB / power / GEM / VM / queue interfaces that the unchanged kernel/ sources use
 * (pvr_mmu.c; the job layer pvr_context.c, pvr_cccb.c, pvr_free_list.c, pvr_hwrt.c, pvr_stream.c, pvr_stream_defs.c,
 * pvr_device_info.c, pvr_dump.c), for NativePowerVR. The MMU part is implemented by NativePowerVR.cpp, the job part by
 * NativePowerVRJobs.cpp. Every member and prototype below is the Linux one (pvr_device.h, pvr_fw.h, pvr_gem.h, pvr_vm.h,
 * pvr_ccb.h, pvr_queue.h at torvalds/linux 734bef548a96e8774d6eef5dfa2026c6edc4e80b); the structs hold only the members
 * those sources read. */
#ifndef NATIVE_POWERVR_KERNEL_H
#define NATIVE_POWERVR_KERNEL_H
#include "../compat/native_powervr_linux_compat.h"
#include "pvr_rogue_fwif.h"
#include "pvr_rogue_fwif_stream.h"
#include "../compat/native_powervr_linux_kernel.h"
#include "../compat/native_powervr_linux_jobs.h"
#include <uapi/drm/pvr_drm.h>
#include "../kernel/pvr_device_info.h"
#include "../kernel/pvr_stream.h"
struct pvr_fw_object;
/* include/drm/drm_file.h: what the job layer reads of a DRM file. */
struct drm_file {
	bool is_master;   /* drm_is_current_master() */
	u64 client_id;
};
struct pvr_device {
	atomic_t mmu_flush_cache_flags;
	struct {
		bool initialised;
		struct { struct pvr_fw_object *mmucache_sync_obj; } mem;
	} fw_dev;
	/* Read from the firmware image's device info (pvr_fw_get_device_info()); use PVR_HAS_FEATURE() and friends. */
	struct pvr_device_features features;
	struct pvr_device_quirks quirks;
	struct pvr_device_enhancements enhancements;
	u32 stream_musthave_quirks[PVR_STREAM_TYPE_MAX][PVR_STREAM_EXTHDR_TYPE_MAX];
	struct xarray ctx_ids;
	struct xarray free_list_ids;
	struct xarray job_ids;
	spinlock_t ctx_list_lock;
};
/* struct pvr_file (pvr_device.h) */
struct pvr_file {
	struct drm_file *file;
	struct pvr_device *pvr_dev;
	struct xarray ctx_handles;
	struct xarray free_list_handles;
	struct xarray hwrt_handles;
	struct xarray vm_ctx_handles;
	struct list_head contexts;
};
#define from_pvr_file(pvr_file) ((pvr_file)->file)
#define PVR_HAS_FEATURE(pvr_dev, feature) ((pvr_dev)->features.has_##feature)
#define PVR_FEATURE_VALUE(pvr_dev, feature, value_out)             \
	({                                                         \
		struct pvr_device *_pvr_dev = pvr_dev;             \
		int _ret = -EINVAL;                                \
		if (_pvr_dev->features.has_##feature) {            \
			*(value_out) = _pvr_dev->features.feature; \
			_ret = 0;                                  \
		}                                                  \
		_ret;                                              \
	})
#define PVR_HAS_QUIRK(pvr_dev, quirk) ((pvr_dev)->quirks.has_brn##quirk)
#define PVR_HAS_ENHANCEMENT(pvr_dev, enhancement) ((pvr_dev)->enhancements.has_ern##enhancement)
/* pvr_gem.h */
#define PVR_BO_FW_FLAGS_DEVICE_CACHED (0ULL)
#define PVR_BO_FW_FLAGS_DEVICE_UNCACHED DRM_PVR_BO_BYPASS_DEVICE_CACHE
struct drm_gem_shmem_object { struct sg_table *sgt; };
struct pvr_gem_object {
	struct drm_gem_shmem_object base;
	u64 flags;
};
struct pvr_vm_context;
struct pvr_context;
struct pvr_queue;
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
/* pvr_device.c */
bool pvr_device_has_uapi_quirk(struct pvr_device *pvr_dev, u32 quirk);
bool pvr_device_has_uapi_enhancement(struct pvr_device *pvr_dev, u32 enhancement);
bool pvr_device_has_feature(struct pvr_device *pvr_dev, u32 feature);
static inline bool drm_is_current_master(struct drm_file *file) { return file && file->is_master; }
/* pvr_ccb.h */
void pvr_kccb_send_cmd_reserved_powered(struct pvr_device *pvr_dev, struct rogue_fwif_kccb_cmd *cmd, u32 *kccb_slot);
void pvr_fwccb_process(struct pvr_device *pvr_dev);
/* pvr_fw.h (pvr_fw_object_vmap(), _vunmap() and _unmap_and_destroy() are inline there) */
int pvr_fw_structure_cleanup(struct pvr_device *pvr_dev, u32 type, struct pvr_fw_object *fw_obj, u32 offset);
int pvr_fw_object_create(struct pvr_device *pvr_dev, size_t size, u64 flags, void (*init)(void *cpu_ptr, void *priv),
			 void *init_priv, struct pvr_fw_object **pvr_obj_out);
void *pvr_fw_object_create_and_map(struct pvr_device *pvr_dev, size_t size, u64 flags,
				   void (*init)(void *cpu_ptr, void *priv), void *init_priv,
				   struct pvr_fw_object **pvr_obj_out);
void *pvr_fw_object_vmap(struct pvr_fw_object *fw_obj);
void pvr_fw_object_vunmap(struct pvr_fw_object *fw_obj);
void pvr_fw_object_destroy(struct pvr_fw_object *fw_obj);
void pvr_fw_object_unmap_and_destroy(struct pvr_fw_object *fw_obj);
void pvr_fw_object_get_fw_addr_offset(struct pvr_fw_object *fw_obj, u32 offset, u32 *fw_addr_out);
/* pvr_gem.h (pvr_gem_object_put() is inline there) and drm_gem_shmem_helper.h */
struct pvr_gem_object *pvr_gem_object_create(struct pvr_device *pvr_dev, size_t size, u64 flags);
void pvr_gem_object_put(struct pvr_gem_object *pvr_obj);
void *pvr_gem_object_vmap(struct pvr_gem_object *pvr_obj);
void pvr_gem_object_vunmap(struct pvr_gem_object *pvr_obj);
struct sg_table *drm_gem_shmem_get_pages_sgt(struct drm_gem_shmem_object *shmem);
/* pvr_vm.h */
struct pvr_vm_context *pvr_vm_context_lookup(struct pvr_file *pvr_file, u32 handle);
bool pvr_vm_context_put(struct pvr_vm_context *vm_ctx);
struct pvr_gem_object *pvr_vm_find_gem_object(struct pvr_vm_context *vm_ctx, u64 device_addr, u64 *mapped_offset_out,
					      u64 *mapped_size_out);
void pvr_vm_unmap_all(struct pvr_vm_context *vm_ctx);
/* pvr_queue.h; the queues are NativePowerVRJobs.cpp's (drm_sched replaced by the firmware's UFO waits). */
struct pvr_queue *pvr_queue_create(struct pvr_context *ctx, enum drm_pvr_job_type type,
				   struct drm_pvr_ioctl_create_context_args *args, void *fw_ctx_map);
void pvr_queue_kill(struct pvr_queue *queue);
void pvr_queue_destroy(struct pvr_queue *queue, bool cleanup_queue_entity);
#ifdef __cplusplus
}
#endif
#endif
