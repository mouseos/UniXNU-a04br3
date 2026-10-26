/* SPDX-License-Identifier: MIT */
#ifndef NATIVE_POWERVR_BO_FLAGS_H
#define NATIVE_POWERVR_BO_FLAGS_H
/* Kernel-owned metadata only. Never accepted from the DRM/NativePowerVR ABI.
 * Kept on the BO so every mapping/remapping enforces GPU read-only access. */
#define NATIVE_POWERVR_BO_READ_ONLY (1ULL << 63)
#endif
