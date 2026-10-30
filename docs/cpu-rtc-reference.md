# RTC reference prototype for generic timer

`A04BR3RtcReference.h` is a disconnected, single-step sampling prototype. It
has no production caller, timer/RTC adapter, boot argument, register access or
loop. It cannot currently measure hardware. The caller must provide a bounded,
serialized seconds read bracketed by monotonic timer reads, and collect points
with interrupts and preemption enabled. It returns outer timer-tick bounds for
the elapsed number of RTC seconds between two observed transitions, not Hz.

The vendor MT6392 RTC driver reads the seconds register without RTC reload.
Its WACS2 transport still issues command and valid-clear writes. The existing
board WACS2 helper has a local lock but does not exclude secure firmware,
other bus users, clock changes, stuck access or RTC time adjustment. Its loop
is iteration bounded, not a proved wall-clock timeout. Do not connect this
prototype to it or enable measurement until shared ownership, transactions,
watchdog scheduling and recovery have been independently established.

A later adapter must identify RTC 0x800a on the actual PMIC, prove that each
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

This candidate never overlaps the meter with PMU, and the existing board PMU
qualification remains unconditionally refused. A calibrated RTC candidate
alone does not establish EL3/debug ownership, PMU trap safety or cycle-counter
semantics. All hardware behavior, RTC accuracy and watchdog recovery remain
unverified; no images or hardware use are authorized by these host tests.
