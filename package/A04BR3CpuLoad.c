// SPDX-License-Identifier: MIT
// Optional, bounded board diagnostic using XNU's PROCESSOR_CPU_LOAD_INFO source.
#include <kern/processor.h>
#include <kern/spl.h>
#include <kern/clock.h>
#include <kern/misc_protos.h>
#include <mach/mach_time.h>
#include <mach/processor_info.h>
#include <stdint.h>

static processor_state_t a04br3_cpu_state(processor_t cpu)
{
    processor_set_t pset = cpu->processor_set;
    spl_t s = splsched();
    pset_lock(pset);
    processor_state_t state = cpu->state;
    pset_unlock(pset);
    splx((boolean_t)s);
    return state;
}

// Snapshot for the opt-in PMU context diagnostic. Processor registration is
// protected by processor_list_lock, as in the existing load reporter. CPU
// state and counters are not an atomic snapshot and are not instantaneous load.
int a04br3_cpu_load_snapshot(unsigned cpu_id, uint32_t out[6])
{
    if (!out) return 0;
    processor_t selected = (processor_t)0;
    simple_lock(&processor_list_lock, LCK_GRP_NULL);
    for (processor_t p = processor_list; p; p = p->processor_list) {
        if ((unsigned)p->cpu_id == cpu_id) { selected = p; break; }
    }
    simple_unlock(&processor_list_lock);
    if (!selected) return 0;
    natural_t ticks[CPU_STATE_MAX] = {0};
    processor_state_t before = a04br3_cpu_state(selected);
    processor_cpu_load_info(selected, ticks);
    processor_state_t after = a04br3_cpu_state(selected);
    out[0] = ticks[CPU_STATE_USER]; out[1] = ticks[CPU_STATE_SYSTEM];
    out[2] = ticks[CPU_STATE_IDLE]; out[3] = ticks[CPU_STATE_NICE];
    out[4] = (uint32_t)before; out[5] = (uint32_t)after;
    return 1;
}

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
        // Hold the scheduler lock only around each state read, not recount.
        processor_state_t state_before = a04br3_cpu_state(cpus[i]);
        processor_cpu_load_info(cpus[i], ticks);
        processor_state_t state_after = a04br3_cpu_state(cpus[i]);
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
