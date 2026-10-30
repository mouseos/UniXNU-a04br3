/* SPDX-License-Identifier: MIT */
/* Interface between the NativePowerVR user-client layer and the job layer.
 *
 *  - User-client layer (NativePowerVRGpu.cpp): the IOKit service and user client (NativePowerVRABI.h), the per-connection
 *    handle tables (Linux pvr_file: ctx_handles, free_list_handles, hwrt_handles, vm_ctx_handles and the GEM handles), the
 *    buffer objects (pvr_gem.c), the VM contexts and their mappings (pvr_vm.c without drm_gpuvm), the device queries
 *    (pvr_drv.c) and the device information of the firmware image (pvr_fw_get_device_info() + pvr_device_info.c).
 *  - Job layer: contexts, free lists, HWRT datasets, job submission and completion (pvr_context.c, pvr_free_list.c,
 *    pvr_hwrt.c, pvr_job.c, pvr_queue.c without drm_sched, dma_fence and xarray).
 *
 * The user-client layer resolves every handle of the uapi structures (pvr_drm.h) to the objects below before it calls the
 * job layer, and copies every user pointer the job layer needs into kernel memory (the static context state and the
 * command streams); the uapi structures are passed unchanged otherwise. Pointers inside them still hold user addresses
 * and must not be followed. All functions run in thread context and may sleep. The user-client layer serialises the calls
 * of one connection, except native_powervr_fence_wait(), which may run concurrently with any other call of the same
 * connection (but never with or after native_powervr_jobs_file_destroy()).
 *
 * Errors are negative Linux errno values (EINVAL, ENOMEM, ...), as in the Linux functions named next to each declaration.
 *
 * The job-layer functions are referenced weakly by the user-client layer (NATIVE_POWERVR_JOBS_API): while the job layer is
 * not linked, the job methods of the ABI answer kIOReturnUnsupported. */
