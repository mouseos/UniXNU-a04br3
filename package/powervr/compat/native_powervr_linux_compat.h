/* SPDX-License-Identifier: MIT */
/* Minimal Linux type and macro shim so the unchanged drm/imagination headers (GPL-2.0-only OR MIT, used under MIT)
 * compile in XNU's kernel C/C++ context. Only what those headers use. */
#ifndef NATIVE_POWERVR_LINUX_COMPAT_H
#define NATIVE_POWERVR_LINUX_COMPAT_H
#include <stdint.h>
#include <stddef.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
typedef int8_t s8; typedef int16_t s16; typedef int32_t s32; typedef int64_t s64;
typedef uint8_t __u8; typedef uint16_t __u16; typedef uint32_t __u32; typedef uint64_t __u64;
typedef int32_t __s32; typedef int64_t __s64;
typedef u64 __attribute__((aligned(8))) aligned_u64;
typedef u64 dma_addr_t;
#ifndef __cplusplus
typedef _Bool bool;
#define true 1
#define false 0
#endif
#define __packed __attribute__((packed))
#ifndef __aligned
#define __aligned(x) __attribute__((aligned(x)))
#endif
#define BIT(n) (1UL << (n))
#define BIT_ULL(n) (1ULL << (n))
#define GENMASK(h, l) (((~0UL) << (l)) & (~0UL >> (63 - (h))))
#define GENMASK_ULL(h, l) (((~0ULL) << (l)) & (~0ULL >> (63 - (h))))
#ifndef ARRAY_SIZE
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#endif
#define SZ_1K 0x400
#define SZ_4K 0x1000
#define SZ_8K 0x2000
#define SZ_16K 0x4000
#define SZ_32K 0x8000
#define SZ_64K 0x10000
#define SZ_256K 0x40000
#define SZ_1M 0x100000
#define SZ_2M 0x200000
#define SZ_4M 0x400000
#define SZ_16M 0x1000000
#define SZ_256M 0x10000000
#define SZ_1G 0x40000000ULL
#ifndef static_assert
#ifdef __cplusplus
#else
#define static_assert _Static_assert
#endif
#endif
#define BUILD_BUG_ON(c) static_assert(!(c), #c)
#define ALIGN(x, a) (((x) + ((a) - 1)) & ~((__typeof__(x))(a) - 1))
#define sizeof_field(TYPE, MEMBER) sizeof((((TYPE *)0)->MEMBER))
#define flex_array_size(p, member, count) ((count) * sizeof(*(p)->member))
#define __stringify_1(x) #x
#define __stringify(x) __stringify_1(x)
#endif
