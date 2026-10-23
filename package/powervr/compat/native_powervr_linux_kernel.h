/* SPDX-License-Identifier: MIT */
/* Linux kernel API subset for the unchanged drm/imagination kernel sources NativePowerVR compiles (pvr_mmu.c, ...).
 * Memory, DMA, vmap and allocation are implemented in NativePowerVR.cpp on IOKit (IOBufferMemoryDescriptor, IOMalloc). */
#ifndef NATIVE_POWERVR_LINUX_KERNEL_H
#define NATIVE_POWERVR_LINUX_KERNEL_H
#include "native_powervr_linux_compat.h"
#ifdef __cplusplus
extern "C" {
#endif
/* The pages of this shim are 4 KiB (one IOKit buffer each). Only the C sources (pvr_mmu.c) see these: in C++ the XNU
 * definitions (16 KiB kernel pages) stay, and NativePowerVR.cpp does not use them. */
#ifndef __cplusplus
#undef PAGE_SHIFT
#undef PAGE_SIZE
#undef PAGE_MASK
#define PAGE_SHIFT 12
#define PAGE_SIZE (1UL << PAGE_SHIFT)
#define PAGE_MASK (~(PAGE_SIZE - 1))
#endif
#define HZ 100
typedef unsigned gfp_t;
#define GFP_KERNEL 0x1u
#define __GFP_ZERO 0x100u
#ifndef EIO
#define EIO 5
#define ENOMEM 12
#define EFAULT 14
#define EBUSY 16
#define EINVAL 22
#define ENOSPC 28
#define ERANGE 34
#define ETIMEDOUT 60
#define ENODEV 19
#endif
#ifndef ENXIO
#define ENXIO 6
#endif
#ifndef EPERM
#define EPERM 1
#endif
#ifndef EEXIST
#define EEXIST 17
#endif
#define unlikely(x) __builtin_expect(!!(x), 0)
#define likely(x) __builtin_expect(!!(x), 1)
#define READ_ONCE(x) (*(const volatile __typeof__(x) *)&(x))
#define WRITE_ONCE(x, v) (*(volatile __typeof__(x) *)&(x) = (v))
typedef struct { volatile int counter; } atomic_t;
static inline int atomic_read(const atomic_t *a) { return a->counter; }
static inline void atomic_set(atomic_t *a, int v) { a->counter = v; }
static inline int atomic_xchg(atomic_t *a, int v) { return __atomic_exchange_n(&a->counter, v, __ATOMIC_SEQ_CST); }
static inline int atomic_fetch_or(int v, atomic_t *a) { return __atomic_fetch_or(&a->counter, v, __ATOMIC_SEQ_CST); }
#define min(a, b) ((a) < (b) ? (a) : (b))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define max_t(t, a, b) ((t)(a) > (t)(b) ? (t)(a) : (t)(b))
#define __ffs(x) ((unsigned long)__builtin_ctzll(x))
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define IS_ALIGNED(x, a) (((x) & ((__typeof__(x))(a) - 1)) == 0)
#define check_add_overflow(a, b, d) __builtin_add_overflow(a, b, d)
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#define MAX_ERRNO 4095
static inline void *ERR_PTR(long e) { return (void *)e; }
static inline long PTR_ERR(const void *p) { return (long)p; }
static inline bool IS_ERR(const void *p) { return (unsigned long)p >= (unsigned long)-MAX_ERRNO; }
static inline int PTR_ERR_OR_ZERO(const void *p) { return IS_ERR(p) ? (int)PTR_ERR(p) : 0; }
static inline bool IS_ERR_OR_NULL(const void *p) { return !p || IS_ERR(p); }
void native_powervr_warn(const char *file, int line, const char *what);
#define WARN_ON(c) ({ bool __c = !!(c); if (__c) native_powervr_warn(__FILE__, __LINE__, #c); __c; })
#define WARN(c, ...) WARN_ON(c)
#define WARN_ON_ONCE(c) WARN_ON(c)
void *kzalloc(size_t size, gfp_t flags);
void *kmalloc(size_t size, gfp_t flags);
void *kcalloc(size_t n, size_t size, gfp_t flags);
void kfree(const void *p);
/* kzalloc_obj(obj[, gfp]) (include/linux/slab.h, v6.19+): a zeroed allocation of sizeof(obj). */
#define __kzalloc_obj1(o) ((__typeof__(o) *)kzalloc(sizeof(o), GFP_KERNEL))
#define __kzalloc_obj2(o, g) ((__typeof__(o) *)kzalloc(sizeof(o), (g)))
#define __kzalloc_objN(_1, _2, n, ...) n
#define kzalloc_obj(...) __kzalloc_objN(__VA_ARGS__, __kzalloc_obj2, __kzalloc_obj1, _)(__VA_ARGS__)
/* Barriers (arm64). */
#define wmb() __asm__ volatile("dsb st" ::: "memory")
#define rmb() __asm__ volatile("dsb ld" ::: "memory")
#define mb() __asm__ volatile("dsb sy" ::: "memory")
#define kmemleak_ignore(p) ((void)(p))
#define kmemleak_alloc(...) ((void)0)
#define kmemleak_free(p) ((void)(p))
/* Pages: one 4 KiB physically contiguous IOKit buffer each. */
struct page;
struct device;
enum dma_data_direction { DMA_BIDIRECTIONAL = 0, DMA_TO_DEVICE = 1, DMA_FROM_DEVICE = 2 };
struct page *alloc_page(gfp_t flags);
void __free_page(struct page *page);
void *page_address(struct page *page);
dma_addr_t dma_map_page(struct device *dev, struct page *page, size_t offset, size_t size, enum dma_data_direction dir);
void dma_unmap_page(struct device *dev, dma_addr_t addr, size_t size, enum dma_data_direction dir);
static inline int dma_mapping_error(struct device *dev, dma_addr_t addr) { (void)dev; return addr == 0; }
void dma_sync_single_for_device(struct device *dev, dma_addr_t addr, size_t size, enum dma_data_direction dir);
typedef struct { unsigned v; } pgprot_t;
#define PAGE_KERNEL ((pgprot_t){0})
#define pgprot_writecombine(p) (p)
#define VM_MAP 0x4u
void *vmap(struct page **pages, unsigned int count, unsigned long flags, pgprot_t prot);
void vunmap(const void *addr);
/* Scatter lists: a flat (DMA address, length) array. */
struct scatterlist { dma_addr_t dma_address; unsigned int dma_length; };
struct sg_table { struct scatterlist *sgl; unsigned int nents; unsigned int orig_nents; };
#define sg_dma_address(sg) ((sg)->dma_address)
#define sg_dma_len(sg) ((sg)->dma_length)
#define for_each_sgtable_dma_sg(sgt, sg, i) for ((i) = 0, (sg) = (sgt)->sgl; (i) < (int)(sgt)->nents; (i)++, (sg)++)
void dma_sync_sg_for_device(struct device *dev, struct scatterlist *sg, int nents, enum dma_data_direction dir);
#define dma_sync_sgtable_for_device(dev, sgt, dir) dma_sync_sg_for_device(dev, (sgt)->sgl, (int)(sgt)->nents, dir)
#define typeof_member(T, m) __typeof__(((T *)0)->m)
#include <string.h>
enum dev_dma_attr { DEV_DMA_NOT_SUPPORTED, DEV_DMA_NON_COHERENT, DEV_DMA_COHERENT };
static inline enum dev_dma_attr device_get_dma_attr(struct device *d) { (void)d; return DEV_DMA_NON_COHERENT; }
/* DRM device and printing. */
struct drm_device { struct device *dev; };
static inline bool drm_dev_enter(struct drm_device *d, int *idx) { (void)d; *idx = 0; return true; }
static inline void drm_dev_exit(int idx) { (void)idx; }
void native_powervr_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
#define drm_err(d, fmt, ...) native_powervr_log("powervr: " fmt, ##__VA_ARGS__)
#define drm_warn(d, fmt, ...) native_powervr_log("powervr: " fmt, ##__VA_ARGS__)
#define drm_info(d, fmt, ...) native_powervr_log("powervr: " fmt, ##__VA_ARGS__)
#ifdef __cplusplus
}
#endif
#endif
