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

static uint64_t now_ns(void);

#ifdef __APPLE__
static int enough_free_memory(size_t size, int observe_only) {
    mach_port_t host = mach_host_self();
    vm_statistics64_data_t stats = {0};
    mach_msg_type_number_t count = HOST_VM_INFO64_COUNT;
    vm_size_t page_size = 0;
    kern_return_t r1 = host_page_size(host, &page_size);
    uint64_t query_begin = now_ns();
    kern_return_t r2 = host_statistics64(host, HOST_VM_INFO64, (host_info64_t)&stats, &count);
    uint64_t query_end = now_ns();
    mach_port_deallocate(mach_task_self(), host);
    if (r1 != KERN_SUCCESS || r2 != KERN_SUCCESS || count < HOST_VM_INFO64_REV0_COUNT) {
        fprintf(stderr, "a04br3_mem_copy phase=skip reason=vm_statistics_unavailable\n"); return -1;
    }
    // XNU host_statistics64 includes speculative pages in free_count. They
    // are cached pages, not unoccupied pages; exclude them from the reserve.
    if (stats.speculative_count > stats.free_count || !page_size) {
        fprintf(stderr, "a04br3_mem_copy phase=skip reason=vm_statistics_inconsistent\n"); return -1;
    }
    // Reuse this same VM snapshot: counters are cumulative and non-atomic.
    printf("a04br3_mem_copy phase=vm_activity monotonic_ns=%" PRIu64
           " query_begin_ns=%" PRIu64 " query_end_ns=%" PRIu64 " cache_freshness=unknown page_size=%lu pageins=%" PRIu64 " pageouts=%" PRIu64
           " reactivations=%" PRIu64 " faults=%" PRIu64
           " purgeable_pages=%" PRIu64 " purges=%" PRIu64 " non_atomic=1\n",
           now_ns(), query_begin, query_end, (unsigned long)page_size, (uint64_t)stats.pageins,
           (uint64_t)stats.pageouts, (uint64_t)stats.reactivations,
           (uint64_t)stats.faults, (uint64_t)stats.purgeable_count, (uint64_t)stats.purges);
    if (count >= HOST_VM_INFO64_REV1_COUNT) {
        printf("a04br3_mem_copy phase=vm_activity_extended compressions=%" PRIu64
               " decompressions=%" PRIu64 " swapins=%" PRIu64 " swapouts=%" PRIu64 " non_atomic=1\n",
               (uint64_t)stats.compressions, (uint64_t)stats.decompressions,
               (uint64_t)stats.swapins, (uint64_t)stats.swapouts);
    } else {
        printf("a04br3_mem_copy phase=vm_activity_extended status=unavailable\n");
    }
    uint64_t reported_free = (uint64_t)stats.free_count * page_size;
    uint64_t speculative = (uint64_t)stats.speculative_count * page_size;
    uint64_t free_bytes = reported_free - speculative;
    printf("a04br3_mem_copy phase=vm_usage query_begin_ns=%" PRIu64 " query_end_ns=%" PRIu64
           " page_size=%lu free_pages=%u speculative_pages=%u active_pages=%u inactive_pages=%u wired_pages=%u cache_freshness=unknown non_atomic=1\n",
           query_begin, query_end, (unsigned long)page_size, stats.free_count, stats.speculative_count,
           stats.active_count, stats.inactive_count, stats.wire_count);
    if (count >= HOST_VM_INFO64_REV1_COUNT)
        printf("a04br3_mem_copy phase=vm_usage_extended compressor_pages=%u external_pages=%u internal_pages=%u non_atomic=1\n",
               stats.compressor_page_count, stats.external_page_count, stats.internal_page_count);
    else
        printf("a04br3_mem_copy phase=vm_usage_extended status=unavailable\n");
    if (observe_only) return 1;
    uint64_t threshold = 2 * (uint64_t)size + 32 * UINT64_C(1024) * 1024;
    printf("a04br3_mem_copy phase=memory_check reported_free_bytes=%" PRIu64 " speculative_bytes=%" PRIu64 " free_bytes=%" PRIu64 " threshold_bytes=%" PRIu64 " cache_freshness=unknown\n", reported_free, speculative, free_bytes, threshold);
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
static int run_copy(size_t size, int checked) {
    printf("a04br3_mem_copy synthetic=1 phase=prepare size_bytes=%zu monotonic_ns=%" PRIu64 "\n", size, now_ns());
#ifdef __APPLE__
    if (!checked) { int available = enough_free_memory(size, 0); if (available != 1) return available == 0 ? 2 : 1; }
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
    if (!ok || end_ns <= start) {
        printf("a04br3_mem_copy phase=error reason=%s\n", !ok ? "verification_failed" : "invalid_elapsed");
        free(dst); free(src);
        return 1;
    }
    printf("a04br3_mem_copy synthetic=1 threads=1 size_bytes=%zu start_monotonic_ns=%" PRIu64
           " end_monotonic_ns=%" PRIu64 " elapsed_ns=%" PRIu64 " logical_bytes=%zu verified=%d\n",
           size, start, end_ns, end_ns - start, size, ok);
    free(dst); free(src);
    return 0;
}

static void wait_seconds(unsigned seconds) {
    while (seconds) seconds = sleep(seconds);
}
int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    if (argc == 2 && !strcmp(argv[1], "--observe")) {
        printf("a04br3_mem_copy phase=begin kind=observe size_bytes=0\n");
#ifdef __APPLE__
        int rc = enough_free_memory(0, 1) == 1 ? 0 : 1;
#else
        fprintf(stderr, "a04br3_mem_copy phase=skip reason=vm_statistics_unavailable\n");
        int rc = 1;
#endif
        printf("a04br3_mem_copy phase=end kind=observe status=%s\n", rc ? "error" : "ok");
        return rc;
    }
    if (argc == 2 && !strcmp(argv[1], "--boot-run")) {
        if (!freopen("/dev/console", "w", stdout) || !freopen("/dev/console", "w", stderr)) return 1;
        setvbuf(stdout, NULL, _IOLBF, 0);
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
                    available = enough_free_memory((size_t)sizes[i] * 1024 * 1024, 0);
                    if (available != 0 || attempt == 3) break;
                    printf("a04br3_mem_copy synthetic=1 phase=retry attempt=%u delay_seconds=15\n", attempt);
                    wait_seconds(15);
                }
#endif
                if (available < 0) return 1;
                if (!available) { ++skipped_sizes; break; }
                if (run_copy((size_t)sizes[i] * 1024 * 1024, 1)) return 1;
                ++completed;
            }
        }
        printf("a04br3_mem_copy synthetic=1 phase=complete samples=%u incomplete_sizes=%u\n", completed, skipped_sizes);
        if (!completed) return 1;
        return 0;
    }
    if (argc != 3 || (strcmp(argv[1], "--run") && strcmp(argv[1], "--run-kib"))) {
        fprintf(stderr, "usage: %s --observe | --run size_mib (1..64) | --run-kib 64|256|1024\n", argv[0]); return 2;
    }
    char *end;
    errno = 0;
    unsigned long mib = strtoul(argv[2], &end, 10);
    int kib = !strcmp(argv[1], "--run-kib");
    if (errno || !*argv[2] || *argv[2] == '-' || *end ||
        (kib ? (mib != 64 && mib != 256 && mib != 1024) : (mib < 1 || mib > 64))) {
        fprintf(stderr, "invalid copy size\n"); return 2;
    }
    size_t size = (size_t)mib * (kib ? 1024 : 1024 * 1024);
    printf("a04br3_mem_copy phase=begin kind=copy size_bytes=%zu\n", size);
    int rc = run_copy(size, 0);
    printf("a04br3_mem_copy phase=end kind=copy status=%s\n", rc == 0 ? "ok" : rc == 2 ? "skip" : "error");
    return rc ? 1 : 0;
}
