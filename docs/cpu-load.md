# a04br3 CPU load diagnostic

The default-off `a04br3-cpu-load=1` or `native-msdc-iostat=1` boot argument starts a separate kernel
thread. After each 10-second sleep it snapshots up to four registered CPUs,
then reports their cumulative user, system, idle and nice ticks and monotonic
timestamps. `extra` records any processors outside that bound; an empty or
incomplete list is not a four-core utilization measurement. This uses the
same `processor_cpu_load_info` counters as XNU's PROCESSOR_CPU_LOAD_INFO;
XNU's current implementation reports nice as zero.

Each row reports the raw processor state immediately before and after reading
the counters: 0 OFF_LINE, 1 SHUTDOWN, 2 START, 3 PENDING_OFFLINE, 4 IDLE,
5 DISPATCHING, 6 RUNNING. Each state read uses `splsched` and the processor
set scheduler lock, released before recount or logging. The two reads and
counters are not atomic. A difference proves that the sampled endpoints differ; equality does
not rule out intervening transitions. CPU hotplug can occur between reports
and leave matching endpoints, so these samples do not prove uninterrupted
online time. In particular IDLE is an online scheduler state, not OFF_LINE.

Use unsigned 32-bit subtraction between consecutive samples of the same CPU
and the same boot, including wrap, then calculate non-idle from the user,
system, nice and idle deltas. Sum deltas over the same set of CPUs for a
tick-weighted aggregate. Reject zero totals, missing cores, boot changes,
intervals long enough for multiple wraps, and samples where either state read
is OFF_LINE, SHUTDOWN, START, PENDING_OFFLINE or unknown. Accept transitions
among IDLE, DISPATCHING and RUNNING: those are ordinary online scheduler
states, so a changed value alone is not an offline event. Neither matching nor
different online endpoints rule out unobserved offline/hotplug and restart
between reads or between samples. The analyzer's finite 1000-tick/second
plausibility bound and single-wrap assumption also cannot prove that a reset
near wrap did not happen. The user/system accounting split is under
investigation; an undercounted user component can also understate non-idle. The per-CPU `at_ns` and batch start
and end are XNU monotonic time, not a simultaneous snapshot; sleep and
console output make the interval longer than 10 seconds. Recount, sequential
sampling and UART output add overhead. The values are scheduler accounting,
not CPU frequency or energy; timebase accuracy and actual runtime behavior
remain to be verified on the target. This does not change GPU composition.

Run `python3 scripts/tests/test_cpu_load.py` for the host model. This is not a
target ABI, link, boot or accounting validation. XNU source:
https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/osfmk/kern/processor.c
