# TAB-A04-BR3 synthetic memory copy measurement

This optional standalone user-space program is not installed, started or built
by the normal device build. It does not change graphics or rendering. Compile
it for the target using the toolchain and signing/entitlements appropriate to
the image; target compilation and execution have not yet been validated.
For a host-only check run `python3 scripts/tests/test_memory_copy_bench.py`.

Run explicitly as `a04br3-memory-copy-bench --run 64`. The argument is the
size of **each** of two buffers in MiB (1–64); 64 therefore allocates 128 MiB.
One thread initializes and touches both buffers, performs one timed copy,
checks the result outside the timed interval, and exits. Output contains the
monotonic timestamps, elapsed time, logical source bytes, and verification.
Effective copy MiB/s = logical_bytes * 1e9 / elapsed_ns / 1048576. Count
logical bytes once, not both read and write. This is a synthetic CPU copy
throughput, not a measurement of DRAM controller traffic or total system
bandwidth: caches, write allocation, scheduling, page placement, frequency,
other activity, and memcpy implementation affect the result. A large buffer
reduces but does not prove the absence of cache effects.

Record image/source IDs, workload, memory availability and timestamps; do not
run during the idle or real-workload observations. Memory pressure may make
128 MiB inappropriate; cancel the test if available memory is insufficient.
Use repeated separate runs with recovery gaps and report the distribution.
Compare against a pre-test and post-test baseline. The existing UART heartbeat
uses a monotonic sample timestamp; verify that target CLOCK_MONOTONIC and the
kernel timestamp share an epoch before joining them. The UART lines within one
heartbeat are sequential, not an atomic snapshot. To measure actual total
DRAM traffic during workloads, a validated MT8167 memory-controller counter
and its ownership, rollover, units and sampling cost are still needed.
