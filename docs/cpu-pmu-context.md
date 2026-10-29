# PMUv3 context diagnostic

The explicitly experimental `arw=3 aq=1` mode collects a context snapshot
before each PMU attempt and after successful cleanup, in addition to the
repeated active-window measurements. The board hook is called outside the PMU
lease, watchdog gate and hotplug lock; no context is read after quarantine.
The load snapshot uses XNU's existing processor cumulative ticks, not an
instantaneous load percentage. Per-CPU data and register reads are non-atomic;
use both brackets and the CNTVCT timestamps to assess timing and missing data.

Clock fields are raw INFRACFG mux/DCM and APMIXED ARMPLL CON0/CON1/PWR,
already used by the board's existing diagnostic. PLL PWR is not CPU rail
voltage. This diagnostic does not change PLL, PMU or power registers. Runtime
rail voltage and calibrated SoC temperature are not available through a
validated shared runtime interface and are explicitly recorded as unknown.
In particular it does not use the GPU startup-only WACS path. Higher EL/debug
PMU ownership, timer calibration, transient register changes between the two
snapshots and the effect of logging itself remain unproven. The readout cannot
by itself establish that a counter-ratio change reflects DVFS or thermal limits.
