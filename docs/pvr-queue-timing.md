# PowerVR queue and transfer timing

`--queue-timing` is an opt-in diagnostic for the Mesa revision pinned by this
repository. The default bundle does not enable it. A slow driver submit emits
at most four `QT` records per compiled architecture per process. `begin` and
`end` are `CLOCK_MONOTONIC` nanoseconds; durations are hexadecimal nanoseconds.
`p` and `t` are hexadecimal process and thread IDs. Verify the clock epoch
before correlating with kernel timestamps. `s1` is clearing prior syncs, `s2`
is processing submit waits, `s3` is command buffers, and `s4` is signals.
`k1` through `k5` give graphics, compute, transfer, query, and event
sub-command counts and inclusive elapsed times, respectively. Error markers
invalidate the corresponding timing. A result and phase identify an early exit.

`XT` measures a single transfer command in the same thread: `k1` is transfer
preparation and `k2` is the winsys submit, inclusive of any nested CPU waits.
It emits only if that transfer takes at least one second, with a separate
four-record limit per architecture and process. Timestamps identify overlapping
`QT` and `XT` intervals, not an automatic proof that clocks from other sources
share an epoch. Timings include preemption and diagnostic overhead. In
particular, synchronous `XT` logging happens inside the `QT` command interval;
compare nested sums with this overhead in mind. A missing `XT` may mean each
transfer took under one second, not that their aggregate is small. Diagnostic
limits, incomplete reporting after a crash, and interleaved thread output must
be considered. Neither log changes dependency semantics.
