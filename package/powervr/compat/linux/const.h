#include "../native_powervr_linux_compat.h"
/* include/uapi/linux/const.h, for the unchanged uAPI pvr_drm.h */
#ifndef _BITULL
#define _BITUL(x) (1UL << (x))
#define _BITULL(x) (1ULL << (x))
#endif
