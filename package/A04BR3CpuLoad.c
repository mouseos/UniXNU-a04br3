// SPDX-License-Identifier: MIT
// Optional, bounded board diagnostic using XNU's PROCESSOR_CPU_LOAD_INFO source.
#include <kern/processor.h>
#include <kern/clock.h>
#include <mach/mach_time.h>
#include <mach/processor_info.h>
#include <sys/systm.h>
#include <stdint.h>

void a04br3_cpu_load_report(void)
{
    // The list can grow as CPUs are registered. Copy pointers under its lock;
    // never hold it over recount, timestamp conversion, or console output.
    processor_t cpus[4] = {0};
    unsigned count = 0, extra = 0;
    simple_lock(&processor_list_lock, LCK_GRP_NULL);
    for (processor_t p = processor_list; p; p = p->processor_list) {
        if (count < 4) cpus[count++] = p;
        else ++extra;
    }
    simple_unlock(&processor_list_lock);
    uint64_t begin = 0, end = 0;
    absolutetime_to_nanoseconds(mach_absolute_time(), &begin);
    for (unsigned i = 0; i < count; ++i) {
        natural_t ticks[CPU_STATE_MAX] = {0};
        // XNU processor_info(PROCESSOR_BASIC_INFO) likewise reads state
        // without the list lock. These are observations, not a transition log.
        processor_state_t state_before = cpus[i]->state;
        processor_cpu_load_info(cpus[i], ticks);
        processor_state_t state_after = cpus[i]->state;
        uint64_t at = 0;
        absolutetime_to_nanoseconds(mach_absolute_time(), &at);
        printf("a04br3 cpu-load cpu=%d at_ns=%llu user=%u system=%u idle=%u nice=%u state_before=%u state_after=%u ticks32=1 non_atomic=1\n",
            cpus[i]->cpu_id, (unsigned long long)at, ticks[CPU_STATE_USER], ticks[CPU_STATE_SYSTEM],
            ticks[CPU_STATE_IDLE], ticks[CPU_STATE_NICE], (unsigned)state_before, (unsigned)state_after);
    }
    absolutetime_to_nanoseconds(mach_absolute_time(), &end);
    printf("a04br3 cpu-load sample_begin_ns=%llu sample_end_ns=%llu cpus=%u extra=%u non_atomic=1\n",
        (unsigned long long)begin, (unsigned long long)end, count, extra);
}
