/* SPDX-License-Identifier: MIT */
/* Linux kernel API subset for the unchanged drm/imagination job-layer sources NativePowerVR compiles (kernel/pvr_context.c,
 * pvr_cccb.c, pvr_free_list.c, pvr_hwrt.c, pvr_stream.c, pvr_stream_defs.c, pvr_device_info.c, pvr_dump.c): kref, lists,
 * xarray, mutex/spinlock, scatterlist page iteration, memdup_user. The locks are IOLocks and the xarray is a flat table,
 * both implemented in NativePowerVRJobs.cpp (thread context only; nothing here runs in interrupt context).
 *
 * C++ (NativePowerVR.cpp, NativePowerVRJobs.cpp) sees the types, because struct pvr_device and struct pvr_file
 * (stub/native_powervr_kernel.h) embed them, and the inline helpers the drm/imagination headers call. Names that the XNU
 * kernel headers may also define (fls, round_up, the mutex_/spin_ macros) exist for C only. */
#ifndef NATIVE_POWERVR_LINUX_JOBS_H
#define NATIVE_POWERVR_LINUX_JOBS_H
#include "native_powervr_linux_kernel.h"
#ifdef __cplusplus
extern "C" {
#endif

#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
#endif
#ifndef __always_unused
#define __always_unused __attribute__((unused))
#endif
#ifndef U32_MAX
#define U8_MAX ((u8)~0U)
#define U16_MAX ((u16)~0U)
#define U32_MAX ((u32)~0U)
#define U64_MAX ((u64)~0ULL)
#endif
#define lower_32_bits(n) ((u32)((n) & 0xffffffff))
#define upper_32_bits(n) ((u32)((u64)(n) >> 32))
/* include/linux/bits.h (64-bit long), for the job flags of pvr_rogue_fwif_client.h */
#ifndef BIT_MASK
#define BIT_MASK(nr) (1UL << ((nr) % 64))
#endif
/* XNU errno values, as native_powervr_linux_kernel.h uses. */
#ifndef E2BIG
#define E2BIG 7
#endif
#ifndef EACCES
#define EACCES 13
#endif

/* Locks: an IOLock each, allocated by the init call (mutex_init(), spin_lock_init(), xa_init_flags()). */
struct mutex { void *native_lock; };
typedef struct { void *native_lock; } spinlock_t;
void native_powervr_lock_init(void **lock);
void native_powervr_lock_destroy(void **lock);
void native_powervr_lock(void *lock);
void native_powervr_unlock(void *lock);

/* include/linux/kref.h */
struct kref { atomic_t refcount; };
static inline void kref_init(struct kref *k) { __atomic_store_n(&k->refcount.counter, 1, __ATOMIC_RELAXED); }
static inline void kref_get(struct kref *k) { __atomic_fetch_add(&k->refcount.counter, 1, __ATOMIC_RELAXED); }
static inline int kref_put(struct kref *k, void (*release)(struct kref *k))
{
	if (__atomic_sub_fetch(&k->refcount.counter, 1, __ATOMIC_ACQ_REL) == 0) {
		release(k);
		return 1;
	}
	return 0;
}
static inline int kref_get_unless_zero(struct kref *k)
{
	int v = __atomic_load_n(&k->refcount.counter, __ATOMIC_RELAXED);
	while (v) {
		if (__atomic_compare_exchange_n(&k->refcount.counter, &v, v + 1, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED))
			return 1;
	}
	return 0;
}

/* include/linux/list.h (no poisoning: list_del() leaves NULL links) */
struct list_head { struct list_head *next, *prev; };
static inline void INIT_LIST_HEAD(struct list_head *l) { l->next = l; l->prev = l; }
static inline void native_powervr_list_insert(struct list_head *n, struct list_head *prev, struct list_head *next)
{
	next->prev = n;
	n->next = next;
	n->prev = prev;
	prev->next = n;
}
static inline void list_add(struct list_head *n, struct list_head *head) { native_powervr_list_insert(n, head, head->next); }
static inline void list_add_tail(struct list_head *n, struct list_head *head) { native_powervr_list_insert(n, head->prev, head); }
static inline void list_del(struct list_head *e)
{
	e->next->prev = e->prev;
	e->prev->next = e->next;
	e->next = 0;
	e->prev = 0;
}
static inline void list_del_init(struct list_head *e)
{
	e->next->prev = e->prev;
	e->prev->next = e->next;
	INIT_LIST_HEAD(e);
}
static inline int list_empty(const struct list_head *h) { return h->next == h; }
#define list_entry(ptr, type, member) container_of(ptr, type, member)
#define list_first_entry(ptr, type, member) list_entry((ptr)->next, type, member)
#define list_next_entry(pos, member) list_entry((pos)->member.next, __typeof__(*(pos)), member)
#define list_entry_is_head(pos, head, member) (&(pos)->member == (head))
#define list_for_each_safe(pos, n, head) for (pos = (head)->next, n = pos->next; pos != (head); pos = n, n = pos->next)
#define list_for_each_entry(pos, head, member)                                                    \
	for (pos = list_first_entry(head, __typeof__(*pos), member); !list_entry_is_head(pos, head, member); \
	     pos = list_next_entry(pos, member))
#define list_for_each_entry_safe(pos, n, head, member)                                            \
	for (pos = list_first_entry(head, __typeof__(*pos), member), n = list_next_entry(pos, member); \
	     !list_entry_is_head(pos, head, member); pos = n, n = list_next_entry(n, member))

/* include/linux/xarray.h, the allocating form: a table indexed by the ID, lowest free ID first (as xa_alloc()). Every
 * access takes its lock, and xa_load() is only valid under xa_lock(), which is how every caller here uses it (Linux
 * reads under RCU instead). xa_erase() and xa_alloc() take the lock themselves, as in Linux. */
struct xarray { void *native_lock; void **slots; u32 nr_slots; u32 flags; };
struct xa_limit { u32 max; u32 min; };
#define XA_FLAGS_ALLOC 1U
#define XA_FLAGS_ALLOC1 3U   /* XA_FLAGS_ALLOC with ID 0 never handed out */
void xa_init_flags(struct xarray *xa, unsigned int flags);
void xa_destroy(struct xarray *xa);
int xa_alloc(struct xarray *xa, u32 *id, void *entry, struct xa_limit limit, gfp_t gfp);
void *native_powervr_xa_erase(struct xarray *xa, unsigned long index);
void *native_powervr_xa_load(struct xarray *xa, unsigned long index);       /* caller holds xa_lock() */
/* The first entry at an index >= *index (after == 0) or > *index (after != 0); its index is stored in *index. */
void *native_powervr_xa_find(struct xarray *xa, unsigned long *index, int after);
bool xa_empty(struct xarray *xa);
#define xa_lock(xa) native_powervr_lock((xa)->native_lock)
#define xa_unlock(xa) native_powervr_unlock((xa)->native_lock)
#ifdef __cplusplus
/* C++ callers (the inline lookups of pvr_context.h, pvr_hwrt.h and pvr_free_list.h) assign the entry to a typed pointer
 * as C does implicitly. */
extern "C++" {
struct native_powervr_xa_entry {
	void *p;
	template <class T> operator T *() const { return static_cast<T *>(p); }
	explicit operator bool() const { return p != nullptr; }
};
static inline native_powervr_xa_entry xa_load(struct xarray *xa, unsigned long index) { return {native_powervr_xa_load(xa, index)}; }
static inline native_powervr_xa_entry xa_erase(struct xarray *xa, unsigned long index) { return {native_powervr_xa_erase(xa, index)}; }
}
#define xa_limit_32b (xa_limit{U32_MAX, 0})
#else
static inline void *xa_load(struct xarray *xa, unsigned long index) { return native_powervr_xa_load(xa, index); }
static inline void *xa_erase(struct xarray *xa, unsigned long index) { return native_powervr_xa_erase(xa, index); }
#define xa_limit_32b ((struct xa_limit){.max = U32_MAX, .min = 0})
#define xa_for_each(xa, index, entry) \
	for (index = 0, entry = native_powervr_xa_find(xa, &(index), 0); entry; entry = native_powervr_xa_find(xa, &(index), 1))
#endif

/* memdup_user(): NativePowerVR's callers pass kernel copies of the user buffers (native_powervr_jobs.h), so this is a
 * kmalloc() and a memcpy(); ERR_PTR(-ENOMEM) on failure, as Linux. */
void *memdup_user(const void *src, size_t len);
#define u64_to_user_ptr(x) ((const void *)(uintptr_t)(x))

#ifndef __cplusplus
#define mutex_init(m) native_powervr_lock_init(&(m)->native_lock)
#define mutex_destroy(m) native_powervr_lock_destroy(&(m)->native_lock)
#define mutex_lock(m) native_powervr_lock((m)->native_lock)
#define mutex_unlock(m) native_powervr_unlock((m)->native_lock)
#define spin_lock_init(s) native_powervr_lock_init(&(s)->native_lock)
#define spin_lock(s) native_powervr_lock((s)->native_lock)
#define spin_unlock(s) native_powervr_unlock((s)->native_lock)
#define lockdep_assert_held(l) ((void)(l))
#define in_interrupt() 0
/* include/asm-generic/bitops/fls.h */
static __always_inline int fls(unsigned int x) { return x ? 32 - __builtin_clz(x) : 0; }
/* include/linux/math.h */
#define round_up(x, y) ((((x) - 1) | ((__typeof__(x))((y) - 1))) + 1)
/* include/uapi/linux/capability.h; no caller holds a capability here (the privileged path is drm_is_current_master()). */
#define CAP_SYS_NICE 23
static inline bool capable(int cap) { (void)cap; return false; }
#define drm_warn_once drm_warn
/* include/linux/scatterlist.h: the 4 KiB pages (PAGE_SIZE of the C shim) of a DMA-mapped table. */
struct sg_dma_page_iter { struct scatterlist *sg; unsigned int left; u64 offset; bool started; };
static inline void native_powervr_sg_dma_iter_start(struct sg_dma_page_iter *it, struct sg_table *sgt, unsigned long pgoffset)
{
	it->sg = sgt->sgl;
	it->left = sgt->nents;
	it->offset = (u64)pgoffset << PAGE_SHIFT;
	it->started = false;
}
static inline bool native_powervr_sg_dma_iter_next(struct sg_dma_page_iter *it)
{
	if (it->started)
		it->offset += PAGE_SIZE;
	it->started = true;
	while (it->left && it->offset >= sg_dma_len(it->sg)) {
		it->offset -= sg_dma_len(it->sg);
		it->sg++;
		it->left--;
	}
	return it->left != 0;
}
#define for_each_sgtable_dma_page(sgt, iter, pgoffset) \
	for (native_powervr_sg_dma_iter_start(iter, sgt, pgoffset); native_powervr_sg_dma_iter_next(iter);)
#define sg_page_iter_dma_address(iter) ((iter)->sg->dma_address + (iter)->offset)
#endif

#ifdef __cplusplus
}
#endif
#endif
