# RTC reference prototype for generic timer

`A04BR3RtcReference.h` computes conservative intervals. This revision adds
an experimental board adapter and worker in `A04BR3AutoReset.cpp`. The build
script defaults `XNU_A04BR3_RTC_REF=0`; set it to `1` explicitly to compile.
The `configs/a04br3_rtc_ref_defconfig` supplies that setting through the
UniXNU XNU configuration environment. At boot, `rtc-ref=1`, `a04br3-autoreset` >= 30 and recovery-ready are required;
any nonzero `cm`, `arw`, `armcpu-runtime-window`, `arc`,
`armcpu-runtime-cycle`, `cc` or `armcpu-cycle-clock` refuses the request.
It samples 64 times with scheduler sleeps of 50 ms and emits each raw CNTVCT
brackets, RTC seconds and status, plus conditional aggregate ticks. It does not
operate the CPU PMU or hardware frequency meter. The current PMU qualification
remains unconditionally refused; this is not yet direct CPU clock measurement.

The vendor MT6392 RTC driver reads the seconds register without RTC reload.
Each diagnostic transaction takes the existing board WACS2 nonblocking lock,
requires idle/initialized state without clearing a foreign valid response,
issues one read of 0x800a, waits up to 2048 polls for valid and clears only
its own response. If command completion or return to idle is uncertain after issue, it retains
the shared lock for the boot. A foreign initial state is left untouched and the
lock is released without issuing a command. Known AutoReset, Input
and GPU WACS2 command paths also retain that lock on uncertain completion;
AutoReset no longer clears an unknown response. AutoReset and Input accept a write after an observed non-idle phase followed by idle, or after a bounded run of idle polls (INIT_DONE, no pending response) when the write finished before the first poll, as observed on hardware for the fastboot-flag writes; a write that stays busy is quarantined. Neither is proof of hardware command acceptance; callers read the register back. That quarantine prevents
known local users from touching a possibly pending transaction, but may disable
touch, GPU power or recovery paths which use WACS2. The RTC worker reserves the existing board probe/watchdog gate over the entire
capture, blocking cooperating runtime probes while allowing normal watchdog
feeding. The worker releases its own capture state after returned transactions,
including rejected data and lost recovery readiness. A stuck worker may leave
capture exclusion active; periodic feeding continues, so watchdog recovery is
not established. After a returned operation the capture exclusion is cleared atomically, independent of gate contention. The periodic software-reset thread and external watchdog
agents are separate; actual reset/recovery is unverified. The poll bound is not a
wall-time guarantee and an MMIO trap or hang may never return. There is no
exclusive lease against secure firmware, EL2/EL3 or debug agents. An opt-in
is not proof of ownership, access safety, RTC source precision or completion.
The worker holds no WACS lock between successful samples and does not disable preemption; it retains the probe gate throughout.
If interrupted, timed out or otherwise incomplete, the aggregate valid flag is
zero. Even a valid aggregate is conditional on the assumptions below and is
never a certified clock result. Each completed row is logged before the next sample; the summary follows
only a non-panic exit. Missing summary means interrupted, not a valid result.
UART delivery and flush are not guaranteed, and panic can lose the current
row or any buffered data. Logging itself perturbs sampling and scheduling.

Before hardware use one must verify RTC 0x800a on the actual PMIC, prove that each
read's returned value was sampled within its timer bracket (including caching
and bridge latency), and independently prove that fewer than 60 RTC second updates occur between
every pair of actual RTC sampling instants. A wall-clock gap below 60 seconds
is insufficient near boundary phases. The seconds-only field aliases after a
full minute, and the state machine cannot detect a clock adjustment, skipped
full minute, stale data or RTC reset. It cannot prove a constant RTC period or the source's independence.
Those are prerequisites, not properties implied by a valid result. Polls should
be scheduler-driven with bounded count and deadline, never an extended busy or
preemption-disabled loop. Reject missing reads, a change by more than one
second, bad ordering, ambiguous bounds and incomplete transitions. Explicitly
record intervals and observed elapsed RTC ticks; do not label them a calibrated
13 MHz or CPU frequency unless an independent RTC rate bound exists. The
`clk32k` device-tree value is 32000 Hz, not a measured oscillator frequency.

The probe gate excludes only callers that use the same board protocol; it is not a lease against external agents. The requested boot arguments exclude known meter and PMU modes; they cannot
exclude an external owner or a separately started agent. The existing board
PMU qualification remains unconditionally refused. A calibrated RTC candidate
alone does not establish EL3/debug ownership, PMU trap safety or cycle-counter
semantics. All hardware behavior, RTC accuracy and watchdog recovery remain
unverified. Host tests are not evidence that the hardware path is safe.

For the fixed 22e49b19 UniXNU tree and its NativeMsdc 66d40654 gitlink,
`scripts/a04br3-rtc-ref-incremental.sh` checks both commits and the caller's
exact clean device commit, then sets the IRQ and overlap trace build flags.
Set `UNIXNU_ROOT` to the fixed UniXNU checkout and `RUNNER_TEMP` to the workflow seed directory. The script verifies the seed identity and required seed files and honors `UNIXNU_OUT`. These flags make
`native_msdc_irq_stats`, `native_msdc_irq_mask_failures`,
`native_msdc_worker_stats`, and `native_cs_trace_report` available to the
static kernel link; weak imports alone did not resolve in the observed link.
This is a diagnostic build configuration, not a hardware qualification.
