/* Explicit, single-threaded synthetic copy measurement; boot path is opt-in. */
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
        fprintf(stderr, "a04br3_mem_copy phase=skip reason=vm_statistics_unavailable\n"); return -1;
    }
    // XNU host_statistics64 includes speculative pages in free_count. They
    // are cached pages, not unoccupied pages; exclude them from the reserve.
    if (stats.speculative_count > stats.free_count || !page_size) {
        fprintf(stderr, "a04br3_mem_copy phase=skip reason=vm_statistics_inconsistent\n"); return -1;
    }
    uint64_t reported_free = (uint64_t)stats.free_count * page_size;
    uint64_t speculative = (uint64_t)stats.speculative_count * page_size;
    uint64_t free_bytes = reported_free - speculative;
    uint64_t threshold = 2 * (uint64_t)size + 32 * UINT64_C(1024) * 1024;
    printf("a04br3_mem_copy phase=memory_check reported_free_bytes=%" PRIu64 " speculative_bytes=%" PRIu64 " free_bytes=%" PRIu64 " threshold_bytes=%" PRIu64 "\n", reported_free, speculative, free_bytes, threshold);
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
static int run_copy(unsigned long mib, int checked) {
    size_t size = (size_t)mib * 1024 * 1024;
    printf("a04br3_mem_copy synthetic=1 phase=prepare size_bytes=%zu monotonic_ns=%" PRIu64 "\n", size, now_ns());
#ifdef __APPLE__
    if (!checked && enough_free_memory(size) != 1) return 1;
#endif
    (void)checked;
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

static void wait_seconds(unsigned seconds) {
    while (seconds) seconds = sleep(seconds);
}
int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--boot-run")) {
        /* The inherited descriptors may be closed; this is best effort only. */
        fprintf(stderr, "a04br3_mem_copy phase=main_entered\n");
        fflush(stderr);
        if (!freopen("/dev/console", "w", stdout)) {
            int saved = errno;
            fprintf(stderr, "a04br3_mem_copy phase=console_error stream=stdout errno=%d\n", saved);
            return 1;
        }
        setvbuf(stdout, NULL, _IOLBF, 0);
        printf("a04br3_mem_copy phase=console_stdout_ready\n");
        if (!freopen("/dev/console", "w", stderr)) {
            int saved = errno;
            printf("a04br3_mem_copy phase=console_error stream=stderr errno=%d\n", saved);
            return 1;
        }
        printf("a04br3_mem_copy synthetic=1 phase=waiting delay_seconds=180\n");
        wait_seconds(180);
        // One bounded observation per size and sample. Retry insufficient
        // unoccupied pages without allocating; no retry on statistics errors.
        static const unsigned long sizes[] = {4, 1};
        unsigned completed = 0, skipped_sizes = 0;
        for (size_t i = 0; i < sizeof(sizes) / sizeof(sizes[0]); ++i) {
            for (unsigned sample = 1; sample <= 3; ++sample) {
                if (i || sample > 1) {
                    printf("a04br3_mem_copy synthetic=1 phase=recovery delay_seconds=10\n");
                    wait_seconds(10);
                }
                printf("a04br3_mem_copy synthetic=1 phase=sample size_mib=%lu sample=%u\n", sizes[i], sample);
                int available = 1;
#ifdef __APPLE__
                for (unsigned attempt = 1; attempt <= 3; ++attempt) {
                    available = enough_free_memory((size_t)sizes[i] * 1024 * 1024);
                    if (available != 0 || attempt == 3) break;
                    printf("a04br3_mem_copy synthetic=1 phase=retry attempt=%u delay_seconds=15\n", attempt);
                    wait_seconds(15);
                }
#endif
                if (available < 0) return 1;
                if (!available) { ++skipped_sizes; break; }
                if (run_copy(sizes[i], 1)) return 1;
                ++completed;
            }
        }
        printf("a04br3_mem_copy synthetic=1 phase=complete samples=%u incomplete_sizes=%u\n", completed, skipped_sizes);
        if (!completed) return 1;
        return 0;
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
    return run_copy(mib, 0);
}
