/* SPDX-License-Identifier: MIT */
/* The Linux PowerVR uAPI for the kernel side: pvr_drm.h (linux/uapi/drm, unchanged, torvalds/linux 734bef548a96, "(GPL-2.0-only WITH
 * Linux-syscall-note) OR MIT", used under MIT) with the DRM core uAPI it includes (linux/uapi/drm: drm.h, drm_mode.h of Linux include/uapi/drm,
 * MIT, unchanged as Mesa 0d94b3f397fb include/drm-uapi carries them). The shim's linux/const.h (compat/) has no
 * _BITULL(); its Linux definition (include/uapi/linux/const.h) is given here. */
#ifndef NATIVE_POWERVR_UAPI_H
#define NATIVE_POWERVR_UAPI_H
#ifndef _AC
#define __AC(X, Y) (X##Y)
#define _AC(X, Y) __AC(X, Y)
#endif
#ifndef _ULL
#define _ULL(x) (_AC(x, ULL))
#endif
#ifndef _BITULL
#define _BITULL(x) (_ULL(1) << (x))
#endif
#include "linux/uapi/drm/pvr_drm.h"
#endif
