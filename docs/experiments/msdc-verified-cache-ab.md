# Authenticated MSDC cache A/B protocol

This is a planned experiment, not a performance claim. The cache holds the last
fully authenticated chunk per immutable, signed read-only window. It does not
re-read a cache hit, so an external medium change or fault after authentication
is not detected until a miss. Confirm that no other agent can modify the root
medium before testing. Writable Data/overlay windows are excluded.

Use a single kernel built with the NativeMsdc short-alias change and this device
reporter, identical firmware, root, Data image, GPU/CPU settings and metrics in
both arms. Record commits, artifact hashes, boot arguments, timestamps and input
sequence. Baseline is `mcv=0`, treatment `mcv=1`; the long spelling takes
precedence if present, so omit it. Keep `native-msdc-iostat=1`,
`a04br3-home-perf=1` and `a04br3-proclog=10` in both arms. If available, keep
root timing enabled in both; record any boot-argument adjustments identically.

The P54 iostat4 boot argument string is 1018 ASCII bytes without its NUL.
Adding ` mcv=0` or ` mcv=1` alone gives 1024 bytes, exceeding the 1023-byte
text capacity of the ARM64 1024-byte field. For a candidate pair, remove the
initial `-v ` in both arms and add one alias: each is 1021 bytes before NUL.
This changes verbose boot display in both arms versus P54; check that the
resulting capture still contains all required UART records. Validate the actual
UTF-8 byte length and image's kernel command line after building, rather than
assuming this example applies to another image. Do not truncate arguments.

Capture independent complete boots with comparable starting state; repeat and
alternate arm order. Separate first boot and subsequent reboots using preloader
and boot reason. Save actual framebuffer pixels, input edges and display
completion timestamps for the same scripted Home/Settings actions. Compare
input-to-visible-response and boot milestones alongside GPU submit, scanout,
CPU process deltas, queue time, transfer counts and errors. Aggregate values do
not establish request ownership or input-to-display causality.

With iostat enabled, each complete sequence contains a cache sample bracketed
by the same begin/end timestamps as the other separately atomic counters. Hits
and misses count cache segments rather than requests; reused bytes are bytes
copied, including a partial copy. Cache-off zeros mean no cache observations,
not measured misses. Missing, unavailable, malformed, decreasing or mixed
sequence records invalidate a cache comparison. `scripts/summarize-msdc-io.py`
validates frames and emits interval deltas when cache rows are present. Pass
`--require-cache` for new A/B runs to reject a missing row in any frame. Older
logs without such rows remain readable without this option. Never infer a
zero from an absent row.

A candidate passes only if repeated comparable runs show fewer physical reads
and a meaningful, repeatable improvement in the predefined user-visible
latency, with no integrity failures, I/O errors, panic, reboot, or display/GPU
regression. A cache hit alone is not a UI result. If hits are scarce, or if
latency does not improve, reject this as a UI fix and investigate queue service
and rendering independently. Host fixtures model behavior and do not validate
real IOKit, DMA, medium coherence or power-loss behavior.
