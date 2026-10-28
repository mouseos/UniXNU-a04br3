/* Explicit, single-threaded synthetic copy measurement; never runs at boot. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#ifdef __APPLE__
#include <mach/mach.h>
#endif

#ifdef __APPLE__
static int enough_free_memory(size_t size) {
    mach_port_t host = mach_host_self();
    vm_statistics64_data_t stats;
    mach_msg_type_number_t count = HOST_VM_INFO64_COUNT;
    vm_size_t page_size = 0;
    kern_return_t r1 = host_page_size(host, &page_size);
    kern_return_t r2 = host_statistics64(host, HOST_VM_INFO64, (host_info64_t)&stats, &count);
    mach_port_deallocate(mach_task_self(), host);
    if (r1 != KERN_SUCCESS || r2 != KERN_SUCCESS) {
        fprintf(stderr, "a04br3_mem_copy phase=skip reason=vm_statistics_unavailable\n"); return 0;
    }
    uint64_t free_bytes = (uint64_t)stats.free_count * page_size;
    uint64_t threshold = 2 * (uint64_t)size + 32 * UINT64_C(1024) * 1024;
    printf("a04br3_mem_copy phase=memory_check free_bytes=%" PRIu64 " threshold_bytes=%" PRIu64 "\n", free_bytes, threshold);
    if (free_bytes < threshold) {
        fprintf(stderr, "a04br3_mem_copy phase=skip reason=insufficient_free_pages\n"); return 0;
    }
    return 1;
}
#endif
static uint64_t now_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) != 0) { perror("clock_gettime"); exit(1); }
    return (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec;
}
int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--boot-run")) {
        if (!freopen("/dev/console", "w", stdout) || !freopen("/dev/console", "w", stderr)) return 1;
        setvbuf(stdout, NULL, _IOLBF, 0);
        printf("a04br3_mem_copy synthetic=1 phase=waiting delay_seconds=180\n");
        unsigned remaining = 180;
        while (remaining) remaining = sleep(remaining);
        static char *boot_args[] = {"a04br3-memory-copy-bench", "--run", "16", NULL};
        argc = 3; argv = boot_args;
    }
    if (argc != 3 || strcmp(argv[1], "--run")) {
        fprintf(stderr, "usage: %s --run size_mib (1..64)\n", argv[0]); return 2;
    }
    char *end;
    errno = 0;
    unsigned long mib = strtoul(argv[2], &end, 10);
    if (errno || !*argv[2] || *argv[2] == '-' || *end || mib < 1 || mib > 64) {
        fprintf(stderr, "invalid size_mib (1..64)\n"); return 2;
    }
    size_t size = (size_t)mib * 1024 * 1024;
    printf("a04br3_mem_copy synthetic=1 phase=prepare size_bytes=%zu monotonic_ns=%" PRIu64 "\n", size, now_ns());
#ifdef __APPLE__
    if (!enough_free_memory(size)) return 1;
#endif
    unsigned char *src = malloc(size), *dst = malloc(size);
    if (!src || !dst) { fprintf(stderr, "allocation failed\n"); free(src); free(dst); return 1; }
    /* Touch both buffers and use nonuniform data before timing. */
    for (size_t i = 0; i < size; ++i) src[i] = (unsigned char)((i * 17u + (i >> 12)) & 255u);
    // Volatile writes ensure the destination pages are committed before timing.
    volatile unsigned char *touch = dst;
    for (size_t i = 0; i < size; i += 4096) touch[i] = 0;
    touch[size - 1] = 0;
    uint64_t start = now_ns();
    memcpy(dst, src, size);
    uint64_t end_ns = now_ns();
    int ok = memcmp(src, dst, size) == 0;
    printf("a04br3_mem_copy synthetic=1 threads=1 size_bytes=%zu start_monotonic_ns=%" PRIu64
           " end_monotonic_ns=%" PRIu64 " elapsed_ns=%" PRIu64 " logical_bytes=%zu verified=%d\n",
           size, start, end_ns, end_ns - start, size, ok);
    free(dst); free(src);
    return ok && end_ns > start ? 0 : 1;
}
