/* SPDX-License-Identifier: MIT */
#ifndef NATIVE_POWERVR_ABI_H
#define NATIVE_POWERVR_ABI_H
#include <stdint.h>

/* Kernel/user-client ABI of the native PowerVR Rogue driver (NativePowerVR), version 1.
 *
 * The argument records are the structs of the Linux PowerVR uAPI, include/uapi/drm/pvr_drm.h, unchanged (Linux
 * torvalds/linux 734bef548a96e8774d6eef5dfa2026c6edc4e80b; Mesa 0d94b3f397fb carries the identical file as
 * include/drm-uapi/pvr_drm.h). The includer includes that header first; this header adds only what Darwin needs on top:
 * the external-method selectors, the submission reply and the fence wait of the Turnip Darwin KMD (NativeAdrenoABI.h).
 *
 * Transport: IOConnectCallStructMethod(connection, selector, input, input_size, output, &output_size).
 *  - A DRM_IOWR ioctl is an in/out method: input and output are the same struct, sizes exactly sizeof(struct).
 *  - A DRM_IOW ioctl is an input-only method: output_size 0.
 *  - Pointers inside the structs (drm_pvr_ioctl_dev_query_args.pointer, drm_pvr_obj_array.array,
 *    drm_pvr_job.cmd_stream, drm_pvr_ioctl_create_context_args.static_context_state) are addresses in the calling
 *    task; the kernel copies them in and out as Linux does with copy_from_user()/copy_to_user().
 *  - The status is a kern_return_t. Mapping used by the user side: kIOReturnBadArgument = EINVAL,
 *    kIOReturnNoMemory/kIOReturnNoResources = ENOMEM, kIOReturnUnsupported = ENOTTY, kIOReturnNotPermitted = EPERM,
 *    kIOReturnBusy = EBUSY, kIOReturnTimeout = ETIMEDOUT, anything else = EIO.
 *  - Unknown selectors, flags and struct sizes are refused, never ignored.
 *
 * Both participants are ARM64 little endian. One connection is one Linux DRM file: handles (BO, VM context, context,
 * free list, HWRT dataset) and fences are per connection and die with it. */

#ifndef PVR_DRM_UAPI_H
#error "include the Linux PowerVR uAPI (pvr_drm.h) before NativePowerVRABI.h"
#endif

/* IOService class of the direct (unsandboxed) user client, IOServiceOpen type 0. */
#define NativePowerVRServiceName "NativePowerVRGpu"

enum {
    NativePowerVRAbiVersion = 1,

    /* Selector = DRM_PVR ioctl number (the "nr" of DRM_IOCTL_PVR_*, minus DRM_COMMAND_BASE). */
    NativePowerVRMethodDevQuery = 0x00,          /* in/out drm_pvr_ioctl_dev_query_args */
    NativePowerVRMethodCreateBo = 0x01,          /* in/out drm_pvr_ioctl_create_bo_args */
    NativePowerVRMethodGetBoMmapOffset = 0x02,   /* in/out drm_pvr_ioctl_get_bo_mmap_offset_args, see below */
    NativePowerVRMethodCreateVmContext = 0x03,   /* in/out drm_pvr_ioctl_create_vm_context_args */
    NativePowerVRMethodDestroyVmContext = 0x04,  /* in drm_pvr_ioctl_destroy_vm_context_args */
    NativePowerVRMethodVmMap = 0x05,             /* in drm_pvr_ioctl_vm_map_args */
    NativePowerVRMethodVmUnmap = 0x06,           /* in drm_pvr_ioctl_vm_unmap_args */
    NativePowerVRMethodCreateContext = 0x07,     /* in/out drm_pvr_ioctl_create_context_args */
    NativePowerVRMethodDestroyContext = 0x08,    /* in drm_pvr_ioctl_destroy_context_args */
    NativePowerVRMethodCreateFreeList = 0x09,    /* in/out drm_pvr_ioctl_create_free_list_args */
    NativePowerVRMethodDestroyFreeList = 0x0a,   /* in drm_pvr_ioctl_destroy_free_list_args */
    NativePowerVRMethodCreateHwrtDataset = 0x0b, /* in/out drm_pvr_ioctl_create_hwrt_dataset_args */
    NativePowerVRMethodDestroyHwrtDataset = 0x0c,/* in drm_pvr_ioctl_destroy_hwrt_dataset_args */
    NativePowerVRMethodSubmitJobs = 0x0d,        /* in drm_pvr_ioctl_submit_jobs_args, out NativePowerVRAbiSubmitReply */

