/* Explicit one-shot read-only sysctl observation; never changes memory policy. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#ifdef __APPLE__
#include <sys/sysctl.h>
#else
#error "This diagnostic requires Darwin sysctlbyname"
#endif
static uint64_t now_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC,&t)) return 0;
    return (uint64_t)t.tv_sec*UINT64_C(1000000000)+(uint64_t)t.tv_nsec;
}
static int read_one(const char *name) {
    uint32_t value=0;size_t length=sizeof(value);uint64_t begin=now_ns();
    if (!begin) {printf("a04br3 pressure name=%s status=clock_error\n",name);return 1;}
    errno=0;int rc=sysctlbyname(name,&value,&length,NULL,0);int saved=errno;uint64_t end=now_ns();
    if (!end || end<begin) {printf("a04br3 pressure name=%s status=clock_error\n",name);return 1;}
    if (rc) {printf("a04br3 pressure name=%s status=unavailable errno=%d begin_ns=%" PRIu64 " end_ns=%" PRIu64 "\n",name,saved,begin,end);return 1;}
    if (length!=sizeof(value) || value==UINT32_MAX) {printf("a04br3 pressure name=%s status=invalid length=%zu begin_ns=%" PRIu64 " end_ns=%" PRIu64 "\n",name,length,begin,end);return 1;}
    printf("a04br3 pressure name=%s status=ok value=%" PRIu32 " begin_ns=%" PRIu64 " end_ns=%" PRIu64 " non_atomic=1\n",name,value,begin,end);return 0;
}
int main(int argc,char **argv) {
    if (argc!=2 || strcmp(argv[1],"--sample")) {fprintf(stderr,"usage: %s --sample\n",argv[0]);return 2;}
    static const char *names[]={"kern.memorystatus_level","kern.memorystatus_available_pages","kern.memorystatus_available_pages_pressure","kern.memorystatus_available_pages_critical"};
    unsigned failures=0;for (unsigned i=0;i<sizeof(names)/sizeof(names[0]);i++) failures+=(unsigned)read_one(names[i]);
    printf("a04br3 pressure complete unavailable=%u clocks=user_monotonic cross_clock=unverified\n",failures);
    return failures?1:0;
}
