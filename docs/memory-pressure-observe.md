# Optional one-shot memory-pressure sysctls

`tools/a04br3-memory-pressure-observe.c` is a standalone diagnostic, never
started by this repository's boot hooks. It requires an explicit `--sample`.
It issues only four `sysctlbyname` reads: `kern.memorystatus_level`,
`kern.memorystatus_available_pages`, `kern.memorystatus_available_pages_pressure`,
and `kern.memorystatus_available_pages_critical`. It makes no writes or policy
changes. The last two sysctls are conditional in XNU and can be missing; access,
returned length, uninitialized sentinel and clock failures are all reported
individually, with nonzero exit if any failed. Available pages are a kernel
memorystatus policy value, not necessarily instant reclaimable bytes. Do not
infer pressure or jetsam from a single number without proving config, thresholds
and update semantics on that target. Reads are sequential and non-atomic;
user CLOCK_MONOTONIC has not been synchronized to the kernel heartbeat clock.
Console output, sysctl collection and its scheduler work perturb measurements.

Host fixture: `python3 scripts/tests/test_memory_pressure_observe.py` checks
that only reads are passed to sysctl, success, missing name, bad length, and
explicit command gating. Target compilation, entitlements, sysctl presence,
trust, deployment, output path and runtime behavior remain unverified. No
signed tool or boot image is produced by this source change. A target build must
use the normal iPhoneOS SDK, signing and trust chain and an explicit launch
method; record its exact source, binary, boot and cache conditions. The older
memory bench described in `memory-copy-bench.md` provides conditional VM event
counters and process ranking but can perform artificial copies and has distinct
measurement overhead. Neither method proves complete UART delivery or records
all jetsam events; missing logs and unavailable sysctls are not zero.