#ifndef NATIVE_POWERVR_JOBS_H
#define NATIVE_POWERVR_JOBS_H
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "native_powervr_pte.h"
#ifndef NATIVE_POWERVR_JOBS_API
#define NATIVE_POWERVR_JOBS_API
#endif
#ifdef __cplusplus
extern "C" {
#endif
struct drm_pvr_ioctl_create_context_args;
struct drm_pvr_ioctl_create_free_list_args;
struct drm_pvr_ioctl_create_hwrt_dataset_args;
struct drm_pvr_job;
struct native_powervr_vm;   /* native_powervr_internal.h */
struct native_powervr_sg;   /* native_powervr_internal.h */
struct pvr_device_features;      /* Linux pvr_device_info.h */
struct pvr_device_quirks;
struct pvr_device_enhancements;

/* ---- Provided by the user-client layer ---- */

/* Device information, loaded once from the embedded firmware image before the service is published: Linux
 * pvr_fw_get_device_info() with the unchanged pvr_device_info.c (package/powervr/kernel, compiled once, through
 * stub/devinfo). A job-layer struct pvr_device stand-in copies these three members. Null before publication. */
const struct pvr_device_features *native_powervr_device_features(void);
const struct pvr_device_quirks *native_powervr_device_quirks(void);
const struct pvr_device_enhancements *native_powervr_device_enhancements(void);

/* VM contexts (struct pvr_vm_context), reference counted. The handle table holds one reference; a job-layer object
 * that keeps the VM context (a context, as pvr_context.c does with ctx->vm_ctx) takes its own. */
struct native_powervr_vm_context;
/* Freeze mappings and retain the VM until reboot after an ambiguous KICK. */
void native_powervr_vm_quarantine(struct native_powervr_vm_context *vm_ctx);
bool native_powervr_vm_quarantined(struct native_powervr_vm_context *vm_ctx);
struct native_powervr_vm_context *native_powervr_vm_context_get(struct native_powervr_vm_context *vm_ctx);   /* pvr_vm_context_get() */
void native_powervr_vm_context_put(struct native_powervr_vm_context *vm_ctx);                                 /* pvr_vm_context_put() */
/* The page tables and firmware memory context: native_powervr_vm_fw_mem_ctx(), native_powervr_vm_page_table_root(). */
struct native_powervr_vm *native_powervr_vm_context_vm(struct native_powervr_vm_context *vm_ctx);

/* Buffer objects (struct pvr_gem_object), reference counted. */
struct native_powervr_bo;
struct native_powervr_bo *native_powervr_bo_get(struct native_powervr_bo *bo);   /* pvr_gem_object_get() */
void native_powervr_bo_put(struct native_powervr_bo *bo);                        /* pvr_gem_object_put() */
uint64_t native_powervr_bo_size(const struct native_powervr_bo *bo);             /* pvr_gem_object_size() */
uint64_t native_powervr_bo_flags(const struct native_powervr_bo *bo);            /* pvr_obj->flags: DRM_PVR_BO_* */
/* The kernel's CPU view of the whole BO (pvr_gem_object_vmap()). Unlike Linux's write-combined vmap it is CACHED: clean
 * (native_powervr_clean()) after writing what the GPU or firmware reads, clean+invalidate (native_powervr_invalidate())
 * before reading what they wrote. User space maps the same pages write-combined. */
void *native_powervr_bo_cpu(const struct native_powervr_bo *bo);
/* The physical runs of the whole BO (pvr_gem_object_get_pages_sgt()); valid while a reference is held. */
const struct native_powervr_sg *native_powervr_bo_sg(const struct native_powervr_bo *bo, uint32_t *nents);

/* pvr_vm_find_gem_object(): the mapping of vm_ctx that contains device_addr. On success out->bo carries a new reference
 * (native_powervr_bo_put()). Returns 0 or -ENOENT. */
struct native_powervr_bo_mapping {
    struct native_powervr_bo *bo;
    uint64_t va_addr;     /* drm_gpuva.va.addr: start of the mapping */
    uint64_t va_range;    /* drm_gpuva.va.range: its size (*mapped_size_out) */
    uint64_t bo_offset;   /* drm_gpuva.gem.offset: offset of va_addr in the BO (*mapped_offset_out) */
};
int native_powervr_vm_find_bo(struct native_powervr_vm_context *vm_ctx, uint64_t device_addr,
                              struct native_powervr_bo_mapping *out);

/* Retained snapshot of all host imports in a VM. Sync before kicking GPU work and once after actual GPU completion,
 * before publishing fence success. A timeout is not quiescence: keep the snapshot until the GPU really retires. */
struct native_powervr_host_sync;
int native_powervr_vm_hosts_snapshot(struct native_powervr_vm_context *vm, struct native_powervr_host_sync **out);
int native_powervr_hosts_sync(struct native_powervr_host_sync *hosts);
void native_powervr_hosts_put(struct native_powervr_host_sync *hosts);

/* ---- Provided by the job layer ---- */

/* Per-connection job state (the job part of struct pvr_file): the fence numbering. Created when a connection opens,
 * destroyed after all of its contexts, HWRT datasets and free lists. */
struct native_powervr_jobs_file;
NATIVE_POWERVR_JOBS_API struct native_powervr_jobs_file *native_powervr_jobs_file_create(void);
NATIVE_POWERVR_JOBS_API void native_powervr_jobs_file_destroy(struct native_powervr_jobs_file *file);

/* pvr_context_create(). vm_ctx is the resolved args->vm_context_handle (borrowed for the call). static_context_state is
 * the kernel copy of args->static_context_state (args->static_context_state_len bytes, null when the length is 0). On
 * success *out is the context; the handle (args->handle) is assigned by the caller afterwards. */
struct native_powervr_context;
NATIVE_POWERVR_JOBS_API int native_powervr_context_create(struct native_powervr_jobs_file *file,
                                                          struct native_powervr_vm_context *vm_ctx,
                                                          const struct drm_pvr_ioctl_create_context_args *args,
                                                          const void *static_context_state,
                                                          struct native_powervr_context **out);
/* pvr_context_destroy() after its xa_erase(): kill the queues and drop the handle's reference. Jobs still queued keep
 * what they need alive themselves. */
NATIVE_POWERVR_JOBS_API void native_powervr_context_destroy(struct native_powervr_context *ctx);

/* pvr_free_list_create(). vm_ctx is the resolved args->vm_context_handle (borrowed for the call). */
struct native_powervr_free_list;
NATIVE_POWERVR_JOBS_API int native_powervr_free_list_create(struct native_powervr_jobs_file *file,
                                                            struct native_powervr_vm_context *vm_ctx,
                                                            const struct drm_pvr_ioctl_create_free_list_args *args,
                                                            struct native_powervr_free_list **out);
NATIVE_POWERVR_JOBS_API void native_powervr_free_list_put(struct native_powervr_free_list *free_list);   /* pvr_free_list_put() */

/* pvr_hwrt_dataset_create(). free_lists[i] is the resolved args->free_list_handles[i] (borrowed for the call; a dataset
 * that keeps a free list takes its own reference, as hwrt_init_kernel_structure() does). */
struct native_powervr_hwrt_dataset;
NATIVE_POWERVR_JOBS_API int native_powervr_hwrt_dataset_create(struct native_powervr_jobs_file *file,
                                                               const struct drm_pvr_ioctl_create_hwrt_dataset_args *args,
                                                               struct native_powervr_free_list *const free_lists[2],
                                                               struct native_powervr_hwrt_dataset **out);
NATIVE_POWERVR_JOBS_API void native_powervr_hwrt_dataset_put(struct native_powervr_hwrt_dataset *hwrt);   /* pvr_hwrt_dataset_put() */

/* pvr_submit_jobs() for one ABI call (NativePowerVRABI.h SubmitJobs). The user-client layer has checked the ABI rules
 * (1 <= count <= NativePowerVRAbiMaxJobs, stride == sizeof(struct drm_pvr_job), sync_ops.count == 0) and create_job()'s
 * first two checks (a command stream is given; no hwrt on a job that is neither geometry nor fragment), and resolved
 * the handles (pvr_context_lookup(); for hwrt.set_handle != 0 the dataset of pvr_hwrt_data_lookup(), whose data_index
 * the job layer checks). The objects are borrowed for the call: a queued job takes its own references.
 * A KICK failure may follow partial submission; it returns an error, quarantines affected jobs and VM mappings until reboot,
 * and publishes no fences. On success fences[i] is the
 * fence of jobs[i] (nonzero, unique in the connection, increasing in submission order, and never a value whose low 32
 * bits are NativePowerVRTunnelMagic, so that no direct WaitFence input reads as a tunnel header). */
struct native_powervr_job_submit {
    const struct drm_pvr_job *args;                 /* kernel copy of the uapi job */
    struct native_powervr_context *ctx;             /* args->context_handle */
    struct native_powervr_hwrt_dataset *hwrt;       /* args->hwrt.set_handle, null when 0 */
    const void *cmd_stream;                         /* kernel copy, args->cmd_stream_len bytes */
};
/* Optional PTE pre-phase runs after jobs retain their objects and before
 * gJobs.lock. The callback releases/reacquires the UserClient connection lock
 * for pmap walks, returns with it held, and rejects a closing connection.
 * Borrowed handle wrappers are not accessed after the callback. */
NATIVE_POWERVR_JOBS_API int native_powervr_submit_jobs(struct native_powervr_jobs_file *file,
                                                       const struct native_powervr_job_submit *jobs, uint32_t count,
                                                       uint64_t *fences, native_powervr_pte_phase pte_phase, void *pte_cookie);
/* NativePowerVRMethodWaitFence: 0 once the job of fence has completed (also for an earlier fence no longer tracked),
 * -ETIMEDOUT when it has not within timeout_ns (0 polls once), -EINVAL for a fence never issued on this file, any other
 * negative value when the job failed or its context was lost. */
NATIVE_POWERVR_JOBS_API int native_powervr_fence_wait(struct native_powervr_jobs_file *file, uint64_t fence,
                                                      uint64_t timeout_ns);
#ifdef __cplusplus
}
#endif
#endif
