/* SPDX-License-Identifier: MIT */
/* Darwin user-space stand-in for Linux include/uapi/linux/const.h, which linux/uapi/drm/pvr_drm.h includes; the same
 * definitions as the Linux header (and as Mesa's darwin winsys compat header of patches/mesa/pvr-darwin-winsys.patch). */
#ifndef POWERVRPROBED_COMPAT_LINUX_CONST_H
#define POWERVRPROBED_COMPAT_LINUX_CONST_H

#define __AC(X, Y) (X##Y)
#define _AC(X, Y) __AC(X, Y)
#define _AT(T, X) ((T)(X))

#define _UL(x) (_AC(x, UL))
#define _ULL(x) (_AC(x, ULL))

#define _BITUL(x) (_UL(1) << (x))
#define _BITULL(x) (_ULL(1) << (x))

#endif