    /* The DRM core ioctl the Linux winsys uses to free a BO (pvr_drm_bo.c: DRM_IOCTL_GEM_CLOSE; DRM_PVR has no
     * DESTROY_BO). */
    NativePowerVRMethodGemClose = 0x40,          /* in struct drm_gem_close (drm.h) */
    /* Synchronisation, the fence model of the Turnip Darwin KMD (NativeAdrenoMethodWaitFence). */
    NativePowerVRMethodWaitFence = 0x41,         /* in NativePowerVRAbiWaitFence */
    /* Version handshake, the first call on a connection (NativeAdrenoMethodQuery). */
    NativePowerVRMethodGetParams = 0x42,         /* out NativePowerVRAbiParams */
    /* Optional (VK_EXT_external_memory_host): a BO over pages of the calling task, as the Turnip Darwin KMD's
     * NativeAdrenoMethodBoImport. A kernel without it answers kIOReturnUnsupported (unknown method); one with it answers
     * kIOReturnBadArgument to a call without input, which is how the user side probes for it (ABI version stays 1). */
    NativePowerVRMethodImportHostMemory = 0x43,  /* in/out NativePowerVRAbiImportHost */

    NativePowerVRAbiMaxJobs = 8                  /* jobs per SubmitJobs call */
};

/* GetBoMmapOffset is the Darwin form of the BO's user mapping. Linux returns a fake mmap() offset of the DRM file;
 * here drm_pvr_ioctl_get_bo_mmap_offset_args.offset returns the memory type for
 * IOConnectMapMemory64(connection, (uint32_t)offset, mach_task_self(), &address, &size, kIOMapAnywhere), served by
 * the user client's clientMemoryForType(). The value fits in 32 bits (upper half zero), stays valid while the BO
 * exists, and the mapping always covers the whole BO; IOConnectUnmapMemory64 with the same type undoes it. As on
 * Linux the BO needs DRM_PVR_BO_ALLOW_CPU_USERSPACE_ACCESS, and the kernel picks the CPU cache mode (Linux maps
 * write-combined). Mapping at a caller-chosen address is not offered. */

/* SubmitJobs.
 *  - jobs.stride == sizeof(struct drm_pvr_job), 1 <= jobs.count <= NativePowerVRAbiMaxJobs.
 *  - Version 1 has no kernel sync objects: every job's sync_ops.count is 0. The user side waits for its dependencies
 *    on the CPU (WaitFence) before submitting, as the Turnip Darwin KMD does.
 *  - Ordering inside one call: a DRM_PVR_JOB_TYPE_FRAGMENT job (partial render or not) whose hwrt (set_handle,
 *    data_index) equals that of an earlier DRM_PVR_JOB_TYPE_GEOMETRY job of the same call runs after that geometry
 *    job. Linux gets the same order from the geometry -> partial render syncobj that Mesa attaches
 *    (pvr_drm_job_render.c) and pairs the two jobs (pvr_job.c can_combine_jobs()). Jobs of one type on one context
 *    run in submission order, as on Linux.
 *  - All jobs are queued or none is; on failure no fence is issued.
 *  - Reply: fence[i] identifies jobs.array[i] for i < jobs.count, the rest is zero. */
struct NativePowerVRAbiSubmitReply {
    uint64_t fence[NativePowerVRAbiMaxJobs];
};

