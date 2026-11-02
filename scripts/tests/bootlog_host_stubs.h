/* Host stubs for package/A04BR3BootLog.c (scripts/tests/test_bootlog.py): an 8 MiB window in memory instead of NativeMsdc. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define __unused __attribute__((unused))
typedef int wait_result_t;
typedef void *thread_t;
#define KERN_SUCCESS 0
#define NULL_THREAD ((thread_t)0)
typedef unsigned long clock_sec_t;
typedef unsigned int clock_usec_t;
static char kernel_uuid_string[37] = "0123ABCD-4567-89EF-0123-456789ABCDEF";
static uint64_t host_clock;
static inline uint64_t mach_absolute_time(void) { return host_clock += 13000000; }
static inline void absolutetime_to_nanoseconds(uint64_t t, uint64_t *ns) { *ns = t * 1000 / 13; }
static inline int PE_parse_boot_argn(const char *n, void *v, int s) { (void)n; (void)s; *(unsigned *)v = 1; return 1; }
static inline int kernel_thread_start(void (*f)(void *, wait_result_t), void *a, thread_t *t) { (void)f; (void)a; *t = 0; return 1; }
static inline void thread_deallocate(thread_t t) { (void)t; }
static inline size_t host_strlcpy(char *d, const char *s, size_t n) { size_t l = strlen(s); if (n) { size_t c = l < n - 1 ? l : n - 1; memcpy(d, s, c); d[c] = 0; } return l; }
#define strlcpy host_strlcpy

