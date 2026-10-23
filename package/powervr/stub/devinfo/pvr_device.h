/* SPDX-License-Identifier: MIT */
/* The pvr_device of the device information: what the unchanged Linux pvr_device_info.c (../../kernel) writes and what the
 * device queries of NativePowerVRGpu.cpp read. Only pvr_device_info.c (compiled by scripts/build-a04br3-board.py with this
 * directory first on the include path) and NativePowerVRGpu.cpp include it.
 *
 * Linux keeps features, quirks and enhancements in struct pvr_device (pvr_device.h). This struct has those three members
 * only and is a different type from the pvr_device of pvr_mmu.c (../native_powervr_kernel.h), so the name is mapped to
 * native_powervr_device_info here; the functions of pvr_device_info.c keep their Linux names. The macros below are those
 * of Linux pvr_device.h (torvalds/linux 734bef548a96e8774d6eef5dfa2026c6edc4e80b, GPL-2.0-only OR MIT, used under MIT,
 * Copyright Imagination Technologies Ltd.); PVR_FEATURE_VALUE() reads through a const pointer, because the device
 * information is read-only once loaded. */
#ifndef NATIVE_POWERVR_DEVINFO_PVR_DEVICE_H
#define NATIVE_POWERVR_DEVINFO_PVR_DEVICE_H
#include "../../compat/native_powervr_linux_compat.h"
#ifndef __cplusplus
#include "../../compat/native_powervr_linux_kernel.h"   /* drm_warn(), min() and BIT_ULL() for pvr_device_info.c */
#endif
#define pvr_device native_powervr_device_info
#ifdef __cplusplus
extern "C" {
#endif
#include "../../kernel/pvr_device_info.h"
struct pvr_device {
	struct pvr_device_features features;
	struct pvr_device_quirks quirks;
	struct pvr_device_enhancements enhancements;
};
#ifdef __cplusplus
}
#endif
/* pvr_device.h. drm_warn() of the shim does not use its device argument. */
#define from_pvr_device(pvr_dev) (pvr_dev)
#define PVR_HAS_FEATURE(pvr_dev, feature) ((pvr_dev)->features.has_##feature)
#define PVR_FEATURE_VALUE(pvr_dev, feature, value_out)             \
	({                                                         \
		const struct pvr_device *_pvr_dev = pvr_dev;       \
		int _ret = -EINVAL;                                \
		if (_pvr_dev->features.has_##feature) {            \
			*(value_out) = _pvr_dev->features.feature; \
			_ret = 0;                                  \
		}                                                  \
		_ret;                                              \
	})
#define PVR_HAS_QUIRK(pvr_dev, quirk) ((pvr_dev)->quirks.has_brn##quirk)
#define PVR_HAS_ENHANCEMENT(pvr_dev, enhancement) ((pvr_dev)->enhancements.has_ern##enhancement)
#endif