/* Fences: nonzero, unique per connection, increasing in submission order. Jobs run on separate firmware queues
 * (geometry, fragment, compute, transfer), so a completed fence says nothing about any other fence.
 * WaitFence returns kIOReturnSuccess once job "fence" has completed (also for any earlier fence the kernel no longer
 * tracks), kIOReturnTimeout when it has not completed within timeout_ns (0 polls once), kIOReturnBadArgument for a
 * fence this connection never issued, and any other status when the job failed or the context was lost (the user
 * side reports VK_ERROR_DEVICE_LOST). */
struct NativePowerVRAbiWaitFence {
    uint64_t fence;
    uint64_t timeout_ns;
};

struct NativePowerVRAbiParams {
    uint32_t abi_version;                        /* NativePowerVRAbiVersion */
    uint32_t max_jobs;                           /* NativePowerVRAbiMaxJobs */
};

/* ImportHostMemory. address and size are 4 KiB aligned and nonzero; the range belongs to the calling task (an IOSurface's
 * pages, a Metal no-copy buffer, a mapping of the scanout). The kernel pins it (IOMemoryDescriptor::withAddressRange() and
 * prepare() for writing; read-only backing is refused) and returns a BO handle that VmMap, VmUnmap and GemClose take like any
 * other; the pages stay pinned until the handle, every GPU mapping and every submitted job using the import are gone.
 * The BO has no CPU mapping of its own: the owner keeps using its own mapping. The kernel cleans and invalidates imported
 * pages before job submission and after actual GPU completion, before fence success. CPU/GPU access must still be ordered
 * by the caller. flags: only DRM_PVR_BO_BYPASS_DEVICE_CACHE is accepted. Every
 * physical page must lie below 4 GiB (the GPU's 32-bit bus); otherwise kIOReturnUnsupported. */
struct NativePowerVRAbiImportHost {
    uint64_t address;
    uint64_t size;
    uint64_t flags;
    uint32_t handle;                             /* out */
    uint32_t read_only;                          /* reserved out: always 0; read-only backing is refused */
};

/* Sandboxed callers reach the driver through one external-method selector admitted by their sandbox profile, as with
 * NativeAdreno (NativeAdrenoPublish.cpp): the input is {NativePowerVRTunnelMagic, selector} followed by the method's
 * input, the output is the method's output. Method NativePowerVRTunnelProbe with no payload succeeds on every
 * selector that carries the tunnel. */
enum {
    NativePowerVRTunnelMagic = 0x4e544e4c,       /* 'NTNL', the NativeAdreno tunnel word */
    NativePowerVRTunnelProbe = 0xffffffff
};

/* The Metal plugin's connections (package/powervr/NativePowerVRMetal.cpp: the accelerator AGXAccelerator that the original Metal
 * discovery finds through IOServiceMatching("IOAcceleratorES"), sandbox-granted to the processes it names). Its open types:
 *  - NativePowerVRMetalOpenType: a GPU connection with this ABI, reached through the tunnel (Mesa: PVR_DARWIN_SERVICE=IOAcceleratorES,
 *    PVR_DARWIN_OPEN_TYPE=0x5056);
 *  - NativePowerVRMetalHandoffOpenType: the display hand-off only (UniXNU NativeGOPPresenter.cpp, boot argument sheng-fb-gpu=1):
 *    methods NativePowerVRMetalHandoffBase + NGP_METHOD_MODE/WAIT_SWAP/COMPLETE_SWAP (NativeGOPWire.h) through the tunnel and
 *    memory type NativePowerVRMetalScanoutType, the writable scanout. The same numbers as NativeAdreno's hand-off;
 *  - NativePowerVRMetalMarkBase | value (value 1..0xffff): a progress marker or text character of the plugin, logged on the
 *    console and refused (no connection). 0x7ffe/0x7fff start a text line, 0x7000 | c appends the character c (1..0x7f),
 *    0x7ffd ends the line. */
