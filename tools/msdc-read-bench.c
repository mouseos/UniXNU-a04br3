/* Read-only repeated range comparison, wall time and process CPU time. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static uint64_t ns(clockid_t id) {
    struct timespec t;
    if (clock_gettime(id, &t)) { perror("clock_gettime"); exit(1); }
    return (uint64_t)t.tv_sec * 1000000000ULL + (uint64_t)t.tv_nsec;
}
static uint64_t number(const char *s) {
    char *end; errno = 0; unsigned long long n = strtoull(s, &end, 0);
    if (errno || !*s || *s == '-' || *end) { fprintf(stderr, "invalid number: %s\n", s); exit(2); }
    return n;
}
static int read_all(int fd, unsigned char *p, size_t len, off_t offset) {
    size_t at = 0;
    while (at < len) {
        ssize_t n = pread(fd, p + at, len - at, offset + (off_t)at);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { if (n < 0) perror("pread"); else fprintf(stderr, "short read\n"); return 0; }
        at += (size_t)n;
    }
    return 1;
}
int main(int argc, char **argv) {
    if (argc != 5) { fprintf(stderr, "usage: %s file offset bytes iterations\n", argv[0]); return 2; }
    uint64_t offset = number(argv[2]), length = number(argv[3]), iterations = number(argv[4]);
    if (!length || length > 64u*1024*1024 || !iterations || iterations > 1000000 ||
        offset > INT64_MAX || length > (uint64_t)INT64_MAX - offset || iterations > UINT64_MAX / length) {
        fprintf(stderr, "range, length or iteration count invalid\n"); return 2;
    }
    int fd = open(argv[1], O_RDONLY);
    if (fd < 0) { perror("open"); return 1; }
    unsigned char *reference = malloc((size_t)length), *actual = malloc((size_t)length);
    if (!reference || !actual) { fprintf(stderr, "allocation failed\n"); return 1; }
    if (!read_all(fd, reference, (size_t)length, (off_t)offset)) return 1;
    uint64_t wall = ns(CLOCK_MONOTONIC), cpu = ns(CLOCK_PROCESS_CPUTIME_ID);
    for (uint64_t i=0; i<iterations; i++) {
        if (!read_all(fd, actual, (size_t)length, (off_t)offset)) return 1;
        if (memcmp(reference, actual, (size_t)length)) { fprintf(stderr, "data mismatch at iteration %" PRIu64 "\n", i); return 1; }
    }
    cpu=ns(CLOCK_PROCESS_CPUTIME_ID)-cpu; wall=ns(CLOCK_MONOTONIC)-wall;
    printf("bytes=%" PRIu64 " iterations=%" PRIu64 " wall_ns=%" PRIu64 " process_cpu_ns=%" PRIu64 "\n", length*iterations, iterations, wall, cpu);
    free(actual); free(reference); close(fd); return 0;
}
