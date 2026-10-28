/* Explicit, single-threaded synthetic copy measurement; never runs at boot. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static uint64_t now_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) != 0) { perror("clock_gettime"); exit(1); }
    return (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec;
}
int main(int argc, char **argv) {
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
    unsigned char *src = malloc(size), *dst = malloc(size);
    if (!src || !dst) { fprintf(stderr, "allocation failed\n"); free(src); free(dst); return 1; }
    /* Touch both buffers and use nonuniform data before timing. */
    for (size_t i = 0; i < size; ++i) src[i] = (unsigned char)((i * 17u + (i >> 12)) & 255u);
    memset(dst, 0, size);
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