enum {
    NativePowerVRMetalOpenType = 0x5056,
    NativePowerVRMetalHandoffOpenType = 0x4148,
    NativePowerVRMetalMarkBase = 0x41470000,
    NativePowerVRMetalHandoffBase = 0x7000,
    NativePowerVRMetalHandoffLast = 0x7003,
    NativePowerVRMetalScanoutType = 0x5343
};

#if defined(__cplusplus)
static_assert(sizeof(struct drm_pvr_ioctl_dev_query_args) == 16, "pvr_drm.h dev_query layout");
static_assert(sizeof(struct drm_pvr_ioctl_create_bo_args) == 24, "pvr_drm.h create_bo layout");
static_assert(sizeof(struct drm_pvr_ioctl_get_bo_mmap_offset_args) == 16, "pvr_drm.h get_bo_mmap_offset layout");
static_assert(sizeof(struct drm_pvr_ioctl_create_vm_context_args) == 8, "pvr_drm.h create_vm_context layout");
static_assert(sizeof(struct drm_pvr_ioctl_destroy_vm_context_args) == 8, "pvr_drm.h destroy_vm_context layout");
static_assert(sizeof(struct drm_pvr_ioctl_vm_map_args) == 40, "pvr_drm.h vm_map layout");
static_assert(sizeof(struct drm_pvr_ioctl_vm_unmap_args) == 24, "pvr_drm.h vm_unmap layout");
static_assert(sizeof(struct drm_pvr_ioctl_create_context_args) == 40, "pvr_drm.h create_context layout");
static_assert(sizeof(struct drm_pvr_ioctl_destroy_context_args) == 8, "pvr_drm.h destroy_context layout");
static_assert(sizeof(struct drm_pvr_ioctl_create_free_list_args) == 32, "pvr_drm.h create_free_list layout");
static_assert(sizeof(struct drm_pvr_ioctl_destroy_free_list_args) == 8, "pvr_drm.h destroy_free_list layout");
static_assert(sizeof(struct drm_pvr_ioctl_create_hwrt_dataset_args) == 136, "pvr_drm.h create_hwrt_dataset layout");
static_assert(sizeof(struct drm_pvr_ioctl_destroy_hwrt_dataset_args) == 8, "pvr_drm.h destroy_hwrt_dataset layout");
static_assert(sizeof(struct drm_pvr_ioctl_submit_jobs_args) == 16, "pvr_drm.h submit_jobs layout");
static_assert(sizeof(struct drm_pvr_job) == 48, "pvr_drm.h job layout");
static_assert(sizeof(struct drm_pvr_sync_op) == 16, "pvr_drm.h sync_op layout");
static_assert(sizeof(struct drm_gem_close) == 8, "drm.h gem_close layout");
static_assert(sizeof(NativePowerVRAbiSubmitReply) == 64, "ABI submit reply layout");
static_assert(sizeof(NativePowerVRAbiWaitFence) == 16, "ABI wait layout");
static_assert(sizeof(NativePowerVRAbiParams) == 8, "ABI params layout");
static_assert(sizeof(NativePowerVRAbiImportHost) == 32, "ABI host import layout");
#else
_Static_assert(sizeof(struct drm_pvr_ioctl_submit_jobs_args) == 16, "pvr_drm.h submit_jobs layout");
_Static_assert(sizeof(struct drm_pvr_job) == 48, "pvr_drm.h job layout");
_Static_assert(sizeof(struct NativePowerVRAbiSubmitReply) == 64, "ABI submit reply layout");
_Static_assert(sizeof(struct NativePowerVRAbiWaitFence) == 16, "ABI wait layout");
_Static_assert(sizeof(struct NativePowerVRAbiParams) == 8, "ABI params layout");
_Static_assert(sizeof(struct NativePowerVRAbiImportHost) == 32, "ABI host import layout");
#endif
#endif
