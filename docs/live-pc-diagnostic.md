# Opt-in launchd timer samples

For automatic backboardd diagnosis, build with the same timer hook and set
`a04br3-live-pc-backboard=1`. It takes precedence over both numeric options;
any other explicit value disables sampling. If `a04br3-proclog` is absent or
zero, this mode starts the reporting worker with a 10-second interval. The
worker searches `proc_best_name` for `backboardd` on each report and enables
sampling only when exactly one eligible match is seen. The process list scan
waits for process transitions and may block; the first selection may be delayed
until the next report. The target line records match
count, PID, unique ID and exec version. Duplicate matches disable collection.

The IRQ hook never scans or locks. A bounded sequence check rejects a target
snapshot if publication overlaps it. It checks the interrupted process against
the selected PID, unique ID and exec version, so PID reuse or an exec prevents
new samples until the worker observes the new identity. Each sample has its
own PID, unique ID and exec version: ring entries from the preceding target
can be drained after a selection change, and the report header alone must not
be used to attribute them. Image reports also reject a target whose identity
changed before lookup. A scan can miss a short-lived process or overlapping
transitions; sampling and process/image observations remain non-atomic.

Build with the companion UniXNU timer patch and `XNU_IRQ_PC_STATS=1`; the default
is zero. Set `a04br3-live-pc=1 a04br3-proclog=10` at boot to observe PID 1.
The separate runtime gate defaults to zero. Existing `native-irq-pid` / home
histogram controls are independent. An absent timer hook is reported as hook=0;
zero samples are not evidence of idle CPU. No binary or hardware behavior is
altered when the runtime gate is off beyond the compiled weak call if the
compile gate was explicitly enabled.

For another process, set `a04br3-live-pc-pid=<pid>` instead. The explicit PID
option takes precedence over `a04br3-live-pc`, including zero to disable; values
above INT32_MAX, non-decimal, malformed, or empty values disable sampling.
When the entire PID option is absent, the legacy flag retains its old behavior.
The first exact argument is authoritative; duplicates cannot override an invalid
first value. Tokens use the XNU space/tab separators and require `=` immediately
after the name. PID allocation varies between boots: verify the
target against this boot's process report and use each sample's unique ID and
exec version, not the PID alone, before attributing it. If the PID is absent or
belongs to a different process during the interval, the intended process was
not sampled. This option does not discover or follow a process by name.

The timer callback records interrupted PC and LR for user mode; for kernel
mode it records the interrupted kernel PC after VM_KERNEL_UNSLIDE and LR=0.
It records process unique ID, exec identity version, thread ID and mode. One
in 16 timer observations is retained in a fixed 64-slot ring for each of four
CPUs. The interrupt path has no shared lock, scan, retrying atomic RMW, or
allocation. `lost` counts samples overwritten before the report, and `torn`
counts slots changed during reading; neither is presented as a valid sample.
The counters and timestamps are non-atomic. Other CPU IDs set `bad_cpu`. Periodic sampling may alias and excludes time with interrupts masked.
It does not calculate CPU time. This is not a saved UPCB or a call stack.

Every report includes the XNU boot session UUID when available, pid,
epoch and monotonic report-start times; samples are not individually
timestamped and are not bounded by the report-start time. Each sample includes
its own uid and exec_version; `unavailable` is not a boot identifier. Follow the
UART boot boundary and do not join separate boots. Samples carry the process
identity version, which XNU increments on exec. Never attribute a sample to an
image row of another version, PID, unique ID or boot. Rows describe a bounded
later image-list observation, not an atomic mapping at every earlier interrupt.
Report start/end timestamps, status (`complete`, `partial`, `failed` or
`changed`), 128-entry truncation, detail completeness and Mach-O UUID /
__TEXT validity flags are explicit. `exec_version` and `dyld_version` are
distinct. A complete status means only that these bounded reads completed and
matched their later checks; it does not make the snapshot atomic. A malformed
or partially unreadable Mach-O reports incomplete, clears UUID/text validity
and values, and must be excluded from address attribution. Consumers must
require `detail_complete=1`, UUID and text validity, a matching fixed binary,
and independently establish that its mapping existed at the sampled time.
`cache_slide_valid=0` means unknown; the cache slide is not the executable or
dyld slide. For a valid individual __TEXT row the candidate image slide is
`load - text_vm`, after confirming the image UUID against the local binary.
The dyld all-image-infos record itself is user-controlled and may be stale,
changed during reading, absent early in boot, or incomplete. Even when the
header and entries compare equal, the result is not atomic and does not prove
that the mapping was present at the sample time; this is explicitly reported
as `mapping_at_sample=unknown`. A dyld load address is printed
separately when the ABI field is populated. Treat missing or invalid values as
unavailable; never invent a slide or combine with another boot's mapping.
The worker reads user VM and can fault or block. UART output of up to 129 image
rows and up to 256 PC rows each period adds substantial diagnostic overhead. Compare
against a non-diagnostic baseline; don't regard this as a performance fix.

Host check: `python3 scripts/tests/test_live_pc.py`. It tests bounded capture,
mode/PC/LR, overrun and concurrent ring reads, generation changes and partial
Mach-O reads with mocked memory;
it does not validate actual XNU ABI, IRQ safety, sampling bias, or hardware.
To also run the actual parser boundary test, set `XNU_BOOTARGS_SOURCE` to
`pexpert/gen/bootargs.c` from Apple XNU commit
`d8b80295118ef25ac3a784134bcf95cd8e88109f`; the test verifies its SHA-256
before compiling the relevant functions. Without that source the parser test
is explicitly skipped.
